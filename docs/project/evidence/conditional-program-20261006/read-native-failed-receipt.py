"""Read one definitely failed native model receipt; never read model or Slack credentials."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

request_id = '86e01053-793c-5784-b944-dea0ba1d5349'
raw = (Path('/var/lib/quant-company/codex/jobs') / (request_id + '.json')).read_bytes()
value = json.loads(raw)
assert value['request_id'] == request_id and value['state'] == 'failed'
receipt = {key: value.get(key) for key in ('request_id', 'state', 'input_digest', 'cli_version',
                                         'account', 'requested_execution', 'fault', 'error')}
receipt['receipt_sha256'] = hashlib.sha256(raw).hexdigest()
result = {'schema_version': 1, 'observed_at': datetime.now(UTC).isoformat(),
          'receipt': receipt, 'credentials_read': False, 'model_requests_replayed': 0}
target = Path(__file__).resolve().parent / 'native-failed-model-receipt.json'
with target.open('x') as stream:
    stream.write(json.dumps(result, indent=2) + '\n')
print(json.dumps(result))
