"""Register a bound technical receipt; probe or refresh the existing repair stage.

Settings/credentials remain in a protected host env file and a disposable 256MiB
container. No model, worker execution, raw-response edit or scientific decision.
"""

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

CODE = r"""
import hashlib,json,os
from datetime import datetime
from pathlib import Path
from psycopg.types.json import Jsonb
from quant_company.company import Company,fingerprint
from quant_company.config import Settings
from quant_company.research.controller import stage_prompt
from quant_company.research.mission_backend import MissionBackend
from quant_company.research.missions import MissionStore

company=Company(Settings())
root=Path('/operator')
manifest=json.loads((root/'manifest.json').read_bytes())
raw=(root/'failure-evidence.json').read_bytes()
assert hashlib.sha256(raw).hexdigest()==manifest['payload_files']['failure-evidence.json']
data=json.loads(raw)
identity='research-failure:'+data['job_id']
content=json.dumps(data,sort_keys=True,ensure_ascii=False,allow_nan=False)
content_hash=hashlib.sha256(content.encode()).hexdigest()
mode=os.environ['REPAIR_PROVISION_MODE']
extra=None
if mode!='source':
    with company.db.transaction() as conn:
        prepared_snapshot=MissionStore(company).snapshot(conn,manifest['mission_id'],public=False)
    # File/profile preparation may open its own authorization transactions.
    # Run it outside project/job locks, then recheck the exact stage below.
    extra=MissionBackend(company).context(prepared_snapshot,None)
with company.db.transaction() as conn:
    project=company._project(conn,manifest['project_id'])
    snapshot=MissionStore(company).snapshot(conn,manifest['mission_id'],public=False)
    assert project['revision']==5 and project['status']=='active'
    assert snapshot['state']=='active' and snapshot['manifest_digest']==manifest['mission_digest']
    assert snapshot['program_id']==manifest['program_id'] and snapshot['stage']['stage']=='repair'
    programme=conn.execute('SELECT state,manifest_digest FROM research_programs WHERE id=%s',
                           (manifest['program_id'],)).fetchone()
    assert programme['state']=='active' and programme['manifest_digest']==manifest['program_digest']
    job=conn.execute('SELECT * FROM research_jobs WHERE id=%s FOR UPDATE',(data['job_id'],)).fetchone()
    assert job['state']=='failed' and job['artifact_sha256'] is None
    assert str(job['trial_id'])==data['trial_id'] and str(job['mission_id'])==data['mission_id']==snapshot['id']
    assert job['manifest']['plan']['code_commit']==data['code_commit']
    assert job['manifest']['code_files']==data['code_files']
    assert job['company_commit']==data['company_commit']==manifest['scientific_commit']
    assert data['evaluation_started'] is False and data['qualification']['exit_code']==1
    source=conn.execute('SELECT * FROM sources WHERE id=%s',(identity,)).fetchone()
    if mode=='source':
        duplicate=bool(source)
        if source:
            assert source['content']==content and source['approved'] and not source['synthetic']
            assert str(source['project_id'])==manifest['project_id']
        else:
            conn.execute('''INSERT INTO sources(id,title,uri,content,available_at,project_id,approved,synthetic)
                VALUES (%s,%s,%s,%s,%s,%s,true,false)''',
                (identity,'Verified second-trial warmup failure','operator-receipt:'+data['job_id'],content,
                 datetime.fromisoformat(data['observed_at']),manifest['project_id']))
            company._event(conn,'research_failure_evidence_registered',{'job_id':data['job_id'],
                'trial_id':data['trial_id'],'source_id':identity,'content_sha256':content_hash,
                'actor':'operator:root','operation_commit':manifest['commit']},manifest['project_id'])
        report={'state':'failure_evidence_registered','source_id':identity,'content_sha256':content_hash,
                'duplicate':duplicate,'database_mutated':not duplicate}
    else:
        assert source and source['content']==content and source['approved'] and not source['synthetic']
        row=conn.execute('SELECT * FROM research_mission_stages WHERE id=%s FOR UPDATE',
                         (manifest['stage_id'],)).fetchone()
        assert row['stage']=='repair' and str(row['mission_id'])==snapshot['id']
        task=conn.execute('SELECT * FROM tasks WHERE id=%s',(row['task_id'],)).fetchone()
        previous_job_hash=fingerprint(job)
        assert prepared_snapshot['stage']==snapshot['stage']
        assert prepared_snapshot['manifest_digest']==snapshot['manifest_digest']
        # Context preparation above creates only immutable evidence files;
        # the probe's database transition rolls back.
        failed=extra['failed_build']
        assert failed['job_id']==data['job_id'] and failed['failure_source_ids']==[identity]
        required=set(failed['code_paths']+[failed['manifest_file']])
        source_entries=[entry for entry in extra['evidence_sources'] if entry['source_id']==identity]
        assert len(source_entries)==1
        required.add(source_entries[0]['file'])
        context=dict(row['context'])
        files=dict(context['_private_files'])
        for name in required:
            value=extra['_private_files'][name]
            assert name not in files or files[name]['sha256']==value['sha256']
            files[name]=value
        context.update(failed_build=failed,_private_files=files)
        context['available_files']=[{'name':name,'sha256':value['sha256'],'size':value['size']}
                                   for name,value in sorted(files.items())]
        sources=list(context['evidence_sources'])
        if identity not in {entry['source_id'] for entry in sources}:
            sources.extend(source_entries)
        context['evidence_sources']=sources
        context['_repair_recovery']={'operation_commit':manifest['commit'],'source_id':identity,
                                     'content_sha256':content_hash,'preserved_actor_responses':True}
        if mode=='refresh':
            paused=conn.execute('SELECT paused_until,reason FROM runtime_control WHERE id=1').fetchone()
            assert paused['paused_until'] and paused['reason']=='research-repair-'+manifest['commit']
            assert row['state'] in ('waiting','running') and not context.get('_repair_hold')
            frozen=conn.execute('''SELECT count(*) AS n FROM turns WHERE task_id=%s
                AND (status='running' OR (status='queued' AND request IS NOT NULL))''',(row['task_id'],)).fetchone()
            assert frozen['n']==0
        conn.execute('''UPDATE research_mission_stages SET context=%s,error=NULL,
            retry_at=CASE WHEN state='waiting' THEN now() ELSE retry_at END,updated_at=now() WHERE id=%s''',
                     (Jsonb(context),row['id']))
        role,prompt=stage_prompt(company,conn,task)
        payload=json.loads(prompt.split('MISSION DATA JSON:\n',1)[1])
        definitions=payload['output_schema']['$defs']
        assert set(definitions['RepairArtifact']['required'])=={'patches','rationale','repair_source_ids'}
        assert definitions['RepairHold']['properties']['blocked']['const'] is True
        assert payload['failed_build']['failure_source_ids']==[identity] and len(prompt)<=90000
        assert fingerprint(conn.execute('SELECT * FROM research_jobs WHERE id=%s',(data['job_id'],)).fetchone())==previous_job_hash
        report={'state':'actual_repair_context_verified' if mode=='probe' else 'actual_repair_context_refreshed',
                'stage_id':str(row['id']),'task_id':str(row['task_id']),'attempt':row['attempt'],
                'previous_state':row['state'],'failed_code_files':len(failed['code_paths']),
                'failure_source_id':identity,'failed_code_hashes_verified':True,
                'exact_patch_and_hold_schemas_verified':True,'model':role.model,'reasoning_effort':role.reasoning_effort,
                'prompt_characters':len(prompt),'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
                'failed_job_unchanged':True,'prior_context_files_and_raw_responses_preserved':True,
                'model_call_created':False,'worker_execution_created':False,
                'immutable_context_files_prepared':True,'database_mutated':mode=='refresh',
                'transaction_rolled_back':mode=='probe'}
        if mode=='probe':
            conn.rollback()
        else:
            company._event(conn,'research_repair_evidence_refreshed',{'stage_id':str(row['id']),
                'task_id':str(row['task_id']),'attempt':row['attempt'],'source_id':identity,
                'actor':'operator:root','operation_commit':manifest['commit']},manifest['project_id'])
print(json.dumps(report))
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("source", "probe", "refresh"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / "manifest.json").read_bytes())
    assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == manifest["payload_files"][Path(__file__).name]
    rows = json.loads(subprocess.check_output([
        "docker", "inspect", "quant-company-api-1", "quant-company-worker-1", "quant-company-postgres-1"]))
    api, worker, postgres = rows
    owner = api if args.mode == "source" else worker
    network = sorted(set(owner["NetworkSettings"]["Networks"]) & set(postgres["NetworkSettings"]["Networks"]))[0]
    image = owner["Image"]
    if args.mode == "probe":
        prepared = json.loads((Path("/var/lib/quant-company/releases")
                               / ("research-repair-" + manifest["commit"] + "-prepared.json")).read_bytes())
        image = prepared["images"]["worker"]["image_id"]
    descriptor, name = tempfile.mkstemp(prefix="research-repair-env-", dir="/var/lib/quant-company/releases")
    env_path = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            for value in owner["Config"]["Env"]:
                assert "\n" not in value
                stream.write(value + "\n")
            stream.write("REPAIR_PROVISION_MODE=" + args.mode + "\n")
        result = subprocess.run([
            "docker", "run", "--rm", "-i", "--memory", "256m", "--network", network,
            "--volumes-from", owner["Id"], "--env-file", str(env_path),
            "--mount", "type=bind,src=" + str(root) + ",dst=/operator,readonly",
            "--entrypoint", "python", image, "/app/entrypoint.py", "python", "-",
        ], input=CODE, capture_output=True, text=True, timeout=90)
        if result.returncode:
            error = root / (args.mode + "-provision.private-error.log")
            error.write_text(result.stderr)
            error.chmod(0o600)
            raise RuntimeError("repair_provision_failed_check_private_host_receipt")
        print(json.dumps(json.loads(result.stdout), indent=2))
    finally:
        env_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
