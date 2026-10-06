"""Activate compatible private data-output code with exact gates and a consistent backup."""

import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

STATE = Path('/var/lib/quant-company')
ENV = STATE / 'config/runtime.env'
SOURCE = '5c44ad07273adc780a56a47d7d35114fd0dc32c8'
SELECTED = {'api': 'app', 'dispatch': 'app', 'slack-socket': 'app', 'worker': 'worker',
            'account-gateway': 'gateway', 'codex-runtime': 'codex'}


def run(args, **kwargs):
    return subprocess.check_output(args, timeout=900, **kwargs)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sql(query):
    return json.loads(run(['docker', 'exec', 'quant-company-postgres-1', 'psql', '-U', 'postgres',
        '-d', 'quant_company', '-qAt', '-v', 'ON_ERROR_STOP=1', '-c', query]))


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def atomic(path, raw):
    old = path.stat() if path.exists() else None
    temporary = path.with_name(path.name + '.native-tmp')
    assert not temporary.exists(), 'partial_atomic_write_retained'
    temporary.write_bytes(raw)
    temporary.chmod(old.st_mode & 0o777 if old else 0o600)
    if old:
        os.chown(temporary, old.st_uid, old.st_gid)
    os.replace(temporary, path)


def inspect():
    names = run(['docker', 'ps', '-a', '--filter', 'label=com.docker.compose.project=quant-company',
        '--format', '{{.Names}}'], text=True).splitlines()
    return {row['Name'].lstrip('/'): row for row in json.loads(run(['docker', 'inspect', *names]))}


def public(rows):
    return {name: {'id': row['Id'], 'image': row['Config']['Image'], 'image_id': row['Image'],
        'state': row['State']['Status'], 'health': row['State'].get('Health', {}).get('Status')}
        for name, row in rows.items()}


