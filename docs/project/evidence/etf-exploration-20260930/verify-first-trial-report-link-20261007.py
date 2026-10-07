"""Read-only GET of the stored report link; output only status, bytes and SHA.

The private presigned URL stays in protected host/container memory and stdin.
Never print it, parse performance values, call a model, write a report or send Slack.
"""

import json
import subprocess
from datetime import UTC, datetime

query = """BEGIN TRANSACTION READ ONLY;
SELECT json_build_object('source_id',id,'synthetic',synthetic,'approved',approved,
 'view_url',content::jsonb#>>'{report,view_url}',
 'html_sha256',content::jsonb#>>'{report,html_sha256}',
 'expires_at',content::jsonb#>>'{report,expires_at}')
 FROM sources WHERE id='mission-report:de3597b0-e358-5a58-af5e-9f3c79df53e9';
ROLLBACK;"""
raw = subprocess.check_output([
    "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company",
    "-qAt", "-v", "ON_ERROR_STOP=1", "-c", query,
], text=True, timeout=30)
value = json.loads(raw)
assert value["approved"] is True and value["synthetic"] is False
assert value["html_sha256"] == "dba5657bf9e616e09ee8ed97dbfa1a7abeb3e47edc6b52a63f3f7b1329ca9ba2"
code = r'''
import hashlib,json,sys,urllib.error,urllib.parse,urllib.request
value=json.loads(sys.stdin.read())
parts=urllib.parse.urlsplit(value['view_url'])
assert parts.scheme=='https' and parts.hostname in {
 'quant-company-061039802347-backups.s3.amazonaws.com',
 'quant-company-061039802347-backups.s3.ap-northeast-2.amazonaws.com'}
assert parts.path=='/company/research/executions/1ac851a9-f5d0-55a3-9bf8-7d98193e42c8/'+value['html_sha256']+'.html'
result={'source_id':value['source_id'],'expected_html_sha256':value['html_sha256'],'expires_at':value['expires_at']}
try:
    with urllib.request.urlopen(value['view_url'],timeout=15) as response:
        digest=hashlib.sha256()
        size=0
        while chunk:=response.read(65536):
            digest.update(chunk)
            size+=len(chunk)
            assert size<=10485760
        result.update({'http_status':response.status,'content_type':response.headers.get('Content-Type'),
            'bytes':size,'actual_html_sha256':digest.hexdigest(),'sha256_matches':digest.hexdigest()==value['html_sha256']})
except urllib.error.HTTPError as error:
    result.update({'http_status':error.code,'error_type':type(error).__name__})
except Exception as error:
    result.update({'error_type':type(error).__name__})
print(json.dumps(result,sort_keys=True))
'''
completed = subprocess.run([
    "docker", "exec", "-i", "quant-company-api-1", "python", "-c", code,
], input=json.dumps(value), capture_output=True, text=True, timeout=45)
assert completed.returncode == 0
result = json.loads(completed.stdout)
print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), "read_only": True, **result}, indent=2))
