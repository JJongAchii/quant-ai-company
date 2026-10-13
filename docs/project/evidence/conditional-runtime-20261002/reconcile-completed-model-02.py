"""Validate a genuine completed receipt in isolation; reconcile its account ledger only."""

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

REQUEST = 'quant-feed-eed90a79-7864-4486-948b-66abc03686fd'
INPUT = '7d4c5226d23512c0fdf2f9843d29aa0910afa18544e9db05bc1011edef2485f0'
RECEIPT = '5b8c02b50018f1979072b22307d4ce45b190aa249b267a920459808850ad6f8d'
IMAGE = 'quant-company:ea092f419b4b679df4c25e479a8cb361b4d046ff'
IMAGE_ID = 'sha256:2110d56b77c35c89bfab28185f4924e4eec2db85878b4cab9ec92dbdaef73d79'


def sql(query):
    return json.loads(subprocess.check_output(['docker', 'exec', 'quant-company-postgres-1', 'psql',
        '-U', 'postgres', '-d', 'quant_company', '-qAt', '-v', 'ON_ERROR_STOP=1', '-c', query], timeout=30))


path = Path('/var/lib/quant-company/codex/jobs') / (REQUEST + '.json')
raw = path.read_bytes()
assert hashlib.sha256(raw).hexdigest() == RECEIPT
receipt = json.loads(raw)
assert receipt['version'] == 1 and receipt['state'] == 'complete' and receipt['request_id'] == REQUEST
assert receipt['input_digest'] == INPUT and receipt['account'] == {'profile': 'primary', 'revision': 2}
assert json.loads(subprocess.check_output(['docker', 'image', 'inspect', IMAGE]))[0]['Id'] == IMAGE_ID
saved = sql("SELECT row_to_json(q) FROM (SELECT request,state,response FROM quant_feed_calls WHERE id='" + REQUEST + "') q")
payload = json.dumps({'receipt': receipt, 'request': saved['request']}).encode()
probe = r'''
import json,sys
from quant_company.contracts import ProviderRequest,ProviderResponse
from quant_company.providers.codex_runner import request_digest
body=json.loads(sys.stdin.buffer.read());receipt=body['receipt']
request=ProviderRequest.model_validate(body['request']);response=ProviderResponse.model_validate(receipt['result'])
assert request_digest(request)==receipt['input_digest']
assert request.request_id==response.request_id==receipt['request_id']
assert response.provider=='codex' and receipt['state']=='complete' and receipt['version']==1
print(json.dumps({'request_and_response_types_verified':True,'input_digest':receipt['input_digest'],
 'provider':'codex','request_id':receipt['request_id']}))
'''
proof = json.loads(subprocess.check_output(['docker', 'run', '--rm', '-i', '--network', 'none', '--read-only',
    '--tmpfs', '/tmp', '--memory', '256m', '--cpus', '0.5', '--entrypoint', '/app/.venv/bin/python',
    IMAGE_ID, '-B', '-c', probe], input=payload, timeout=90))
assert proof['input_digest'] == INPUT
assert path.read_bytes() == raw
# Use the same policy row lock as AccountProvider.finish; preserve routing and the feed's pending outcome.
result = sql("BEGIN; DO $$ BEGIN PERFORM profile FROM model_account_policy WHERE id=1 FOR UPDATE; END $$; "
    "WITH changed AS (UPDATE model_account_calls SET state='completed',updated_at=now() WHERE request_id='" + REQUEST
    + "' AND input_digest='" + INPUT + "' AND profile='primary' AND revision=2 AND state='dispatching' RETURNING request_id) "
    "SELECT json_build_object('changed',(SELECT count(*) FROM changed)); COMMIT;")
assert result['changed'] in (0, 1)
after = sql("SELECT json_build_object('account',(SELECT row_to_json(m) FROM (SELECT state,input_digest,profile,revision "
    "FROM model_account_calls WHERE request_id='" + REQUEST + "') m),'quant',(SELECT row_to_json(q) FROM "
    "(SELECT state,response FROM quant_feed_calls WHERE id='" + REQUEST + "') q))")
assert after['account'] == {'state': 'completed', 'input_digest': INPUT, 'profile': 'primary', 'revision': 2}
assert after['quant'] == {'state': saved['state'], 'response': saved['response']}
assert path.read_bytes() == raw
print(json.dumps({'schema_version': 1, 'observed_at': datetime.now(UTC).isoformat(), 'request_id': REQUEST,
    'actual_codex_receipt': True, 'runtime_receipt_sha256': RECEIPT, 'account_call_state': 'completed',
    'account_rows_reconciled': result['changed'], 'exact_input_and_account_binding_verified': True,
    'quant_feed_state_preserved': True, 'quant_feed_commit_invoked': False, 'model_requests_replayed': 0,
    'isolated_networkless_validation': proof}))
