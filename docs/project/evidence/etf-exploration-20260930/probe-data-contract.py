"""One bound subscription-only diagnostic; never apply its proposed effects.

Use the existing runner's sandbox, account, feature restrictions and durable lane.
Only fixed validation labels are exported; raw model output remains private.
"""

import json
import pathlib
import subprocess
import sys

FAILED = "da444092-a806-5047-bc21-65411f92fe1c"
attempt = sys.argv[1] if len(sys.argv) > 1 else "01"
assert attempt in {"01", "02"}, "only_two_bounded_diagnostics_registered"
DIAGNOSTIC = "diag-data-contract-20261006-" + attempt


def run(args):
    return subprocess.check_output(args, text=True, timeout=120)


sql = """
SELECT json_build_object('request',t.request,'selection',
  (SELECT json_build_object('profile',profile,'revision',revision) FROM model_account_policy WHERE id=1))
FROM turns t JOIN tasks k ON k.id=t.task_id
WHERE t.id='da444092-a806-5047-bc21-65411f92fe1c' AND t.status='blocked'
AND k.project_id='9aac0de4-2b97-5195-a720-287d324234f3' AND k.agent='data'
"""
payload = json.loads(run(["docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", sql]))
previous = json.loads((pathlib.Path("/var/lib/quant-company/codex/jobs") / (FAILED + ".json")).read_text())
assert previous["state"] == "failed" and previous["fault"]["code"] == "invalid_output"
assert previous["account"] == payload["selection"], "selected_account_changed"
assert payload["request"]["model"] == "gpt-5.6-terra" and payload["request"]["reasoning_effort"] == "high"
payload["request"]["request_id"] = DIAGNOSTIC

code = r"""
import asyncio,json,pathlib,re,sys
from dataclasses import replace
from pydantic import ValidationError
from quant_company.contracts import AgentDecision,ProviderFault,ProviderRequest
from quant_company.providers.codex_runtime import runner_from_environment
from quant_company.providers.codex_runner import ProcessRunner,atomic_json,strict_json
payload=json.load(sys.stdin);request=ProviderRequest.model_validate(payload['request'])
assert request.session is None and not request.web_search and request.output_contract=='agent_decision'
public={'request_id':request.request_id,'original_failed_request_id':'da444092-a806-5047-bc21-65411f92fe1c','model':request.model,'reasoning_effort':request.reasoning_effort,'effects_applied':0,'scientific_trials_added':0}
known={'say','status','tools','name','arguments','artifacts','title','content','source_ids','messages','recipient','text','delegations','agent','instruction','memories','shared','follow_up','at'}
class Inspector(ProcessRunner):
 async def run(self,*args,**kwargs):
  result=await super().run(*args,**kwargs)
  try:
   events=[strict_json(line) for line in result.stdout.splitlines() if line.strip()]
   messages=[e['item']['text'] for e in events if e.get('type')=='item.completed' and e.get('item',{}).get('type')=='agent_message']
   if not messages: return result
   envelope=strict_json(messages[-1]);decision=strict_json(envelope['decision_json'])
   public['root_known_fields']=sorted(k for k in decision if k in known)
   public['root_unknown_field_count']=sum(k not in known for k in decision)
   public['tool_count']=len(decision.get('tools',[])) if isinstance(decision.get('tools',[]),list) else None
   try:
    AgentDecision.model_validate(decision)
   except ValidationError as exc:
    public['validation_errors']=[{'type':e['type'],'location':[part if isinstance(part,int) or part in known else 'unknown_field' for part in e['loc']]} for e in exc.errors(include_input=False,include_url=False)]
   else: public['validation_errors']=[]
  except (ValueError,TypeError,KeyError): pass
  return result
async def main():
 runner=runner_from_environment();runner.config=replace(runner.config,timeout_seconds=120)
 runner.process=Inspector()
 path=runner.config.jobs_dir/(request.request_id+'.validation.json')
 assert not path.exists(),'existing_diagnostic_requires_readback'
 try:
  response=await runner.run(request,**payload['selection'])
  public['state']='complete';public['decision_status']=response.decision.status
 except ProviderFault as fault:
  public['state']='failed';public['fault_code']=fault.code
  public['parse_phase']=re.findall(r'\(([a-z_]+):([a-z_]+)\)',fault.message)
 atomic_json(path,public)
 print(json.dumps(public,ensure_ascii=False))
asyncio.run(main())
"""
result = subprocess.run(["docker", "exec", "-i", "quant-company-codex-runtime-1", "python", "-c", code],
                        input=json.dumps(payload), text=True, capture_output=True, timeout=150, check=True)
print(result.stdout.strip())
