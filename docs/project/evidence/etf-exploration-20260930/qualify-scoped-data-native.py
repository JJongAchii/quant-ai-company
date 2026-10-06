"""Qualify one new schema-bound request through the isolated official subscription.

Only the trusted schema is changed in a separately identified diagnostic request.
The original proposal, frozen request and failure remain untouched. No effects apply.
"""

import json
import pathlib
import subprocess
from uuid import NAMESPACE_URL, uuid5

SOURCE = "86aea8cdf03b5543666813c97689acb1e8158a6d"
QUERY = """
SELECT json_build_object('request',t.request,'original_id',t.id,'selection',
 (SELECT json_build_object('profile',profile,'revision',revision) FROM model_account_policy WHERE id=1))
FROM turns t JOIN research_stage_attempts a ON a.task_id=t.task_id
JOIN research_mission_stages s ON s.id=a.stage_id
WHERE s.id='0b9b6ce9-6c3d-5dee-a9c5-0bbc18089ac5' AND a.attempt=2
AND s.program_id='f7deaf96-e677-5afe-93d4-18ac387043bb'
AND t.status='completed' AND t.response->'decision'->>'status'='complete'
AND jsonb_array_length(t.response->'decision'->'artifacts')=1
ORDER BY t.sequence DESC LIMIT 1
"""
payload = json.loads(subprocess.check_output([
    "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company",
    "-qAt", "-v", "ON_ERROR_STOP=1", "-c", QUERY
], text=True))
payload["request"]["request_id"] = str(uuid5(NAMESPACE_URL, "scoped-data-native:" + SOURCE + ":" + payload["original_id"]))
manifest = json.loads(pathlib.Path("/tmp/" + SOURCE + "-package.json").read_bytes())
assert manifest["source_commit"] == SOURCE
payload["manifest"] = manifest
root = "/tmp/scoped-data-native-" + SOURCE
payload["source_root"] = root

INSTALL = r'''
import hashlib,json,pathlib,sys,tarfile
root=pathlib.Path(sys.argv[1]);manifest=json.loads(sys.argv[2])
if not root.exists():
 root.mkdir(mode=0o755)
 with tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as stream:
  for member in stream:
   assert member.isfile() or member.isdir()
   assert not member.name.startswith('/') and '..' not in pathlib.PurePosixPath(member.name).parts
   if not member.name.startswith('src'):continue
   target=root/member.name
   if member.isdir():target.mkdir(parents=True,exist_ok=True)
   else:
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(stream.extractfile(member).read());target.chmod(0o644)
assert all(hashlib.sha256((root/name).read_bytes()).hexdigest()==digest for name,digest in manifest['files'].items())
'''
archive = pathlib.Path("/tmp/" + SOURCE + "-company.tar").read_bytes()
import hashlib  # noqa: E402

assert hashlib.sha256(archive).hexdigest() == manifest["archive_sha256"]
subprocess.run(["docker", "exec", "-i", "quant-company-codex-runtime-1", "python", "-c", INSTALL,
                root, json.dumps(manifest)], input=archive, capture_output=True, timeout=30, check=True)

