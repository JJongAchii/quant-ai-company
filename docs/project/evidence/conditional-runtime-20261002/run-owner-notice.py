"""Use the qualified backend to queue the previously authorized, stable owner review notice."""

import json
import subprocess
from pathlib import Path

base = Path('/home/ubuntu/conditional-runtime-231d6ba')
journal = Path('/var/lib/quant-company/releases/conditional-cutover-231d6ba0755f658f167636a8ea2c3ef65b6af562-03.json')
assert json.loads(journal.read_bytes())['phase'] == 'active_verified'
result = subprocess.check_output(['docker', 'exec', 'quant-company-worker-1', 'python', '/app/entrypoint.py',
    'python', '-B', '-c', (base / 'post-cancellation-review.py').read_text(),
    (base / 'activation-package-03/candidate-program.json').read_text()], timeout=60)
print(result.decode().strip())
