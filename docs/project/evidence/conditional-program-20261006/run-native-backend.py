"""Use the verified company worker's trusted operator loader; never expose credentials to a model."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

base = Path(__file__).resolve().parent
action = sys.argv[1]
assert action in {'verify', 'status', 'resume', 'schema-feedback', 'continue-data'}
source = '5c44ad07273adc780a56a47d7d35114fd0dc32c8'
journal = Path('/var/lib/quant-company/releases') / ('native-output-' + source + '-02.json')
activation = json.loads(journal.read_bytes())
assert activation['phase'] in {'active_held_for_data_review_reconciliation', 'active_data_review_resumed'}
name = 'quant-company-worker-1'
container = json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]
expected = activation['after'][name]
assert container['Id'] == expected['id'] and container['Image'] == expected['image_id'] and container['State']['Running']
scripts = {'verify': 'verify-native-installed.py', 'status': 'read-native-program-status.py',
           'resume': 'resume-native-data-stage.py', 'schema-feedback': 'repair-scoped-data-guidance.py',
           'continue-data': 'continue-native-data-attempt.py'}
args = [(base / 'candidate-program.json').read_text()]
saved_receipts = {}
if action == 'verify':
    args.append(json.dumps(json.loads((base / 'program-preparation.json').read_bytes())['history']))
elif action == 'resume':
    assert activation['phase'] == 'active_held_for_data_review_reconciliation'
    verification = json.loads((base / 'native-installed-verification.json').read_bytes())
    registration = json.loads((base / 'worker-registration.json').read_bytes())
    hold = json.loads((base / 'data-stage-hold.json').read_bytes())
    for prior in hold['failure_receipts']:
        path = Path('/var/lib/quant-company/codex/jobs') / (prior['request_id'] + '.json')
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == prior['receipt_sha256']
        saved_receipts[path] = raw
    args += [json.dumps(activation), json.dumps(verification), json.dumps(registration),
             json.dumps([raw.decode() for raw in saved_receipts.values()])]
elif action == 'continue-data':
    failure = json.loads((base / 'native-failed-model-receipt.json').read_bytes())
    path = Path('/var/lib/quant-company/codex/jobs') / (failure['receipt']['request_id'] + '.json')
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == failure['receipt']['receipt_sha256']
    saved_receipts[path] = raw
    args.append(json.dumps(failure))
result = subprocess.run(['docker', 'exec', name, 'python', '/app/entrypoint.py',
    'python', '-B', '-c', (base / scripts[action]).read_text(), *args], capture_output=True, timeout=90)
if result.returncode:
    print(result.stderr.decode()[-2500:], file=sys.stderr)
    raise SystemExit(result.returncode)
assert all(path.read_bytes() == raw for path, raw in saved_receipts.items()), 'original_failure_receipt_changed'
receipt = json.loads(result.stdout)
if action == 'verify':
    target = base / 'native-installed-verification.json'
    assert not target.exists(), 'verification_already_recorded'
    target.write_text(json.dumps(receipt, indent=2) + '\n')
elif action == 'resume':
    target = base / 'native-data-resume.json'
    assert not target.exists(), 'resume_receipt_already_recorded'
    target.write_text(json.dumps(receipt, indent=2) + '\n')
    activation.update(phase='active_data_review_resumed', data_review_resume=receipt)
    journal.write_text(json.dumps(activation, sort_keys=True, indent=2) + '\n')
elif action == 'schema-feedback':
    target = base / 'artifact-schema-guidance.json'
    with target.open('x') as stream:
        stream.write(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
elif action == 'continue-data':
    with (base / 'native-data-continuation.json').open('x') as stream:
        stream.write(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
if action == 'status' and len(sys.argv) == 3:
    snapshot_name = sys.argv[2]
    assert snapshot_name.startswith('staff-state-native-') and snapshot_name.endswith('.json')
    assert '/' not in snapshot_name and '..' not in snapshot_name
    target = base / snapshot_name
    with target.open('x') as stream:
        stream.write(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'observed_at': receipt['observed_at'], 'snapshot': str(target),
        'actual_stages': receipt['actual_stages'], 'last_progress': receipt['stage_progress'][-5:],
        'actual_tasks': [{'id': task['id'], 'state': task['state'],
                         'data_decision': (task['data_assessment'] or {}).get('decision'),
                         'selection_decision': (task['decision'] or {}).get('decision'), 'mission_id': task['mission_id']}
                         for task in receipt['actual_tasks']], 'activity': receipt['activity'],
        'read_count': len(receipt['stage_reads']), 'last_reads': receipt['stage_reads'][-3:],
        'followup_feedback': receipt['followup_feedback'],
        'worker_authenticated_polling': receipt['worker_authenticated_polling']}, ensure_ascii=False))
else:
    print(json.dumps(receipt, ensure_ascii=False))
