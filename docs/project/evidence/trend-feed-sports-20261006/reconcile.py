"""Inspect the known first-stage mount restoration, then complete only the unchanged dispatch stage."""

import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path('/opt/quant-company/operator-releases/trend-feed-sports-20261006')
BASE_ROOT = ROOT.parent / 'trend-feed-20261006'
STATE = Path('/var/lib/quant-company')
JOURNAL = STATE / 'releases/trend-feed-sports-20261006-activation.json'
RECONCILIATION = STATE / 'releases/trend-feed-sports-20261006-reconciliation.json'


def signature_sha(operator, row):
    return hashlib.sha256(json.dumps(operator.signature(row), sort_keys=True).encode()).hexdigest()


def main(operator):
    if RECONCILIATION.exists():
        print(json.dumps({'state': json.loads(RECONCILIATION.read_text())['state'], 'automatic_retry': False}))
        return
    record = json.loads(JOURNAL.read_text())
    candidate = json.loads((STATE / 'releases/trend-feed-sports-20261006-image.json').read_text())
    preview = json.loads((STATE / 'releases/trend-feed-sports-20261006-preview.json').read_text())
    if record['state'] != 'reconciliation_required' or record.get('error') != 'RuntimeError:sports_policy_runtime_drift':
        raise RuntimeError('unexpected_reconciliation_state')
    before = operator.inspect()
    current = before['news-worker']
    signature = operator.signature(current)
    naver_mount = ('bind', str(STATE / 'secrets/trend-naver-credentials.json'), '/run/secrets/trend_naver_credentials', False)
    if current['Image'] != candidate['image'] or not current['State']['Running'] or naver_mount not in signature['mounts']:
        raise RuntimeError('first_stage_not_verified')
    signature['mounts'].remove(naver_mount)
    if hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest() != candidate['target_signature_sha256']['news-worker']:
        raise RuntimeError('unexplained_first_stage_change')
    dispatch = before['dispatch']
    if (dispatch['Id'] != candidate['target_ids']['dispatch'] or dispatch['Image'] != candidate['base_image']
            or signature_sha(operator, dispatch) != candidate['target_signature_sha256']['dispatch']):
        raise RuntimeError('dispatch_changed_requires_review')
    if operator.lanes_busy(before) or operator.sql("SELECT count(*) FROM outbox WHERE status='sending'"):
        print(json.dumps({'state': 'waiting_for_quiet_reconciliation', 'external_effects_started': False}))
        return
    reconciliation = {'state': 'completing_dispatch', 'started_at': datetime.datetime.now(datetime.UTC).isoformat(),
        'first_stage_inspected': True, 'first_stage_repeated': False,
        'only_first_stage_configuration_difference': 'previously authorized read-only NAVER secret mount restored'}
    operator.atomic(RECONCILIATION, (json.dumps(reconciliation, indent=2) + '\n').encode())
    operator.atomic(STATE / 'releases/trend-feed-sports-reconciliation-before-containers.json', json.dumps(before).encode())
    frozen = operator.frozen_requests()
    oldpause = operator.sql('SELECT row_to_json(s) FROM (SELECT paused_until,reason FROM runtime_control WHERE id=1)s')
    try:
        operator.sql("WITH s AS (UPDATE runtime_control SET paused_until=now()+interval '5 minutes',"
                     "reason='deployment_trend_feed_sports_reconcile' WHERE id=1 RETURNING id) SELECT json_agg(id) FROM s")
        path = STATE / 'config/trend-feed-20261006-dispatch.compose.json'
        content = json.loads(path.read_text())
        operator.atomic(STATE / 'releases/trend-feed-sports-dispatch.before.json', path.read_bytes())
        content['services']['dispatch']['image'] = candidate['image_tag']
        content['services']['dispatch']['environment'] = dict(e.split('=', 1) for e in dispatch['Config']['Env'])
        operator.atomic(path, json.dumps(content).encode())
        operator.run([*operator.compose(dispatch, path), 'up', '-d', '--no-deps', '--no-build', '--pull', 'never',
                      '--force-recreate', '--wait', '--wait-timeout', '120', 'dispatch'], timeout=180)
        fresh = operator.inspect()
        if (fresh['dispatch']['Image'] != candidate['image'] or not fresh['dispatch']['State']['Running']
                or fresh['dispatch']['State']['OOMKilled'] or operator.signature(fresh['dispatch']) != operator.signature(dispatch)
                or any(fresh[n]['Id'] != row['Id'] for n, row in before.items() if n != 'dispatch')):
            raise RuntimeError('reconciliation_runtime_drift')
        after = operator.frozen_requests()
        if any(after.get(table, {}).get(key) != digest for table, rows in frozen.items() for key, digest in rows.items()):
            raise RuntimeError('existing_request_changed')
        active_preview = json.loads(operator.run([*operator.compose(fresh['news-worker'], STATE / 'config/trend-feed-20261006-news-worker.compose.json'),
                                                 'run', '--rm', '--no-deps', '--pull', 'never', '-T', 'news-worker', 'python', '-'],
                                                input=(ROOT / 'preview.py').read_text(), timeout=90))
        if active_preview['text_sha256'] != preview['text_sha256'] or active_preview['source_sha256'] != candidate['source_sha256']:
            raise RuntimeError('active_filtered_preview_changed')
        record.update(state='publication_enabled', completed_at=datetime.datetime.now(datetime.UTC).isoformat(), publish_enabled=True,
            target_ids={n: fresh[n]['Id'] for n in operator.TARGETS}, target_images={n: fresh[n]['Image'] for n in operator.TARGETS},
            target_pids={n: fresh[n]['HostConfig']['PidsLimit'] for n in operator.TARGETS},
            runtime_config_preserved_except_restored_naver_mount=True, previously_authorized_naver_mount_restored=True,
            unrelated_container_ids_preserved_during_reconciliation=True, existing_frozen_requests_preserved=True,
            existing_delivered_body_preserved=True, active_preview_sha256=active_preview['text_sha256'],
            active_source_sha256=active_preview['source_sha256'], first_stage_failure_reconciled=True)
        record['first_stage_error'] = record.pop('error')
        cutover = json.loads(operator.JOURNAL.read_text())
        cutover.update(target_ids=record['target_ids'], sports_policy_gate=str(JOURNAL), editorial_policy_version=2)
        operator.save(cutover)
        operator.atomic(STATE / 'releases/trend-feed-sports-20261006-active-preview.json',
                        (json.dumps(active_preview, indent=2, ensure_ascii=False) + '\n').encode())
        reconciliation.update(state='completed', completed_at=record['completed_at'], dispatch_only_replaced=True,
                              unrelated_container_ids_preserved=True, existing_requests_preserved=True)
    except BaseException as exc:
        reconciliation.update(state='reconciliation_required', error=type(exc).__name__ + ':' + str(exc))
        raise
    finally:
        operator.sql('WITH s AS (UPDATE runtime_control SET paused_until='
                     + ("'" + oldpause['paused_until'] + "'::timestamptz" if oldpause['paused_until'] else 'NULL')
                     + ',reason=' + ("'" + oldpause['reason'].replace("'", "''") + "'" if oldpause['reason'] else 'NULL')
                     + ' WHERE id=1 RETURNING id) SELECT json_agg(id) FROM s')
        operator.atomic(RECONCILIATION, (json.dumps(reconciliation, indent=2) + '\n').encode())
    record['runtime_pause_restored'] = operator.sql('SELECT row_to_json(s) FROM (SELECT paused_until,reason FROM runtime_control WHERE id=1)s') == oldpause
    operator.atomic(JOURNAL, (json.dumps(record, indent=2) + '\n').encode())
    print(json.dumps({'activation': record, 'reconciliation': reconciliation}))


if __name__ == '__main__':
    os.umask(0o077)
    spec = importlib.util.spec_from_file_location('trend_operator', BASE_ROOT / 'production-cutover.py')
    operator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(operator)
    with (STATE / '.backup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        main(operator)