CODE = r'''
import asyncio,hashlib,json,pathlib,re,sys
from datetime import UTC,datetime
payload=json.load(sys.stdin);root=pathlib.Path(payload['source_root'])
assert all(hashlib.sha256((root/name).read_bytes()).hexdigest()==digest for name,digest in payload['manifest']['files'].items())
sys.path.insert(0,str(root/'src'))
import quant_company
assert pathlib.Path(quant_company.__file__).resolve().is_relative_to(root)
from quant_company.contracts import ProviderFault,ProviderRequest
from quant_company.providers.codex_runtime import runner_from_environment
from quant_company.providers.codex_runner import ProcessRunner,atomic_json,request_digest,strict_json
from quant_company.research.program_contracts import DataAssessment
from quant_company.research.program_controller import data_assessment_output_schema
original=payload['request']['prompt'];prefix,body=original.split('MISSION DATA JSON:\n',1)
context=json.loads(body);schema=data_assessment_output_schema(context)
assert schema['properties']['schema_version']['const']==2
assert 'data_policy_digest' not in schema['properties'] and 'evaluation_prices' not in schema['properties']
context['output_schema']=schema
payload['request']['prompt']=prefix+'MISSION DATA JSON:\n'+json.dumps(context,ensure_ascii=False,allow_nan=False)
request=ProviderRequest.model_validate(payload['request'])
assert request.session is None and not request.web_search and request.output_contract=='research_stage_v1'
runner=runner_from_environment()
path=runner.config.jobs_dir/(request.request_id+'.validation.json')
public={'kind':'inactive-scoped-data-native-qualification','source_commit':payload['manifest']['source_commit'],
 'request_id':request.request_id,'original_request_id':payload['original_id'],'input_digest':request_digest(request),
 'source_files_verified':len(payload['manifest']['files']),'prompt_characters':len(request.prompt),
 'model':request.model,'reasoning_effort':request.reasoning_effort,'account':payload['selection'],
 'schema_version':2,'legacy_fields_excluded':True,'effects_applied':0,'scientific_trials_added':0,
 'production_changed':False,'observed_at':datetime.now(UTC).isoformat()}
async def main():
 if path.exists():
  value=json.loads(path.read_bytes());assert value['input_digest']==public['input_digest']
  if value.get('fault_code')!='busy' or (runner.config.jobs_dir/(request.request_id+'.json')).exists():
   print(json.dumps(value));return
  # Busy was acknowledged before inference and left no request receipt. Preserve
  # that observation before retrying the identical ID; never replace a started call.
  previous=path.with_name(request.request_id+'.preflight-busy.json')
  if not previous.exists():atomic_json(previous,value)
  public['acknowledged_busy_before_inference']=True
 class Inspector(ProcessRunner):
  async def run(self,*args,**kwargs):
   result=await super().run(*args,**kwargs)
   if kwargs.get('stdin'):
    known={'thread.started','turn.started','turn.completed','turn.failed','error','item.started','item.completed','item.updated'}
    info={'returncode':result.returncode,'stdout_bytes':len(result.stdout),'stderr_bytes':len(result.stderr)}
    try:
     events=[strict_json(line) for line in result.stdout.splitlines() if line.strip()]
     info.update(event_types=[e.get('type') if e.get('type') in known else 'other' for e in events],
      completed_count=sum(e.get('type')=='turn.completed' for e in events),
      error_count=sum(e.get('type') in {'turn.failed','error'} for e in events))
    except (ValueError,TypeError,AttributeError):info['event_parse_failed']=True
    public['transport_validation']=info;atomic_json(path,public)
   return result
 runner.process=Inspector()
 try:
  for preflight in range(8):
   try:
    response=await runner.run(request,**payload['selection']);break
   except ProviderFault as fault:
    if fault.code!='busy' or preflight==7:raise
    public['busy_preflight_count']=preflight+1
    await asyncio.sleep(5)
  decision=response.decision
  assert decision.say=='' and not decision.messages and not decision.delegations and not decision.memories
  if decision.status=='complete' and len(decision.artifacts)==1 and not decision.tools:
   value=DataAssessment.model_validate_json(decision.artifacts[0].content)
   envelope=context['task']['proposal']['envelope']
   assert value.schema_version==2 and value.research_scope.model_dump(mode='json')==context['research_scopes'][envelope]
   public.update(state='complete',artifact_contract_valid=True,canonical_scope_matched=True,
                 employee_data_decision=value.decision)
  else:public.update(state='incomplete',artifact_contract_valid=False,tool_count=len(decision.tools))
 except ProviderFault as fault:
  public.update(state='failed',fault_code=fault.code,parse_phase=re.findall(r'\(([a-z_]+):([a-z_]+)\)',fault.message))
 except (ValueError,AssertionError):public.update(state='failed',fault_code='scoped_qualification_contract')
 receipt=runner.config.jobs_dir/(request.request_id+'.json')
 if receipt.exists():
  value=json.loads(receipt.read_bytes());assert value['input_digest']==public['input_digest']
  public.update(cli_version=value.get('cli_version'),started_at=value.get('started_at'),completed_at=value.get('completed_at'))
 atomic_json(path,public);print(json.dumps(public))
asyncio.run(main())
'''
result = subprocess.run(["docker", "exec", "-i", "quant-company-codex-runtime-1", "python", "-c", CODE],
                        input=json.dumps(payload), text=True, capture_output=True, timeout=340, check=True)
print(result.stdout.strip())
