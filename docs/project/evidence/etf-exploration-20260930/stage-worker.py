import hashlib, json, os, pathlib, subprocess, sys
from datetime import UTC, datetime

P = pathlib.Path
COMMIT = '3c848af95dc33b7da444a55018fd41d374c10025'
ACTIVE = P('/home/achii/.config/quant-company/research-worker.json')
BASE = P('/home/achii/quant-company-qualification/exploration-20261001')
sys.path.insert(0, json.loads(ACTIVE.read_text())['company_repo']+'/src')
from quant_company.research.worker import WorkerConfig, sha_file, atomic_json
from quant_company.research.releases import prepare_release

assert sha_file(ACTIVE) == '23920eaaaa415ac1e2c2ddbe918f908955de20a54223b9b04a1a76644da5deef', 'active_config_changed'
old = WorkerConfig.from_file(ACTIVE)
BASE.mkdir(mode=0o700, exist_ok=True)
release_root = BASE / 'release-candidates'
release_root.mkdir(mode=0o700, exist_ok=True)
prepared = prepare_release(source_snapshot=P('/tmp/company.bundle'),
    snapshot_sha256='aa49d49f075af2410d0000348094b784228b16a08d9e30c7902ebd813b1c8c04',
    commit=COMMIT, release_root=release_root, config=old)

# Qualification subprocess imports the exact candidate, not the operating source.
code = r'''
import hashlib,json,os,pathlib,socket,subprocess,sys
from quant_company.company import fingerprint
from quant_company.research.program_contracts import ResearchProgram
from quant_company.research.mission_contracts import MissionSpec
from quant_company.research.adaptive_executor import AdaptiveProfile
from quant_company.research.sandbox import profile_from_dict,SandboxSpec,InputMount,run_sandbox
from quant_company.research.workspace import snapshot_files
P=pathlib.Path
base=P(sys.argv[1]); old=json.loads(P(sys.argv[2]).read_text())
program=ResearchProgram.model_validate_json(P('/tmp/candidate-program.json').read_text())
assert fingerprint(program.model_dump(mode='json'))=='04cee0f99abab3dfb94b37756a195753960fffd5a3f623ce0dd3563bf776d162'
mission=MissionSpec.model_validate(program.envelopes[0].template)
assert not mission.data.policy.confirmation_eligible and not mission.data.policy.deployment_eligible
assert MissionSpec.model_validate_json(mission.model_dump_json())==mission
profile=AdaptiveProfile.model_validate_json(P(old['adaptive_profiles']['kr-etf-research-v2']).read_text())
runtime=profile_from_dict(profile.runtime)
job=base/'namespace-fixture'; job.mkdir()
repo=job/'code';repo.mkdir();(repo/'src').mkdir()
hidden=job/'host-secret-canary';hidden.write_text('synthetic credential canary')
entry=f"""import json,os,socket
from pathlib import Path
assert not Path({str(hidden)!r}).exists()
assert not Path('/code/.git').exists()
assert not Path('/proc/{os.getpid()}').exists()
assert 'AWS_SECRET_ACCESS_KEY' not in os.environ
assert 'OPERATOR_TOKEN' not in os.environ
assert set(n for _,n in socket.if_nameindex())<={{'lo'}}
for name in ['/code/src/entry.py','/inputs/policy.json']:
 try: Path(name).write_text('forbidden')
 except OSError: pass
 else: raise AssertionError('read-only mount writable')
value=json.loads(Path('/inputs/policy.json').read_text())
assert value['data']['policy']['result_use']=='hypothesis_generation_only'
assert value['data']['policy']['confirmation_eligible'] is False
assert value['data']['policy']['deployment_eligible'] is False
Path('/output/roundtrip.json').write_text(json.dumps(value))
"""
(repo/'src/entry.py').write_text(entry)
subprocess.run(['git','init','-q',str(repo)],check=True)
subprocess.run(['git','-C',str(repo),'add','src/entry.py'],check=True)
env=dict(os.environ,GIT_AUTHOR_NAME='Engineering fixture',GIT_AUTHOR_EMAIL='fixture@example.invalid',GIT_COMMITTER_NAME='Engineering fixture',GIT_COMMITTER_EMAIL='fixture@example.invalid')
subprocess.run(['git','-C',str(repo),'commit','-qm','Non-performance namespace fixture'],check=True,env=env)
commit=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
files=snapshot_files(repo,commit)
inputs=job/'inputs';inputs.mkdir();source=inputs/'policy.json';source.write_text(mission.model_dump_json())
output=job/'output';output.mkdir()
spec=SandboxSpec(runtime,repo,commit,files,(InputMount(source,'policy.json',hashlib.sha256(source.read_bytes()).hexdigest()),),(inputs,),job,output,'src/entry.py',timeout_seconds=30,fixture_only=True)
os.environ['AWS_SECRET_ACCESS_KEY']='synthetic-do-not-inherit';os.environ['OPERATOR_TOKEN']='synthetic-do-not-inherit'
receipt=run_sandbox(spec)
assert receipt.exit_code==0 and not receipt.timed_out, 'real_namespace_fixture_failed'
assert MissionSpec.model_validate_json((output/'roundtrip.json').read_text())==mission
print(json.dumps({'sandbox':receipt.to_dict(),'sandbox_mocked':False,'synthetic_policy_roundtrip':True,'research_price_rows_read':False,'scientific_trials_added':0,'data_policy_digest':fingerprint(mission.data.policy.model_dump(mode='json')),'profile_sha256':hashlib.sha256(P(old['adaptive_profiles']['kr-etf-research-v2']).read_bytes()).hexdigest()}))
'''
env = dict(os.environ, PYTHONPATH=str(prepared.directory/'code/src'), PYTHONDONTWRITEBYTECODE='1')
result = subprocess.run([sys.executable,'-B','-c',code,str(BASE),str(ACTIVE)],capture_output=True,text=True,timeout=180,env=env)
if result.returncode:
    (BASE/'qualification.failure.log').write_text(result.stderr)
    raise RuntimeError('candidate_qualification_failed_private_log_retained')
proof = json.loads(result.stdout)
assert sha_file(ACTIVE)=='23920eaaaa415ac1e2c2ddbe918f908955de20a54223b9b04a1a76644da5deef'
proof.update(state='exact_worker_release_qualified_not_active',candidate_company_commit=COMMIT,
    candidate_directory=str(prepared.directory),candidate_config_sha256=prepared.config_sha256,
    release_receipt_sha256=sha_file(prepared.directory/'release.json'),active_config_unchanged=True,
    active_company_commit=old.company_commit,observed_at=datetime.now(UTC).isoformat(),hostname=subprocess.check_output(['hostname'],text=True).strip())
atomic_json(BASE/'stage.json',proof)
print(json.dumps(proof))
