"""One separately identified transport diagnosis using the official isolated runtime.

The frozen failed request/receipt is preserved. No proposal is committed, no
science is executed, and only fixed transport metadata leaves the runtime.
"""

import json
import subprocess
from uuid import NAMESPACE_URL, uuid5

FAILED = "f38f1417-e299-5908-b5ac-f5b14c966d75"
QUALIFICATION = str(uuid5(NAMESPACE_URL, "native-data-transport-20261006:" + FAILED))
QUERY = f"""
SELECT json_build_object('request',t.request,'selection',
 (SELECT json_build_object('profile',profile,'revision',revision) FROM model_account_policy WHERE id=1))
FROM turns t JOIN research_stage_attempts a ON a.task_id=t.task_id
JOIN research_mission_stages s ON s.id=a.stage_id
WHERE t.id='{FAILED}' AND t.status='blocked' AND t.error='stage_response_rejected'
AND s.stage='program_data' AND s.program_id='f7deaf96-e677-5afe-93d4-18ac387043bb'
"""
payload = json.loads(subprocess.check_output([
    "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company",
    "-qAt", "-v", "ON_ERROR_STOP=1", "-c", QUERY
], text=True))
assert payload["request"]["request_id"] == FAILED
payload["request"]["request_id"] = QUALIFICATION
payload["original_id"] = FAILED

CODE = r'''
import asyncio,hashlib,json,re,sys
from datetime import UTC,datetime
from quant_company.contracts import ProviderFault,ProviderRequest
from quant_company.providers.codex_runtime import runner_from_environment
from quant_company.providers.codex_runner import ProcessRunner,atomic_json,request_digest
payload=json.load(sys.stdin);request=ProviderRequest.model_validate(payload['request'])
assert request.output_contract=='research_stage_v1' and not request.web_search and request.session is None
assert request.model=='gpt-5.6-terra' and request.reasoning_effort=='high'
runner=runner_from_environment()
previous=json.loads((runner.config.jobs_dir/(payload['original_id']+'.json')).read_bytes())
assert previous['state']=='failed' and previous['fault']['code']=='invalid_output'
assert previous['account']==payload['selection']
public={'kind':'one-bound-native-transport-diagnosis','request_id':request.request_id,
        'original_failed_request_id':payload['original_id'],'input_digest':request_digest(request),
        'observed_at':datetime.now(UTC).isoformat(),'model':request.model,'reasoning_effort':request.reasoning_effort,
        'prompt_characters':len(request.prompt),'effects_applied':0,'scientific_trials_added':0}
path=runner.config.jobs_dir/(request.request_id+'.validation.json')
async def main():
 if path.exists():
  value=json.loads(path.read_bytes());assert value['input_digest']==public['input_digest']
  print(json.dumps(value));return
 class Inspector(ProcessRunner):
  async def run(self,*args,**kwargs):
   result=await super().run(*args,**kwargs)
   if not kwargs.get('stdin'):return result
   info={'returncode':result.returncode,'stdout_bytes':len(result.stdout),'stderr_bytes':len(result.stderr),
         'stdout_sha256':hashlib.sha256(result.stdout).hexdigest()}
   known_types={'thread.started','turn.started','turn.completed','turn.failed','error','item.started','item.completed','item.updated'}
   known_items={'agent_message','reasoning','todo_list','error','web_search','command_execution','file_change','mcp_tool_call'}
   known_keys={'type','id','thread_id','item','text','message','error','usage','input_tokens','cached_input_tokens',
               'output_tokens','reasoning_output_tokens','code','status','retryable'}
   tags={'invalid_schema','response_format','rate_limit','usage_limit','unauthorized','stream_disconnected','connection_reset'}
   duplicates=[];events=[];invalid_lines=[]
   def object_pairs(pairs):
    out={}
    for key,value in pairs:
     if key in out:duplicates.append(key if key in known_keys else 'unknown_field')
     out[key]=value
    return out
   for index,line in enumerate(result.stdout.splitlines()):
    if not line.strip():continue
    try:events.append(json.loads(line,object_pairs_hook=object_pairs))
    except (ValueError,UnicodeError):invalid_lines.append(index)
   info.update(event_types=[e.get('type') if isinstance(e,dict) and e.get('type') in known_types else 'other'
                            for e in events],duplicate_keys=duplicates,invalid_json_lines=invalid_lines,
               item_types=[e['item'].get('type') if e['item'].get('type') in known_items else 'other'
                           for e in events if isinstance(e,dict) and isinstance(e.get('item'),dict)],
               completed_count=sum(isinstance(e,dict) and e.get('type')=='turn.completed' for e in events),
               top_error_count=sum(isinstance(e,dict) and e.get('type')=='error' for e in events),
               failure_tags=sorted(tag for tag in tags if tag in json.dumps([e for e in events
                   if isinstance(e,dict) and e.get('type') in {'error','turn.failed'}]).lower()))
   public['transport_validation']=info
   atomic_json(path,public)
   return result
 runner.process=Inspector()
 try:
  response=await runner.run(request,**payload['selection'])
  decision=response.decision
  public.update(state='complete',decision_status=decision.status,
                tool_count=len(decision.tools),artifact_count=len(decision.artifacts))
 except ProviderFault as fault:
  public.update(state='failed',fault_code=fault.code,
                parse_phase=re.findall(r'\(([a-z_]+):([a-z_]+)\)',fault.message))
 receipt=runner.config.jobs_dir/(request.request_id+'.json')
 if receipt.exists():
  value=json.loads(receipt.read_bytes())
  public.update(cli_version=value.get('cli_version'),account=value.get('account'),
                started_at=value.get('started_at'),completed_at=value.get('completed_at'))
 atomic_json(path,public);print(json.dumps(public))
asyncio.run(main())
'''
result = subprocess.run(["docker", "exec", "-i", "quant-company-codex-runtime-1", "python", "-c", CODE],
                        input=json.dumps(payload), text=True, capture_output=True, timeout=340, check=True)
print(result.stdout.strip())
