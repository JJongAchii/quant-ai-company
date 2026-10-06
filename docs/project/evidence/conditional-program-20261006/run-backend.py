"""Execute a reviewed operator helper using the running API's isolated credential loader."""

import json
import subprocess
import sys
from pathlib import Path

base = Path(__file__).resolve().parent
action = sys.argv[1]
assert action in {'prepare', 'publish', 'status', 'reconcile', 'diagnostics', 'hold_data', 'format_guidance'}
container = json.loads(subprocess.check_output(['docker', 'inspect', 'quant-company-api-1']))[0]
assert container['Id'] == '29f630ba7faa0a6c1cbc96676407ffce2688a0e4d583c658a665e21fbdc5ccda'
assert container['Image'] == 'sha256:7f305b5f8b235d065aa2b4d45106436f31355ec4b66fd78445453148472767ff'
assert container['State']['Running']
scripts = {'prepare': 'prepare-program.py', 'publish': 'publish-program.py',
           'status': 'read-program-status.py', 'reconcile': 'reconcile-completed-turn.py',
           'diagnostics': 'read-stage-diagnostics.py', 'hold_data': 'hold-data-stage.py',
           'format_guidance': 'repair-data-format-guidance.py'}
if action == 'prepare':
    spec = Path('/home/ubuntu/conditional-runtime-231d6ba/activation-package-03/candidate-program.json').read_text()
else:
    spec = (base / 'candidate-program.json').read_text()
source = (base / 'source-manifest.json').read_text()
extra = []
receipt_raw = None
if action == 'reconcile':
    receipt_path = Path('/var/lib/quant-company/codex/jobs/3e6de9bd-5c18-5003-8f5f-047ff949a58f.json')
    receipt_raw = receipt_path.read_bytes()
    extra = [receipt_raw.decode()]
failed_receipts = {}
if action == 'hold_data':
    for identity in ['dff80fd7-40c6-5ee2-96a8-1ac8e3913c38', 'da444092-a806-5047-bc21-65411f92fe1c',
                     '40ba5eb0-f9aa-5b0d-9c21-6de04989d4b1', '578a8874-b2f8-5762-b872-37aec557dc76']:
        path = Path('/var/lib/quant-company/codex/jobs') / f'{identity}.json'
        failed_receipts[path] = path.read_bytes()
    extra = [json.dumps([raw.decode() for raw in failed_receipts.values()])]
result = subprocess.run(['docker', 'exec', 'quant-company-api-1', 'python', '/app/entrypoint.py',
    'python', '-B', '-c', (base / scripts[action]).read_text(), spec, source, *extra],
    timeout=90, capture_output=True)
if result.returncode:
    print(result.stderr.decode()[-3000:], file=sys.stderr)
    raise SystemExit(result.returncode)
if receipt_raw is not None:
    assert receipt_path.read_bytes() == receipt_raw, 'original_completed_receipt_changed'
assert all(path.read_bytes() == raw for path, raw in failed_receipts.items()), 'original_failure_receipt_changed'
print(result.stdout.decode().strip())
