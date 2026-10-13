"""Two bound real ChatGPT calls using inactive code; apply no proposed effects.

Run through the trusted administrative path. Credentials remain in the existing
isolated runtime. Reuse its official CLI, selected account, sandbox and lane lock.
Original frozen requests and their failed receipts are never edited or replayed.
"""

import hashlib
import json
import pathlib
import subprocess
import sys
import tarfile
from uuid import NAMESPACE_URL, uuid5

SOURCE = "bb1e42e8a33899f7a290e96bd71d3540a23eeb37"
CASES = {"initial": "da444092-a806-5047-bc21-65411f92fe1c",
         "expanded": "578a8874-b2f8-5762-b872-37aec557dc76"}
case = sys.argv[1]
assert case in CASES
original_id = CASES[case]
qualification_id = str(uuid5(NAMESPACE_URL, "research-data-contract:" + SOURCE + ":" + case + ":" + original_id))
root = pathlib.Path("/tmp/research-data-format-" + SOURCE)
archive = pathlib.Path("/tmp/" + SOURCE + "-src.tar")
manifest = json.loads(pathlib.Path("/tmp/" + SOURCE + "-src.json").read_text())
assert manifest["source_commit"] == SOURCE
assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest["archive_sha256"]
if not root.exists():
    root.mkdir(mode=0o755)
    with tarfile.open(archive) as stream:
        members = stream.getmembers()
        assert all(not m.issym() and not m.islnk() and not m.name.startswith("/")
                   and ".." not in pathlib.PurePosixPath(m.name).parts for m in members)
        assert {m.name for m in members if m.isfile()} == set(manifest["files"])
        stream.extractall(root)
assert all(hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
           for name, digest in manifest["files"].items())

sql = f"""
SELECT json_build_object('request',t.request,'selection',
  (SELECT json_build_object('profile',profile,'revision',revision) FROM model_account_policy WHERE id=1),
  'hold',s.context->'_program_hold')
FROM turns t JOIN tasks k ON k.id=t.task_id JOIN research_mission_stages s ON s.task_id=k.id
JOIN research_programs p ON p.id=s.program_id
WHERE t.id='{original_id}' AND t.status='blocked' AND k.agent='data'
AND k.project_id='9aac0de4-2b97-5195-a720-287d324234f3'
AND p.id='f7deaf96-e677-5afe-93d4-18ac387043bb' AND p.state='active'
AND p.manifest_digest='c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db'
AND s.stage='program_data'
"""
# An earlier attempt has a different task_id. Select its bound stage via attempts.
sql = sql.replace("JOIN research_mission_stages s ON s.task_id=k.id",
                  "JOIN research_stage_attempts a ON a.task_id=k.id JOIN research_mission_stages s ON s.id=a.stage_id")
