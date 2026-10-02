"""Recover account completion from a genuine receipt; never run a model or publish a feed."""

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

request_id = 'quant-feed-eed90a79-7864-4486-948b-66abc03686fd'
path = Path('/var/lib/quant-company/codex/jobs') / (request_id + '.json')
raw = path.read_bytes()
receipt = json.loads(raw)
assert receipt['version'] == 1 and receipt['state'] == 'complete'
assert receipt['request_id'] == request_id
assert receipt['input_digest'] == '7d4c5226d23512c0fdf2f9843d29aa0910afa18544e9db05bc1011edef2485f0'
assert receipt['account'] == {'profile': 'primary', 'revision': 2}
code = r'''
import hashlib,json,sys
from quant_company.accounts import AccountProvider
from quant_company.company import Company
from quant_company.config import Settings
from quant_company.contracts import ProviderRequest,ProviderResponse
from quant_company.providers.codex_runner import request_digest
raw=sys.stdin.buffer.read();receipt=json.loads(raw)
assert hashlib.sha256(raw).hexdigest()==sys.argv[1]
company=Company(Settings())
assert company.settings.company_code_commit=='ea092f419b4b679df4c25e479a8cb361b4d046ff'
assert receipt['version']==1 and receipt['state']=='complete'
response=ProviderResponse.model_validate(receipt['result'])
assert response.request_id==receipt['request_id'] and response.provider=='codex'
with company.db.transaction() as conn:
 call=conn.execute('SELECT * FROM model_account_calls WHERE request_id=%s',(receipt['request_id'],)).fetchone()
 saved=conn.execute('SELECT request,state,response FROM quant_feed_calls WHERE id=%s',(receipt['request_id'],)).fetchone()
 assert call and saved
 request=ProviderRequest.model_validate(saved['request'])
 assert request_digest(request)==receipt['input_digest']==call['input_digest']
 assert receipt['account']=={'profile':call['profile'],'revision':call['revision']}
 assert call['state'] in {'dispatching','completed'}
 original_quant_state=saved['state'];original_response=saved['response']
AccountProvider(company,None).finish(request,receipt['account'])
with company.db.transaction() as conn:
 call=conn.execute('SELECT state,profile,revision FROM model_account_calls WHERE request_id=%s',(receipt['request_id'],)).fetchone()
 saved=conn.execute('SELECT state,response FROM quant_feed_calls WHERE id=%s',(receipt['request_id'],)).fetchone()
 assert call['state']=='completed'
 assert saved['state']==original_quant_state and saved['response']==original_response
print(json.dumps({'request_id':receipt['request_id'],'account_call_state':call['state'],
 'quant_feed_state_preserved':True,'quant_feed_commit_invoked':False,'model_requests_replayed':0,
 'runtime_receipt_sha256':hashlib.sha256(raw).hexdigest(),'exact_input_and_account_binding_verified':True}))
'''
result = subprocess.check_output(['docker', 'exec', '-i', 'quant-company-quant-feed-worker-1',
    'python', '/app/entrypoint.py', 'python', '-B', '-c', code, hashlib.sha256(raw).hexdigest()], input=raw, timeout=60)
assert path.read_bytes() == raw
print(json.dumps({'schema_version': 1, 'observed_at': datetime.now(UTC).isoformat(),
    'actual_codex_receipt': True, **json.loads(result)}))
