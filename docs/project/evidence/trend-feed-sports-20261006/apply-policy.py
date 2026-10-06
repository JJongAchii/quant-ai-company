"""Apply the qualified sports policy to two active consumers and retain delivery receipts."""

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
APPROVAL = 'chat-user-exclude-sports-20261006'


def main(operator):
    if JOURNAL.exists():
        print(json.dumps({'state': json.loads(JOURNAL.read_text())['state'], 'automatic_retry': False}))
        return
    candidate = json.loads((STATE / 'releases/trend-feed-sports-20261006-image.json').read_text())
    preview = json.loads((STATE / 'releases/trend-feed-sports-20261006-preview.json').read_text())
    if (not preview['valid'] or preview['sports_selected'] or preview['editorial_policy_version'] != 2
            or not candidate['base_config_preserved'] or preview['source_sha256'] != candidate['source_sha256']
            or preview['text_sha256'] != candidate['preview_sha256']
            or candidate['changed_files'] != ['trend_feed/editor.py', 'trend_feed/store.py']):
        raise RuntimeError('qualified_sports_policy_missing')
    before = operator.inspect()
    if (operator.lanes_busy(before) or operator.sql("SELECT count(*) FROM outbox WHERE status='sending'")
            or operator.run(['systemctl', 'show', 'quant-company-release.service', '-p', 'ActiveState', '--value']).strip() != 'inactive'):
        print(json.dumps({'state': 'waiting_for_quiet_cutover', 'external_effects_started': False}))
        return
    for name in operator.TARGETS:
        signature = hashlib.sha256(json.dumps(operator.signature(before[name]), sort_keys=True).encode()).hexdigest()
        if (before[name]['Id'] != candidate['target_ids'][name] or before[name]['Image'] != candidate['base_image']
                or signature != candidate['target_signature_sha256'][name]):
            raise RuntimeError('target_changed_requires_review')
        flags = dict(e.split('=', 1) for e in before[name]['Config']['Env'])
        if flags.get('TREND_FEED_PUBLISH_ENABLED') != 'true' or flags.get('TREND_FEED_ENABLED') != 'true':
            raise RuntimeError('regular_delivery_changed')
    frozen = operator.frozen_requests()
    env_sha = hashlib.sha256(operator.ENV.read_bytes()).hexdigest()
    oldpause = operator.sql('SELECT row_to_json(s) FROM (SELECT paused_until,reason FROM runtime_control WHERE id=1)s')
    record = {'state': 'applying', 'approval': APPROVAL, 'started_at': datetime.datetime.now(datetime.UTC).isoformat(),
              'commit': candidate['commit'], 'image': candidate['image'], 'base_image': candidate['base_image'],
              'editorial_policy_version': 2, 'preview_sha256': preview['text_sha256'],
              'changed_files': candidate['changed_files'], 'model_calls_made': 0, 'naver_calls_made': 0,
              'slack_calls_made': 0, 'automatic_message_replay': False}

    def save():
        operator.atomic(JOURNAL, (json.dumps(record, indent=2) + '\n').encode())

    save()
    try:
        operator.sql("WITH s AS (UPDATE runtime_control SET paused_until=now()+interval '5 minutes',"
                     "reason='deployment_trend_feed_sports_policy' WHERE id=1 RETURNING id) SELECT json_agg(id) FROM s")
        for name in operator.TARGETS:
            path = STATE / 'config' / ('trend-feed-20261006-' + name + '.compose.json')
            content = json.loads(path.read_text())
            operator.atomic(STATE / 'releases' / ('trend-feed-sports-' + name + '.before.json'), path.read_bytes())
            content['services'][name]['image'] = candidate['image_tag']
            operator.atomic(path, json.dumps(content).encode())
            operator.run([*operator.compose(before[name], path), 'up', '-d', '--no-deps', '--no-build', '--pull', 'never',
                          '--force-recreate', '--wait', '--wait-timeout', '120', name], timeout=180)
            fresh = operator.inspect()[name]
            if (fresh['Image'] != candidate['image'] or not fresh['State']['Running'] or fresh['State']['OOMKilled']
                    or operator.signature(fresh) != operator.signature(before[name])):
                raise RuntimeError('sports_policy_runtime_drift')
        fresh = operator.inspect()
        if any(fresh[n]['Id'] != row['Id'] for n, row in before.items() if n not in operator.TARGETS):
            raise RuntimeError('unrelated_container_replaced')
        after_frozen = operator.frozen_requests()
        if any(after_frozen.get(table, {}).get(key) != digest for table, rows in frozen.items() for key, digest in rows.items()):
            raise RuntimeError('existing_frozen_request_changed')
        if hashlib.sha256(operator.ENV.read_bytes()).hexdigest() != env_sha:
            raise RuntimeError('runtime_env_changed')
        active_preview = json.loads(operator.run([*operator.compose(fresh['news-worker'], STATE / 'config/trend-feed-20261006-news-worker.compose.json'),
                                                 'run', '--rm', '--no-deps', '--pull', 'never', '-T', 'news-worker', 'python', '-'],
                                                input=(ROOT / 'preview.py').read_text(), timeout=90))
        if active_preview['text_sha256'] != preview['text_sha256'] or active_preview['source_sha256'] != candidate['source_sha256']:
            raise RuntimeError('active_filtered_preview_drift')
        record.update(state='publication_enabled', completed_at=datetime.datetime.now(datetime.UTC).isoformat(),
                      publish_enabled=True, target_ids={n: fresh[n]['Id'] for n in operator.TARGETS},
                      target_images={n: fresh[n]['Image'] for n in operator.TARGETS},
                      target_pids={n: fresh[n]['HostConfig']['PidsLimit'] for n in operator.TARGETS},
                      runtime_config_preserved=True, unrelated_container_ids_preserved=True,
                      existing_frozen_requests_preserved=True, existing_delivered_body_preserved=True,
                      active_preview_sha256=active_preview['text_sha256'], active_source_sha256=active_preview['source_sha256'])
        cutover = json.loads(operator.JOURNAL.read_text())
        cutover.update(target_ids=record['target_ids'], sports_policy_gate=str(JOURNAL), editorial_policy_version=2)
        operator.save(cutover)
        operator.atomic(STATE / 'releases/trend-feed-sports-20261006-active-preview.json',
                        (json.dumps(active_preview, indent=2, ensure_ascii=False) + '\n').encode())
    except BaseException as exc:
        record.update(state='reconciliation_required', error=type(exc).__name__ + ':' + str(exc))
        save()
        raise
    finally:
        operator.sql('WITH s AS (UPDATE runtime_control SET paused_until='
                     + ("'" + oldpause['paused_until'] + "'::timestamptz" if oldpause['paused_until'] else 'NULL')
                     + ',reason=' + ("'" + oldpause['reason'].replace("'", "''") + "'" if oldpause['reason'] else 'NULL')
                     + ' WHERE id=1 RETURNING id) SELECT json_agg(id) FROM s')
    record['runtime_pause_restored'] = operator.sql('SELECT row_to_json(s) FROM (SELECT paused_until,reason FROM runtime_control WHERE id=1)s') == oldpause
    if not record['runtime_pause_restored']:
        record['state'] = 'reconciliation_required'
    save()
    print(json.dumps(record))


if __name__ == '__main__':
    os.umask(0o077)
    spec = importlib.util.spec_from_file_location('trend_operator', BASE_ROOT / 'production-cutover.py')
    operator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(operator)
    with (STATE / '.backup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        main(operator)
