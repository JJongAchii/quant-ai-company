"""Probe lane ownership without invoking or cancelling any model request."""

import fcntl
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

root = Path('/var/lib/quant-company/codex/jobs')
locks = {}
descriptors = []
try:
    for name in ('.runtime.lock', '.runtime-news.lock', '.runtime-quant.lock', '.runtime-brief.lock'):
        path = root / name
        if not path.exists():
            locks[name] = {'exists': False, 'owned': False}
            continue
        fd = os.open(path, os.O_RDWR)
        descriptors.append(fd)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locks[name] = {'exists': True, 'owned': False}
        except BlockingIOError:
            locks[name] = {'exists': True, 'owned': True}
    receipts = []
    for path in root.glob('*.json'):
        value = json.loads(path.read_bytes())
        if value.get('state') == 'running':
            receipts.append({key: value.get(key) for key in ('request_id', 'input_digest', 'state',
                'started_at', 'cli_version', 'account', 'execution_lane')})
            receipts[-1]['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
finally:
    for fd in descriptors:
        os.close(fd)
print(json.dumps({'schema_version': 1, 'observed_at': datetime.now(UTC).isoformat(),
    'locks': locks, 'running_receipts': receipts, 'model_requests_invoked_or_cancelled': 0,
    'receipt_files_modified': False}))