def compose(row):
    files = row['Config']['Labels']['com.docker.compose.project.config_files'].split(',')
    return ['docker', 'compose', '--project-name', 'quant-company', '--profile', '*', '--env-file', str(ENV),
            *[argument for path in files for argument in ('-f', path)]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--wait-seconds', type=int, default=0)
    args = parser.parse_args()
    assert 0 <= args.wait_seconds <= 600
    base = Path(__file__).resolve().parent
    candidate = base / 'native-output-5c44ad0-02'
    manifest = json.loads((candidate / 'native-source-manifest.json').read_bytes())
    images = json.loads((candidate / 'image-preparation.json').read_bytes())['images']
    baseline = json.loads((base / 'server-baseline-pre-native.json').read_bytes())
    ci = json.loads((base / 'source-ci.json').read_bytes())
    worker = json.loads((base / 'native-worker-qualification.json').read_bytes())
    assert SOURCE == manifest['source_commit'] == ci['source_commit'] == worker['source_commit']
    assert ci['conclusion'] == 'success' and ci['run_id'] == 37393064952
    assert worker['qualified_for_frozen_input_transport'] and worker['actual_bubblewrap']
    assert not worker['performance_computed'] and worker['scientific_trials_added'] == 0
    assert manifest['dependencies_entrypoint_and_qdata_pin_preserved']
    assert manifest['numeric_inputs_engine_and_profile_preserved']
    before = inspect()
    assert set(before) == set(baseline['containers'])
    assert all(row['Id'] == baseline['containers'][name]['id'] and
        row['Image'] == baseline['containers'][name]['image_id'] and row['State']['Running']
        for name, row in before.items()), 'container_baseline_changed'
    previous = Path('/opt/quant-company/current').resolve()
    assert str(previous) == baseline['app_release']
    old_env = ENV.read_bytes()
    assert all(sha(STATE / 'config' / name) == digest for name, digest in baseline['config_hashes'].items())
    registry = STATE / 'research/provisioned/data-evidence/registry.json'
    assert sha(registry) == baseline['registry_sha256']
    managed = registry.parent / 'conditional-20261001'
    assert all(sha(managed / name) == digest for name, digest in baseline['managed_file_hashes'].items())
    for role, image in images.items():
        assert json.loads(run(['docker', 'image', 'inspect', image['tag']]))[0]['Id'] == image['image_id']
        assert image['import_qualification']['native_schema_verified']
        expected_name = 'quant-company-' + {'app': 'api', 'worker': 'worker',
            'gateway': 'account-gateway', 'codex': 'codex-runtime'}[role] + '-1'
        assert before[expected_name]['Id'] == image['base_container_id']
    read_baseline = base / 'read-native-baseline.py'

    def snapshot():
        return json.loads(run(['python3', str(read_baseline)]))

    def authority_unchanged(current):
        for key in ('program', 'data_stage', 'task_sha256', 'scientific_origins',
                    'scientific_trial_count', 'lineage_trial_limit', 'account_policy'):
            assert current['database'][key] == baseline['database'][key], 'authority_or_call_changed:' + key
        ambiguous = [call for call in current['database']['model_calls'] if call['state'] == 'uncertain']
        assert ambiguous == baseline['database']['model_calls'], 'ambiguous_account_call_changed'
        assert current['ambiguous_receipts'] == baseline['ambiguous_receipts']
        assert all(current['database'][key] == 0 for key in ('running_turns', 'active_jobs', 'sending_outbox'))
        assert current['managed_file_hashes'] == baseline['managed_file_hashes']
        assert current['registry_sha256'] == baseline['registry_sha256']

    authority_unchanged(snapshot())
    program = baseline['database']['program']
    assert program['state'] == 'active' and program['manifest_digest'] == worker['program_digest']
    assert program['approval_event_id'].startswith('slack:')
    assert baseline['database']['data_stage']['hold']['failure_count'] == 4
    assert baseline['database']['runtime_pause'] == {'paused_until': None, 'reason': None}
    if not args.apply:
        print(json.dumps({'phase': 'qualified_preview', 'source_commit': SOURCE,
            'selected_services': list(SELECTED), 'ci_passed': True, 'worker_transport_qualified': True,
            'production_mutated': False, 'employee_judgment_applied': False}))
        return
    record_path = STATE / 'releases' / ('native-output-' + SOURCE + '-01.json')
    override = STATE / 'config' / ('native-output-' + SOURCE + '-01.compose.json')
    reason = 'native_data_output_' + SOURCE[:12]
    with contextlib.ExitStack() as stack:
        backup_lock = stack.enter_context((STATE / '.backup.lock').open('a'))
        fcntl.flock(backup_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not record_path.exists() and not override.exists(), 'activation_already_attempted'
        lane_locks = ['.runtime.lock', '.runtime-news.lock', '.runtime-quant.lock', '.runtime-brief.lock']
        deadline = time.monotonic() + args.wait_seconds
        while True:
            pending = contextlib.ExitStack()
            try:
                for name in lane_locks:
                    handle = pending.enter_context((STATE / 'codex/jobs' / name).open('r+'))
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                stack.enter_context(pending.pop_all())
                break
            except BlockingIOError:
                pending.close()
                assert time.monotonic() < deadline, 'active_model_lane_wait_timeout_no_activation'
                print(json.dumps({'phase': 'waiting_for_active_model_lane', 'production_mutated': False}), flush=True)
                time.sleep(5)
        frozen = snapshot()
        authority_unchanged(frozen)
        assert all(call['state'] == 'uncertain' for call in frozen['database']['model_calls']), 'account_call_dispatching'
        pause = sql("WITH c AS (UPDATE runtime_control SET paused_until=now()+interval '45 minutes',reason="
            + literal(reason) + " WHERE id=1 AND paused_until IS NULL RETURNING paused_until) "
            "SELECT json_build_object('until',(SELECT paused_until FROM c))")['until']
        assert pause, 'pause_owned_by_another_operation'
        record = {'schema_version': 1, 'phase': 'paused', 'source_commit': SOURCE,
            'started_at': datetime.now(UTC).isoformat(), 'pause_reason': reason, 'pause_until': pause,
            'selected_services': list(SELECTED), 'before': public(before), 'previous_app': str(previous),
            'gates': {'ci': ci, 'worker_transport_qualified': True, 'runtime_lane_locks_held': lane_locks},
            'uncertain_requests_preserved': baseline['ambiguous_receipts'],
            'orphan_running_receipts_preserved': frozen['runtime_jobs']['codex/jobs']['active'],
            'owner_program_digest_preserved': program['manifest_digest'],
            'employee_judgment_applied': False, 'scientific_trials_added': 0, 'model_calls_replayed': 0}

        def save(**changes):
            record.update(changes)
            atomic(record_path, (json.dumps(record, sort_keys=True, indent=2) + '\n').encode())
            print(json.dumps({'phase': record['phase'], 'source_commit': SOURCE}), flush=True)

        stopped = []
        changed = []
        try:
            save()
            authority_unchanged(snapshot())
            rollback = STATE / 'releases' / ('native-output-' + SOURCE + '-rollback-01')
            rollback.mkdir(mode=0o700)
            (rollback / 'runtime.env').write_bytes(old_env)
            stopped = [row['Id'] for name, row in before.items() if name != 'quant-company-postgres-1']
            run(['docker', 'stop', '--time', '120', *stopped], stderr=subprocess.STDOUT)
            authority_unchanged(snapshot())
            save(phase='consistent_backup')
            spec = importlib.util.spec_from_file_location('native_backup', previous / 'deploy/state_backup.py')
            backup = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(backup)
            backup.APP_SERVICES = ()
            for key, value in backup.config_values(STATE / 'config/backup.env').items():
                os.environ[key] = value
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                backup.backup(SimpleNamespace(env_file=ENV, s3_uri=None), backup.config_values(ENV),
                    compose(before['quant-company-api-1']), STATE)
            proof = json.loads(captured.getvalue().strip().splitlines()[-1])
            assert sha(Path(proof['backup'])) == proof['sha256']
            save(phase='backed_up', backup=proof)
            updates = {}
            for row in before.values():
                service = row['Config']['Labels'].get('com.docker.compose.service')
                if not service or service == 'postgres':
                    continue
                env = dict(item.split('=', 1) for item in row['Config']['Env'])
                image = row['Config']['Image']
                if service in SELECTED:
                    env['COMPANY_CODE_COMMIT'] = SOURCE
                    env['PYTHONPATH'] = '/opt/quant-code/src'
                    image = images[SELECTED[service]]['tag']
                updates[service] = {'image': image, 'environment': env}
            atomic(override, json.dumps({'services': updates}).encode())
            configuration = json.loads(run([*compose(before['quant-company-api-1']), '-f', str(override),
                'config', '--format', 'json']))
            for service, role in SELECTED.items():
                row = before['quant-company-' + service + '-1']
                desired = configuration['services'][service]
                assert desired['image'] == images[role]['tag']
                assert desired['environment']['COMPANY_CODE_COMMIT'] == SOURCE
                expected = {(m['Source'], m['Destination'], m['RW']) for m in row['Mounts'] if m['Type'] == 'bind'}
                actual = {(m['source'], m['target'], not m.get('read_only', False))
                    for m in desired.get('volumes', []) if m['type'] == 'bind'}
                for secret in desired.get('secrets', []):
                    target = secret.get('target', secret['source'])
                    if not target.startswith('/'):
                        target = '/run/secrets/' + target
                    actual.add((configuration['secrets'][secret['source']]['file'], target, False))
                assert expected == actual, 'selected_mounts_would_change:' + service
            lines = [line for line in old_env.decode().splitlines() if line.split('=', 1)[0]
                not in ('RELEASE_COMMIT', 'PINNED_COMPANY_WORKER_IMAGE')]
            atomic(ENV, ('\n'.join([*lines, 'RELEASE_COMMIT=' + SOURCE,
                'PINNED_COMPANY_WORKER_IMAGE=' + images['worker']['tag']]) + '\n').encode())
            save(phase='activating', override_path=str(override))
            for service in ('codex-runtime', 'account-gateway', 'api', 'dispatch', 'slack-socket', 'worker'):
                changed.append(service)
                run([*compose(before['quant-company-' + service + '-1']), '-f', str(override),
                    'up', '-d', '--no-deps', '--no-build', '--pull', 'never', service], stderr=subprocess.STDOUT)
            unaffected = [row['Id'] for name, row in before.items() if row['Id'] in stopped
                and name not in {'quant-company-' + service + '-1' for service in SELECTED}]
            run(['docker', 'start', *reversed(unaffected)], stderr=subprocess.STDOUT)
            pointer = Path('/opt/quant-company/current.native-tmp')
            assert not pointer.exists() and not pointer.is_symlink()
            pointer.symlink_to(Path('/opt/quant-company/releases') / SOURCE)
            os.replace(pointer, '/opt/quant-company/current')
            probe = """import hashlib,json,os,pathlib,quant_company,sys
from quant_company.contracts import ProviderRequest
from quant_company.providers.codex_runner import output_schema
root=pathlib.Path(quant_company.__file__).resolve().parent
files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
 for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
assert files==json.loads(sys.argv[1])['company_files']
assert os.environ['COMPANY_CODE_COMMIT']==sys.argv[2]
schema=output_schema(ProviderRequest(request_id='11111111-1111-4111-8111-111111111111',
 model='gpt-5.6-terra',prompt='Import qualification',output_contract='research_stage_v1'))
assert set(schema['required'])=={'action','read_path','read_offset','artifact_json'}
print(json.dumps({'import_root':str(root),'files':len(files),'native_output_contract_verified':True}))
"""
            imported = {}
            for service in SELECTED:
                imported[service] = json.loads(run(['docker', 'exec', 'quant-company-' + service + '-1',
                    'python', '-B', '-c', probe, json.dumps(manifest), SOURCE]))
            deadline = time.monotonic() + 120
            while True:
                after = inspect()
                if all(after['quant-company-' + service + '-1']['State'].get('Health', {}).get('Status') == 'healthy'
                       for service in ('api', 'account-gateway', 'codex-runtime')):
                    break
                assert time.monotonic() < deadline, 'selected_health_timeout'
                time.sleep(2)
            for name, row in before.items():
                service = row['Config']['Labels'].get('com.docker.compose.service')
                if service not in SELECTED:
                    assert after[name]['Id'] == row['Id'] and after[name]['Image'] == row['Image']
                else:
                    assert after[name]['State']['Running'] and after[name]['Image'] == images[SELECTED[service]]['image_id']
            current = snapshot()
            authority_unchanged(current)
            assert current['runtime_jobs']['codex/jobs']['active'] == frozen['runtime_jobs']['codex/jobs']['active']
            assert all(sha(STATE / 'config' / name) == digest for name, digest in baseline['config_hashes'].items()
                       if name != 'runtime.env')
            save(phase='active_held_for_data_review_reconciliation', after=public(after),
                 actual_imported_sources=imported, authority_and_receipts_preserved=True,
                 completed_at=datetime.now(UTC).isoformat())
            print(json.dumps(record), flush=True)
        except Exception as error:
            surviving = {row['Id'] for row in inspect().values()}
            restart = [identity for identity in stopped if identity in surviving]
            if restart:
                run(['docker', 'start', *reversed(restart)], stderr=subprocess.STDOUT)
            save(phase='forward_repair_required' if changed else 'held_before_activation',
                 error_type=type(error).__name__, selected_services_attempted=changed, after=public(inspect()))
            # Preserve the maintenance pause and the independent data-stage hold.
            raise


if __name__ == '__main__':
    os.umask(0o077)
    main()
