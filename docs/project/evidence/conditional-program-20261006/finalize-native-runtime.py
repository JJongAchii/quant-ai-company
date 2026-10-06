"""Finish the interrupted read-only source check without replacing services or replaying requests."""

import fcntl
import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

STATE = Path('/var/lib/quant-company')
SOURCE = '5c44ad07273adc780a56a47d7d35114fd0dc32c8'
base = Path(__file__).resolve().parent
journal = STATE / 'releases' / ('native-output-' + SOURCE + '-02.json')
manifest = json.loads((base / 'native-output-5c44ad0-02/native-source-manifest.json').read_bytes())
images = json.loads((base / 'native-output-5c44ad0-02/image-preparation.json').read_bytes())['images']
baseline = json.loads((base / 'server-baseline-pre-native.json').read_bytes())
roles = {'api': 'app', 'dispatch': 'app', 'slack-socket': 'app', 'worker': 'worker',
         'account-gateway': 'gateway', 'codex-runtime': 'codex'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
    result = subprocess.run(args, capture_output=True, timeout=90)
    assert result.returncode == 0, 'read_only_probe_failed_' + str(result.returncode)
    return result.stdout


probe = """import hashlib,importlib.util,json,os,pathlib,sys
root=pathlib.Path(importlib.util.find_spec('quant_company').origin).parent
actual={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
 for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
expected=json.loads(sys.argv[1])
assert actual==expected['company_files']
assert os.environ['COMPANY_CODE_COMMIT']==expected['source_commit']
assert str(root)=='/opt/quant-code/src/quant_company'
print(json.dumps({'import_root':str(root),'files':len(actual),'source_origin_and_tree_verified':True,
 'probe_imports':'standard_library_only','additional_model_process_started':False}))
"""
with (STATE / '.backup.lock').open('a') as lock:
    deadline = time.monotonic() + 30
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            assert time.monotonic() < deadline, 'shared_lock_wait_timeout'
            time.sleep(1)
    record = json.loads(journal.read_bytes())
    assert record['phase'] == 'forward_repair_required' and set(record['selected_services_attempted']) == set(roles)
    assert sha(Path(record['backup']['backup'])) == record['backup']['sha256']
    assert Path('/opt/quant-company/current').resolve().name == SOURCE
    imported, after, lifecycle = {}, {}, {}
    for name, old in record['before'].items():
        row = json.loads(run(['docker', 'inspect', name]))[0]
        service = row['Config']['Labels'].get('com.docker.compose.service')
        assert row['State']['Running']
        if service in roles:
            assert row['Image'] == images[roles[service]]['image_id']
            imported[service] = json.loads(run(['docker', 'exec', name, 'python', '-B', '-c', probe, json.dumps(manifest)]))
            if service in ('api', 'account-gateway', 'codex-runtime'):
                assert row['State']['Health']['Status'] == 'healthy'
        else:
            assert row['Id'] == old['id'] and row['Image'] == old['image_id']
        after[name] = {'id': row['Id'], 'image': row['Config']['Image'], 'image_id': row['Image'],
            'state': row['State']['Status'], 'health': row['State'].get('Health', {}).get('Status')}
        lifecycle[name] = {'restart_count': row['RestartCount'], 'oom_killed_current_state': row['State']['OOMKilled'],
                          'memory_limit_bytes': row['HostConfig']['Memory']}
    current = json.loads(run(['python3', str(base / 'read-native-baseline.py')]))
    for key in ('program', 'data_stage', 'task_sha256', 'scientific_origins',
                'scientific_trial_count', 'lineage_trial_limit', 'account_policy', 'model_calls'):
        assert current['database'][key] == baseline['database'][key], 'authority_changed:' + key
    assert current['database']['runtime_pause'] == {'paused_until': record['pause_until'], 'reason': record['pause_reason']}
    assert current['ambiguous_receipts'] == baseline['ambiguous_receipts']
    assert current['runtime_jobs']['codex/jobs']['active'] == record['orphan_running_receipts_preserved']
    assert current['managed_file_hashes'] == baseline['managed_file_hashes']
    assert current['registry_sha256'] == baseline['registry_sha256']
    for name, digest in baseline['config_hashes'].items():
        if name != 'runtime.env':
            assert current['config_hashes'][name] == digest
    record.update(phase='active_held_for_data_review_reconciliation', after=after,
        actual_imported_sources=imported, authority_and_receipts_preserved=True,
        prior_configuration_files_and_mounts_preserved=True,
        completed_at=datetime.now(UTC).isoformat(), operator_probe_repair={
            'previous_exit_code': 137, 'previous_probe_started_no_model': True,
            'repair': 'Use stdlib-only source origin and exact tree checks inside live containers; native schema remains qualified in the exact prepared images.',
            'services_replaced_by_repair': False, 'memory_limits_changed': False,
            'cause_claimed': 'not_proven', 'container_lifecycle': lifecycle,
            'operator_helper_sha256': sha(Path(__file__).resolve())})
    temporary = journal.with_name(journal.name + '.probe-tmp')
    assert not temporary.exists()
    temporary.write_text(json.dumps(record, sort_keys=True, indent=2) + '\n')
    temporary.chmod(journal.stat().st_mode & 0o777)
    temporary.replace(journal)
print(json.dumps(record))
