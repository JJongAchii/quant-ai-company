"""Guarded compatible cutover; preserve authority and pause until worker verification."""

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
REGISTRY = STATE / 'research/provisioned/data-evidence/registry.json'
SELECTED = ('api', 'dispatch', 'slack-socket', 'worker')
PROJECT = '9aac0de4-2b97-5195-a720-287d324234f3'
OLD_PROGRAM = 'e06537d3-fac3-5c8c-bf25-ddabb3c7e282'
OLD_DIGEST = '53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args, **kwargs):
    return subprocess.check_output(args, timeout=900, **kwargs)


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def sql(query):
    return json.loads(run(['docker', 'exec', 'quant-company-postgres-1', 'psql', '-U', 'postgres',
        '-d', 'quant_company', '-qAt', '-v', 'ON_ERROR_STOP=1', '-c', query]))


def atomic(path, raw):
    old = path.stat() if path.exists() else None
    temporary = path.with_name(path.name + '.conditional-tmp')
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


def activity():
    return sql("SELECT json_build_object('running_turns',(SELECT count(*) FROM turns WHERE status='running'),"
        "'active_jobs',(SELECT count(*) FROM research_jobs WHERE state IN ('queued','claimed','running','cancel_requested','uncertain')),"
        "'sending_outbox',(SELECT count(*) FROM outbox WHERE status='sending'),"
        "'active_model_calls',(SELECT count(*) FROM model_account_calls WHERE state IN ('dispatching','uncertain')),"
        "'digest',(SELECT manifest_digest FROM research_programs WHERE id=" + literal(OLD_PROGRAM) + "),"
        "'program_state',(SELECT state FROM research_programs WHERE id=" + literal(OLD_PROGRAM) + "),"
        "'revision',(SELECT revision FROM projects WHERE id=" + literal(PROJECT) + "),"
        "'missions',(SELECT count(*) FROM research_missions WHERE program_id=" + literal(OLD_PROGRAM) + "),"
        "'reservations',(SELECT count(*) FROM research_program_reservations WHERE program_id=" + literal(OLD_PROGRAM) + "),"
        "'origins',(SELECT json_agg(json_build_object('task_id',id,'task_digest',digest) ORDER BY id) "
        "FROM research_program_tasks WHERE program_id=" + literal(OLD_PROGRAM) + "))")


def compose(row):
    files = row['Config']['Labels']['com.docker.compose.project.config_files'].split(',')
    return ['docker', 'compose', '--project-name', 'quant-company', '--profile', '*', '--env-file', str(ENV),
        *[arg for path in files for arg in ('-f', path)]]


