"""Finish one account ledger from an exact completed official receipt, without invoking a model or applying its decision."""

import hashlib
import json
import sys
from datetime import UTC, datetime

from quant_company.accounts import AccountProvider
from quant_company.company import Company
from quant_company.config import Settings
from quant_company.contracts import ProviderRequest, ProviderResponse
from quant_company.providers.codex_runner import request_digest

raw = sys.argv[3].encode()
expected = '45c37e4307802b69b9161721cee9810aefdac28a7876e536c6ea2ef42ea0e212'
assert hashlib.sha256(raw).hexdigest() == expected
receipt = json.loads(raw)
request_id = '3e6de9bd-5c18-5003-8f5f-047ff949a58f'
assert receipt['version'] == 1 and receipt['state'] == 'complete'
assert receipt['request_id'] == request_id
assert receipt['input_digest'] == 'e97fc79cbc6173f5a76aa2f52d4c4f09cfdb42d6c880f446c87f39eb346bc3a9'
assert receipt['account'] == {'profile': 'primary', 'revision': 2}
response = ProviderResponse.model_validate(receipt['result'])
assert response.provider == 'codex' and response.request_id == request_id
company = Company(Settings())
assert company.settings.company_code_commit == '231d6ba0755f658f167636a8ea2c3ef65b6af562'
with company.db.transaction() as conn:
    turn = conn.execute('SELECT id,status,request FROM turns WHERE id=%s', (request_id,)).fetchone()
    call = conn.execute('SELECT * FROM model_account_calls WHERE request_id=%s', (request_id,)).fetchone()
    assert turn and call and call['state'] in {'dispatching', 'completed'}
    request = ProviderRequest.model_validate(turn['request'])
    assert request_digest(request) == receipt['input_digest'] == call['input_digest']
    assert request.request_id == request_id and call['profile'] == 'primary' and call['revision'] == 2
AccountProvider(company, None).finish(request, receipt['account'])
with company.db.transaction() as conn:
    after = conn.execute('SELECT state FROM model_account_calls WHERE request_id=%s', (request_id,)).fetchone()
    saved = conn.execute('SELECT status,request FROM turns WHERE id=%s', (request_id,)).fetchone()
    assert after['state'] == 'completed' and saved['request'] == turn['request'] and saved['status'] == turn['status']
print(json.dumps({'schema_version': 1, 'observed_at': datetime.now(UTC).isoformat(),
    'request_id': request_id, 'actual_official_codex_receipt': True, 'receipt_sha256': expected,
    'request_and_response_binding_verified': True, 'account_call_state': 'completed',
    'previous_account_call_state': call['state'], 'turn_state_and_request_preserved': True,
    'model_requests_replayed': 0, 'agent_decision_applied': False, 'scientific_trials_started': 0}))