payload = json.loads(subprocess.check_output(["docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres",
                                            "-d", "quant_company", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", sql],
                                           text=True, timeout=30))
assert payload["hold"]["reason"] in {"data_response_contract_requires_remediation",
                                     "repeated_data_output_contract_failure"}
previous = json.loads((pathlib.Path("/var/lib/quant-company/codex/jobs") / (original_id + ".json")).read_text())
assert previous["state"] == "failed" and previous["fault"]["code"] == "invalid_output"
assert previous["account"] == payload["selection"], "selected_account_changed"
assert payload["request"]["model"] == "gpt-5.6-terra" and payload["request"]["reasoning_effort"] == "high"
assert payload["request"].get("output_contract", "agent_decision") == "agent_decision"
payload["request"]["request_id"] = qualification_id
payload["request"]["output_contract"] = "research_stage_v1"
payload.update(source_manifest=manifest, source_root=str(root), original_id=original_id, case=case)
install = r'''
import pathlib,sys,tarfile
root=pathlib.Path(sys.argv[1])
if not root.exists():
 root.mkdir(mode=0o755)
 with tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as stream:
  for member in stream:
   assert member.isfile() or member.isdir()
   assert not member.name.startswith('/') and '..' not in pathlib.PurePosixPath(member.name).parts
   target=root/member.name
   if member.isdir(): target.mkdir(parents=True,exist_ok=True)
   else:
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(stream.extractfile(member).read());target.chmod(0o644)
'''
# Docker archive-copy does not write into this read-only container's tmpfs.
# Install as its existing unprivileged UID, only inside the writable /tmp mount.
subprocess.run(["docker", "exec", "-i", "quant-company-codex-runtime-1", "python", "-c", install, str(root)],
               input=archive.read_bytes(), check=True, capture_output=True, timeout=30)

code = r'''
import asyncio,hashlib,json,pathlib,re,sys
from dataclasses import replace
payload=json.load(sys.stdin)
root=pathlib.Path(payload['source_root'])
assert all(hashlib.sha256((root/name).read_bytes()).hexdigest()==digest for name,digest in payload['source_manifest']['files'].items())
sys.path.insert(0,str(root/'src'))
import quant_company
assert pathlib.Path(quant_company.__file__).resolve().is_relative_to(root)
from quant_company.contracts import ProviderFault,ProviderRequest
from quant_company.providers.codex_runtime import runner_from_environment
from quant_company.providers.codex_runner import ProcessRunner,atomic_json,output_schema,request_digest,strict_json
request=ProviderRequest.model_validate(payload['request'])
assert request.session is None and not request.web_search
public={'kind':'real-subscription-inactive-data-contract-qualification','source_commit':payload['source_manifest']['source_commit'],
        'archive_sha256':payload['source_manifest']['archive_sha256'],'source_files_verified':len(payload['source_manifest']['files']),
        'case':payload['case'],'request_id':request.request_id,'original_failed_request_id':payload['original_id'],
        'input_digest':request_digest(request),'model':request.model,'reasoning_effort':request.reasoning_effort,
        'prompt_characters':len(request.prompt),'output_contract':request.output_contract,'output_fields':sorted(output_schema(request)['properties']),
        'effects_applied':0,'scientific_trials_added':0,'production_source_changed':False}
async def main():
 runner=runner_from_environment();runner.config=replace(runner.config,timeout_seconds=180)
 class Inspector(ProcessRunner):
  async def run(self,*args,**kwargs):
   result=await super().run(*args,**kwargs)
   if not kwargs.get('stdin'): return result
   known={'thread.started','turn.started','turn.completed','turn.failed','error','item.started','item.completed','item.updated'}
   tags={'invalid_schema','response_format','rate_limit','usage_limit','unauthorized','stream_disconnected','connection_reset'}
   info={'returncode':result.returncode,'stdout_bytes':len(result.stdout),'stderr_bytes':len(result.stderr)}
   try:
    events=[strict_json(line,cli_web_event=True) for line in result.stdout.splitlines() if line.strip()]
    info['event_types']=[e.get('type') if e.get('type') in known else 'other' for e in events]
    info['failure_tags']=sorted(tag for tag in tags if tag in json.dumps([e for e in events if e.get('type') in {'error','turn.failed'}]).lower())
    info['completed_count']=sum(e.get('type')=='turn.completed' for e in events)
    info['top_error_count']=sum(e.get('type')=='error' for e in events)
   except (ValueError,TypeError,AttributeError):
    info['event_parse_failed']=True
   public['transport_validation']=info
   return result
 runner.process=Inspector()
 path=runner.config.jobs_dir/(request.request_id+'.validation.json')
 if path.exists():
  existing=json.loads(path.read_bytes())
  assert existing['input_digest']==public['input_digest'] and existing['source_commit']==public['source_commit']
  print(json.dumps(existing,ensure_ascii=False));return
 try:
  response=await runner.run(request,**payload['selection'])
  d=response.decision
  assert d.say=='' and not d.messages and not d.memories and not d.delegations
  assert d.status=='continue' and len(d.tools)==1 and not d.artifacts
  assert d.tools[0].name=='research_control' and d.tools[0].arguments['action']=='read_stage_file'
  public.update(state='complete',decision_status=d.status,proposed_read=d.tools[0].arguments)
 except ProviderFault as fault:
  public.update(state='failed',fault_code=fault.code,parse_phase=re.findall(r'\(([a-z_]+):([a-z_]+)\)',fault.message))
 except AssertionError:
  public.update(state='failed',fault_code='qualification_effect_contract')
 receipt=json.loads((runner.config.jobs_dir/(request.request_id+'.json')).read_bytes())
 assert receipt['input_digest']==public['input_digest']
 public.update(cli_version=receipt['cli_version'],execution_lane=receipt['execution_lane'],account=receipt['account'],
               started_at=receipt.get('started_at'),completed_at=receipt.get('completed_at'))
 atomic_json(path,public)
 print(json.dumps(public,ensure_ascii=False))
asyncio.run(main())
'''
result = subprocess.run(["docker", "exec", "-i", "quant-company-codex-runtime-1", "python", "-c", code],
                        input=json.dumps(payload), text=True, capture_output=True, timeout=220, check=True)
print(result.stdout.strip())