def swap_current(target):
    pointer = Path('/opt/quant-company/current.conditional-tmp')
    assert not pointer.exists() and not pointer.is_symlink()
    pointer.symlink_to(target)
    os.replace(pointer, '/opt/quant-company/current')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--attempt', type=int, required=True, choices=(3,))
    args = parser.parse_args()
    package = args.package.resolve()
    manifest = json.loads((package / 'activation-package.json').read_bytes())
    assert all(sha(package / name) == digest for name, digest in manifest['files'].items())
    baseline = json.loads((package / 'server-baseline.json').read_bytes())
    stage = json.loads((package / 'server-image-stage.json').read_bytes())
    worker = json.loads((package / 'worker-qualification-receipt.json').read_bytes())
    commit = stage['source_commit']
    assert commit == manifest['source_commit'] == worker['source_commit']
    assert stage['phase'] == 'qualified_images_staged_not_active'
    assert worker['qualified_for_frozen_input_transport'] and worker['actual_bubblewrap']
    assert not worker['performance_computed'] and not worker['sealed_price_rows_read']
    target = Path('/opt/quant-company/releases') / commit
    attempt = str(args.attempt).zfill(2)
    record_path = STATE / 'releases' / ('conditional-cutover-' + commit + '-' + attempt + '.json')
    override = STATE / 'config' / ('conditional-' + commit + '-' + attempt + '.compose.json')
    pause_reason = 'conditional_intake_' + commit[:12]
    images = {row['service']: row for row in stage['images']}
    program = json.loads((package / 'candidate-program.json').read_bytes())
    scope = program['envelopes'][0]['template']['scientific_lineage']
    expected_origins = sorted([{'task_id': row['task_id'], 'task_digest': row['task_digest']}
        for row in scope['originating_task_refs']], key=lambda row: row['task_id'])
    os.umask(0o077)
    with (STATE / '.backup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not record_path.exists() and not override.exists(), 'cutover_already_attempted'
        previous = Path('/opt/quant-company/current').resolve()
        assert str(previous) == baseline['app_release'] and target.is_dir()
        before = inspect()
        assert set(before) == set(baseline['containers']), 'container_set_changed'
        assert all(row['Id'] == baseline['containers'][name]['id'] for name, row in before.items())
        old_files = {name: (STATE / 'config' / name).read_bytes() for name in baseline['config_hashes']}
        assert all(hashlib.sha256(raw).hexdigest() == baseline['config_hashes'][name]
            for name, raw in old_files.items()), 'configuration_changed'
        old_registry = REGISTRY.read_bytes()
        assert sha(REGISTRY) == baseline['registry_sha256']
        for row in images.values():
            assert json.loads(run(['docker', 'image', 'inspect', row['image']]))[0]['Id'] == row['image_id']
        active = activity()
        assert active['digest'] == OLD_DIGEST and active['program_state'] in ('active', 'cancelled')
        assert active['revision'] == 5 and active['missions'] == 0 and active['reservations'] == 0
        assert active['origins'] == expected_origins, 'scientific_origin_history_changed'
        assert all(active[name] == 0 for name in ('running_turns', 'active_jobs', 'sending_outbox', 'active_model_calls'))
        pause = sql("WITH c AS (UPDATE runtime_control SET paused_until=now()+interval '30 minutes',reason="
            + literal(pause_reason) + " WHERE id=1 AND paused_until IS NULL RETURNING paused_until) "
            "SELECT json_build_object('until',(SELECT paused_until FROM c))")['until']
        assert pause, 'pause_owned_by_another_operation'
        record = {'schema_version': 1, 'source_commit': commit, 'phase': 'paused',
            'started_at': datetime.now(UTC).isoformat(), 'owner_authorization': 'INTENT-v8 conversation operational intake',
            'previous_app': str(previous), 'before': public(before), 'selected_services': list(SELECTED),
            'pause_until': pause, 'pause_reason': pause_reason, 'new_scientific_authority_granted': False,
            'registry_before_sha256': baseline['registry_sha256']}

        def save(**changes):
            record.update(changes)
            atomic(record_path, (json.dumps(record, sort_keys=True, indent=2) + '\n').encode())

        def restore_pause():
            sql("WITH c AS (UPDATE runtime_control SET paused_until=NULL,reason=NULL WHERE id=1 AND reason="
                + literal(pause_reason) + ' AND paused_until=' + literal(pause)
                + "::timestamptz RETURNING id) SELECT json_build_object('restored',(SELECT count(*) FROM c))")

        stopped, changed = [], []
        forward_only = False
        save()
        try:
            active = activity()
            assert all(active[name] == 0 for name in ('running_turns', 'active_jobs', 'sending_outbox', 'active_model_calls'))
            assert active['origins'] == expected_origins
            rollback = STATE / 'releases' / ('conditional-' + commit + '-rollback-' + attempt)
            rollback.mkdir(mode=0o700)
            for name, raw in old_files.items():
                (rollback / name).write_bytes(raw)
            (rollback / 'registry.json').write_bytes(old_registry)
            stopped = [row['Id'] for name, row in before.items() if name != 'quant-company-postgres-1'
                and (row['State']['Running'] or row['State'].get('Restarting'))]
            save(phase='consistent_backup', database_before=active)
            print(json.dumps({'phase': 'consistent_backup'}), flush=True)
            run(['docker', 'stop', '--time', '120', *stopped], stderr=subprocess.STDOUT)
            spec = importlib.util.spec_from_file_location('conditional_backup', previous / 'deploy/state_backup.py')
            backup = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(backup)
            backup.APP_SERVICES = ()
            for key, value in backup.config_values(STATE / 'config/backup.env').items():
                os.environ[key] = value
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                backup.backup(SimpleNamespace(env_file=ENV, s3_uri=None), backup.config_values(ENV),
                    compose(before['quant-company-api-1']), STATE)
            backup_receipt = json.loads(captured.getvalue().strip().splitlines()[-1])
            assert sha(Path(backup_receipt['backup'])) == backup_receipt['sha256']
            save(phase='backed_up', backup=backup_receipt)
            managed = REGISTRY.parent / 'conditional-20261001'
            if managed.exists():
                assert not managed.is_symlink() and managed.is_dir()
                assert {p.name for p in managed.iterdir()} == set(manifest['managed_files'])
                for name in manifest['managed_files']:
                    path = managed / name
                    assert not path.is_symlink() and path.is_file()
                    assert sha(path) == manifest['files']['managed/' + name], 'retained_managed_bytes_changed'
            else:
                managed.mkdir()
                for name in manifest['managed_files']:
                    path = managed / name
                    assert '/' not in name
                    path.write_bytes((package / 'managed' / name).read_bytes())
                    os.chown(path, 10001, 10001)
                    path.chmod(0o444)
            os.chown(managed, 10001, 10001)
            managed.chmod(0o555)
            profiles_path = STATE / 'config/research-profiles.json'
            profiles = json.loads(old_files['research-profiles.json'])
            new_profile = json.loads((package / 'server-profile.json').read_bytes())
            profile_id = new_profile['public_profile']['id']
            assert profile_id not in profiles and len(profiles) == 3
            profiles[profile_id] = new_profile
            atomic(profiles_path, (json.dumps(profiles, sort_keys=True, indent=2) + '\n').encode())
            registry = json.loads(old_registry)
            packet = json.loads((package / 'candidate-evidence-packet.json').read_bytes())
            assert len(registry['packets']) == 3 and not any(p['program_digest'] == packet['program_digest'] for p in registry['packets'])
            assert packet['program_digest'] == manifest['program_digest'] == worker['program_digest']
            registry['packets'].append(packet)
            atomic(REGISTRY, (json.dumps(registry, ensure_ascii=False, indent=2) + '\n').encode())
            updates = {}
            for row in before.values():
                service = row['Config']['Labels'].get('com.docker.compose.service')
                if not service or service == 'postgres':
                    continue
                environment = dict(pair.split('=', 1) for pair in row['Config']['Env'])
                image = row['Config']['Image']
                if service in SELECTED:
                    environment['COMPANY_CODE_COMMIT'] = commit
                    environment['PYTHONPATH'] = '/opt/quant-code/src'
                    image = images['worker' if service == 'worker' else 'api']['image']
                updates[service] = {'image': image, 'environment': environment}
            atomic(override, json.dumps({'services': updates}).encode())
            for service in SELECTED:
                row = before['quant-company-' + service + '-1']
                config = json.loads(run([*compose(row), '-f', str(override), 'config', '--format', 'json']))
                desired = config['services'][service]
                assert desired['environment']['COMPANY_CODE_COMMIT'] == commit
                assert desired['environment']['PYTHONPATH'] == '/opt/quant-code/src'
                expected = {(m['Source'], m['Destination'], m['RW']) for m in row['Mounts'] if m['Type'] == 'bind'}
                actual = {(m['source'], m['target'], not m.get('read_only', False))
                    for m in desired.get('volumes', []) if m['type'] == 'bind'}
                for secret in desired.get('secrets', []):
                    destination = secret.get('target', secret['source'])
                    if not destination.startswith('/'):
                        destination = '/run/secrets/' + destination
                    actual.add((config['secrets'][secret['source']]['file'], destination, False))
                assert expected == actual, 'service_mounts_would_change:' + service
            lines = [line for line in old_files['runtime.env'].decode().splitlines()
                if line.split('=', 1)[0] not in ('RELEASE_COMMIT', 'PINNED_COMPANY_WORKER_IMAGE')]
            atomic(ENV, ('\n'.join([*lines, 'RELEASE_COMMIT=' + commit,
                'PINNED_COMPANY_WORKER_IMAGE=' + images['worker']['image']]) + '\n').encode())
            run([*compose(before['quant-company-api-1']), '-f', str(override), 'run', '--rm', '--no-deps',
                '-T', '--entrypoint', 'python', 'api', '/app/entrypoint.py', 'quant-company', 'migrate'],
                stderr=subprocess.STDOUT)
            save(phase='activating', override_path=str(override), registry_after_sha256=sha(REGISTRY),
                profiles_after_sha256=sha(profiles_path))
            for service in SELECTED:
                changed.append(service)
                run([*compose(before['quant-company-' + service + '-1']), '-f', str(override),
                    'up', '-d', '--no-deps', '--no-build', '--pull', 'never', service], stderr=subprocess.STDOUT)
            # Preserve every independent service by restarting its captured container, never recreating it.
            unaffected = [row['Id'] for name, row in before.items() if row['Id'] in stopped
                and name not in {'quant-company-' + service + '-1' for service in SELECTED}]
            run(['docker', 'start', *reversed(unaffected)], stderr=subprocess.STDOUT)
            stopped = []
            swap_current(target)
            source_manifest = json.loads((package / 'source-manifest.json').read_bytes())
            import_probe = """import hashlib,importlib.util,json,pathlib
root=pathlib.Path(importlib.util.find_spec('quant_company').origin).parent
assert str(root)=='/opt/quant-code/src/quant_company'
files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
print(json.dumps({'files':files,'import_root':str(root)}))
"""
            imported = {}
            for service in SELECTED:
                installed = json.loads(run(['docker', 'exec', 'quant-company-' + service + '-1',
                    'python', '/app/entrypoint.py', 'python', '-B', '-c', import_probe]))
                assert installed.pop('files') == source_manifest['company_files'], 'actual_imported_source_mismatch:' + service
                imported[service] = installed
            save(actual_imported_sources=imported)
            deadline = time.monotonic() + 120
            while inspect()['quant-company-api-1']['State'].get('Health', {}).get('Status') != 'healthy':
                assert time.monotonic() < deadline, 'api_health_timeout'
                time.sleep(2)
            proof_script = (package / 'verify-installed.py').read_text()
            proof = json.loads(run(['docker', 'exec', 'quant-company-worker-1', 'python', '/app/entrypoint.py',
                'python', '-B', '-c', proof_script, (package / 'candidate-program.json').read_text()]))
            assert proof['history_digest'] == scope['history_digest']
            assert proof['company_commit'] == commit and proof['scientific_trials_added'] == 0
            after = inspect()
            assert all(after[name]['Id'] == row['Id'] for name, row in before.items()
                if name not in {'quant-company-' + service + '-1' for service in SELECTED})
            for service in SELECTED:
                row = after['quant-company-' + service + '-1']
                assert row['State']['Running'] and row['Image'] == images['worker' if service == 'worker' else 'api']['image_id']
            assert sha(STATE / 'config/roles.json') == baseline['config_hashes']['roles.json']
            assert sha(STATE / 'config/research-qlab.json') == baseline['config_hashes']['research-qlab.json']
            save(phase='active_awaiting_3070_verification', verification=proof, after=public(after),
                completed_at=datetime.now(UTC).isoformat(), scientific_trials_added=0)
            print(json.dumps(record), flush=True)
        except Exception as exc:
            # A persisted v3 program would make the old parser unsafe; keep the new parser for forward repair.
            forward_only = bool(sql("SELECT json_build_object('n',count(*)) FROM research_programs WHERE spec->>'schema_version'='2'")["n"])
            if not forward_only:
                for name, raw in old_files.items():
                    atomic(STATE / 'config' / name, raw)
                atomic(REGISTRY, old_registry)
                rollback_services = {row['Config']['Labels']['com.docker.compose.service']:
                    {'image': row['Config']['Image'], 'environment': dict(pair.split('=', 1) for pair in row['Config']['Env'])}
                    for row in before.values() if row['Config']['Labels'].get('com.docker.compose.service') not in (None, 'postgres')}
                if override.exists():
                    atomic(override, json.dumps({'services': rollback_services}).encode())
                    for service in reversed(changed):
                        run([*compose(before['quant-company-' + service + '-1']), '-f', str(override),
                            'up', '-d', '--no-deps', '--no-build', '--pull', 'never', service], stderr=subprocess.STDOUT)
                if Path('/opt/quant-company/current').resolve() != previous:
                    swap_current(previous)
                restore_pause()
            if stopped:
                surviving = {row['Id'] for row in inspect().values()}
                restart = [identity for identity in stopped if identity in surviving]
                if restart:
                    run(['docker', 'start', *reversed(restart)], stderr=subprocess.STDOUT)
            save(phase='forward_repair_required' if forward_only else 'rolled_back', error_type=type(exc).__name__,
                error=str(exc)[:160], after=public(inspect()))
            raise


if __name__ == '__main__':
    main()
