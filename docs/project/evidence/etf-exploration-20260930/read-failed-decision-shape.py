"""Inspect final decision structure in the exact failed CLI transcript, read-only.

Never read authentication files or export prompts, model text or reasoning.
"""

import json
import subprocess

FAILED = "da444092-a806-5047-bc21-65411f92fe1c"
query = "SELECT request FROM turns WHERE id='da444092-a806-5047-bc21-65411f92fe1c' AND status='blocked'"
request = json.loads(subprocess.check_output([
    "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", query
], text=True))

code = r"""
import hashlib,json,pathlib,sys
from datetime import UTC,datetime
from pydantic import ValidationError
from quant_company.contracts import AgentDecision,ProviderRequest
from quant_company.providers.codex_runtime import runner_from_environment
from quant_company.providers.codex_runner import cli_prompt,strict_json
request=ProviderRequest.model_validate(json.load(sys.stdin))
runner=runner_from_environment()
receipt=strict_json((runner.config.jobs_dir/(request.request_id+'.json')).read_bytes())
assert receipt['state']=='failed' and receipt['fault']['code']=='invalid_output'
profile=receipt['account']['profile']
directory=runner.account_config(profile).codex_home/'sessions'
date=datetime.fromtimestamp(receipt['started_at'],UTC)
day=directory/date.strftime('%Y')/date.strftime('%m')/date.strftime('%d')
expected=hashlib.sha256(cli_prompt(request).decode().strip().encode()).hexdigest()
known={'say','status','tools','name','arguments','artifacts','title','content','source_ids','messages','recipient','text','delegations','agent','instruction','memories','shared','follow_up','at'}
matches=[]
for path in day.glob('*.jsonl'):
 if path.is_symlink() or not path.is_file() or path.stat().st_size>10*1024*1024: continue
 events=[json.loads(line) for line in path.read_text().splitlines() if line.strip()]
 bound=False
 for event in events:
  payload=event.get('payload',{})
  if event.get('type')=='response_item' and payload.get('type')=='message' and payload.get('role')=='user':
   for item in payload.get('content',[]):
    text=item.get('text','')
    if hashlib.sha256(text.strip().encode()).hexdigest()==expected: bound=True
 if not bound: continue
 finals=[]
 for event in events:
  payload=event.get('payload',{})
  if event.get('type')=='response_item' and payload.get('type')=='message' and payload.get('role')=='assistant' and payload.get('phase')=='final_answer':
   finals.extend(item.get('text','') for item in payload.get('content',[]) if item.get('type')=='output_text')
  elif event.get('type')=='event_msg' and payload.get('type')=='agent_message':
   finals.append(payload.get('message',''))
 for final in finals[-1:]:
  envelope=strict_json(final);value=strict_json(envelope['decision_json'])
  item={'exact_prompt_digest_matched':True,'root_known_fields':sorted(k for k in value if k in known),'root_unknown_field_count':sum(k not in known for k in value)}
  try: AgentDecision.model_validate(value)
  except ValidationError as exc:
   item['validation_errors']=[{'type':e['type'],'location':[part if isinstance(part,int) or part in known else 'unknown_field' for part in e['loc']]} for e in exc.errors(include_input=False,include_url=False)]
  else: item['validation_errors']=[]
  matches.append(item)
print(json.dumps({'failed_request_id':request.request_id,'session_day_exists':day.is_dir(),'exact_matches':matches,'model_inferences':0,'effects_applied':0},ensure_ascii=False))
"""
result = subprocess.run(["docker", "exec", "-i", "quant-company-codex-runtime-1", "python", "-c", code],
                        input=json.dumps(request), text=True, capture_output=True, timeout=120, check=True)
print(result.stdout.strip())
