"""Inspect a prompt-bound failed native data transcript without exporting its text.

Authentication files, model reasoning, scientific outcomes and prompts remain local.
This is a read-only diagnosis, never a recovered proposal or replacement inference.
"""

import json
import subprocess

FAILED = "f38f1417-e299-5908-b5ac-f5b14c966d75"
query = f"""
SELECT t.request FROM turns t JOIN research_stage_attempts a ON a.task_id=t.task_id
JOIN research_mission_stages s ON s.id=a.stage_id
WHERE t.id='{FAILED}' AND t.status='blocked' AND s.stage='program_data'
AND s.program_id='f7deaf96-e677-5afe-93d4-18ac387043bb'
"""
request = json.loads(subprocess.check_output([
    "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company",
    "-qAt", "-v", "ON_ERROR_STOP=1", "-c", query
], text=True))

CODE = r'''
import hashlib,json,pathlib,re,sys
from collections import Counter
from datetime import UTC,datetime
from pydantic import ValidationError
from quant_company.contracts import ProviderRequest,ResearchStageOutput
from quant_company.providers.codex_runtime import runner_from_environment
from quant_company.providers.codex_runner import cli_prompt,strict_json
request=ProviderRequest.model_validate(json.load(sys.stdin))
runner=runner_from_environment()
receipt=strict_json((runner.config.jobs_dir/(request.request_id+'.json')).read_bytes())
assert receipt['state']=='failed' and receipt['fault']['code']=='invalid_output'
assert request.output_contract=='research_stage_v1' and not request.web_search and request.session is None
directory=runner.account_config(receipt['account']['profile']).codex_home/'sessions'
date=datetime.fromtimestamp(receipt['started_at'],UTC)
day=directory/date.strftime('%Y')/date.strftime('%m')/date.strftime('%d')
expected=hashlib.sha256(cli_prompt(request).decode().strip().encode()).hexdigest()
known_fields={'action','read_path','read_offset','artifact_json'}
known_events={'task_started','task_complete','turn_aborted','error','warning','stream_error',
              'agent_message','agent_reasoning','token_count','context_compacted'}
tags={'invalid_schema','response_format','rate_limit','usage_limit','unauthorized','stream_disconnected',
      'connection_reset','retry','reconnect','error','warning'}
matches=[]
for path in day.glob('*.jsonl'):
 if path.is_symlink() or not path.is_file() or path.stat().st_size>10*1024*1024: continue
 # The CLI's own transport records are not trusted model JSON.
 try: events=[json.loads(line) for line in path.read_text().splitlines() if line.strip()]
 except (ValueError,UnicodeError): continue
 bound=False
 for event in events:
  payload=event.get('payload',{})
  if event.get('type')=='response_item' and payload.get('type')=='message' and payload.get('role')=='user':
   for item in payload.get('content',[]):
    if hashlib.sha256(item.get('text','').strip().encode()).hexdigest()==expected: bound=True
 if not bound: continue
 finals=[];counts=Counter();failures=[]
 for event in events:
  payload=event.get('payload',{})
  if event.get('type')=='event_msg':
   kind=payload.get('type');counts[kind if kind in known_events else 'other']+=1
   if kind in {'error','warning','stream_error','turn_aborted'}:
    text=json.dumps(payload,sort_keys=True)
    failures.append({'kind':kind,'payload_sha256':hashlib.sha256(text.encode()).hexdigest(),
                     'known_tags':sorted(tag for tag in tags if tag in text.lower())})
   if kind=='agent_message': finals.append(payload.get('message',''))
  if (event.get('type')=='response_item' and payload.get('type')=='message'
      and payload.get('role')=='assistant' and payload.get('phase')=='final_answer'):
   finals.extend(item.get('text','') for item in payload.get('content',[]) if item.get('type')=='output_text')
 summary={'exact_prompt_digest_matched':True,'transcript_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
          'event_counts':dict(counts),'transport_failures':failures,'final_count':len(finals)}
 if finals:
  final=finals[-1]
  summary['final_sha256']=hashlib.sha256(final.encode()).hexdigest()
  try:
   value=ResearchStageOutput.model_validate(strict_json(final))
   summary['native_output_valid']=True
   summary['action']=value.action
  except (ValueError,TypeError,ValidationError) as exc:
   summary['native_output_valid']=False
   if isinstance(exc,ValidationError):
    summary['validation_errors']=[{'type':e['type'],'location':[part if isinstance(part,int) or part in known_fields
       else 'unknown_field' for part in e['loc']]} for e in exc.errors(include_input=False,include_url=False)]
   else: summary['validation_error_type']=type(exc).__name__
 matches.append(summary)
print(json.dumps({'failed_request_id':request.request_id,'session_day_exists':day.is_dir(),
                 'exact_matches':matches,'model_inferences':0,'effects_applied':0},ensure_ascii=False))
'''
result = subprocess.run(["docker", "exec", "-i", "quant-company-codex-runtime-1", "python", "-c", CODE],
                        input=json.dumps(request), text=True, capture_output=True, timeout=120, check=True)
print(result.stdout.strip())
