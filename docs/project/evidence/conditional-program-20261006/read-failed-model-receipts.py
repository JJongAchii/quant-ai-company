"""Read service-owned failure labels from three known model receipts; never read authentication files."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

requests = ['dff80fd7-40c6-5ee2-96a8-1ac8e3913c38', 'da444092-a806-5047-bc21-65411f92fe1c',
            '40ba5eb0-f9aa-5b0d-9c21-6de04989d4b1', '578a8874-b2f8-5762-b872-37aec557dc76']
records = []
for request in requests:
    raw = (Path('/var/lib/quant-company/codex/jobs') / f'{request}.json').read_bytes()
    value = json.loads(raw)
    assert value['request_id'] == request
    records.append({'request_id': request, 'receipt_sha256': hashlib.sha256(raw).hexdigest(),
                    'state': value['state'], 'input_digest': value['input_digest'],
                    'cli_version': value.get('cli_version'), 'account': value.get('account'),
                    'requested_execution': value.get('requested_execution'),
                    'fault': value.get('fault'), 'error': value.get('error')})
print(json.dumps({'schema_version': 1, 'observed_at': datetime.now(UTC).isoformat(),
                  'failed_receipts': records, 'model_calls_replayed': 0, 'credentials_read': False}))
