"""Bind verified new evidence to the existing hold; independently probe the stage.

Host credentials remain in a private temporary env file. Preflight imports the
prepared image without migrating. Attachment requires the owned deployment pause;
the subsequent real-data prompt projection rolls back all task/turn/outbox writes.
No model call, candidate disposition, scientific execution or approval is supplied.
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
from importlib.resources import files
from pathlib import Path
from psycopg.types.json import Jsonb
from quant_company.company import Company,fingerprint
from quant_company.config import Settings
from quant_company.research.controller import MissionController,stage_prompt
from quant_company.research.mission_backend import MissionBackend
from quant_company.research.missions import MissionStore
from quant_company.research.technical_review import TechnicalDisposition,qualification_failure

company=Company(Settings())
root=Path('/operator')
manifest=json.loads((root/'manifest.json').read_bytes())
raw=(root/'contract-evidence.json').read_bytes()
assert hashlib.sha256(raw).hexdigest()==manifest['payload_files']['contract-evidence.json']
packet=json.loads(raw)
content=json.dumps(packet,sort_keys=True,ensure_ascii=False,allow_nan=False)
content_hash=hashlib.sha256(content.encode()).hexdigest()
identity='research-contract:'+packet['trial_id']+':'+content_hash
assert identity==manifest['contract_source_id'] and content_hash==manifest['contract_content_sha256']
mode=os.environ['TECHNICAL_REVIEW_MODE']
prefix='technical-review-'+manifest['commit']
store=MissionStore(company)

def exact(conn):
    project=conn.execute('SELECT * FROM projects WHERE id=%s FOR UPDATE',(manifest['project_id'],)).fetchone()
    mission=conn.execute('SELECT * FROM research_missions WHERE id=%s FOR UPDATE',(manifest['mission_id'],)).fetchone()
    program=conn.execute('SELECT * FROM research_programs WHERE id=%s',(manifest['program_id'],)).fetchone()
    assert project['revision']==5 and project['status']=='active'
    assert str(mission['project_id'])==manifest['project_id'] and mission['state']=='active'
    assert mission['manifest_digest']==manifest['mission_digest'] and mission['approval_event_id']
    assert str(mission['program_id'])==manifest['program_id']
    assert program['state']=='active' and program['manifest_digest']==manifest['program_digest']
    trial=conn.execute('SELECT * FROM research_mission_trials WHERE id=%s FOR UPDATE',(packet['trial_id'],)).fetchone()
    job=conn.execute('SELECT * FROM research_jobs WHERE id=%s FOR UPDATE',(packet['job_id'],)).fetchone()
    assert trial['state']=='technical_waiting' and trial.get('technical_closed_at') is None
    assert str(trial['mission_id'])==manifest['mission_id'] and str(trial['job_id'])==packet['job_id']
    assert job['state']=='failed' and job['artifact_sha256'] is None and job['artifact_path'] is None
    assert str(job['mission_id'])==manifest['mission_id'] and str(job['trial_id'])==packet['trial_id']
    assert job['company_commit']==manifest['scientific_commit']
    assert job['manifest']['plan']['code_commit']==trial['plan']['code_commit']==packet['code_commit']
    assert job['manifest']['code_files']==packet['code_files']
    original=conn.execute('SELECT * FROM sources WHERE id=%s',(packet['failure_source_id'],)).fetchone()
    assert original['approved'] and not original['synthetic'] and str(original['project_id'])==manifest['project_id']
    assert hashlib.sha256(original['content'].encode()).hexdigest()==manifest['original_failure_content_sha256']
    diagnostic=json.loads(original['content'])
    assert diagnostic['evaluation_started'] is False and diagnostic['qualification']['exit_code']==1
    assert diagnostic['code_files']==packet['code_files'] and diagnostic['job_id']==packet['job_id']
    assert not conn.execute("SELECT 1 FROM research_mission_outcomes WHERE trial_id=%s AND kind='result'",
                            (packet['trial_id'],)).fetchone()
    held=conn.execute('SELECT * FROM research_mission_stages WHERE id=%s FOR UPDATE',(manifest['stage_id'],)).fetchone()
    task=conn.execute('SELECT * FROM tasks WHERE id=%s',(held['task_id'],)).fetchone()
    assert held['stage']=='repair' and held['state']=='waiting' and held['attempt']==79 and held['retry_at'] is None
    assert str(held['task_id'])==manifest['task_id'] and task['status']=='completed'
    assert held['error']=='repair_requires_evidence' and held['result']==held['context']['_repair_hold']
    assert held['result']['blocked'] is True
    assert not conn.execute("SELECT 1 FROM turns WHERE task_id=%s AND (status='running' OR "
                            "(status='queued' AND request IS NOT NULL))",(held['task_id'],)).fetchone()
    assert not conn.execute("SELECT 1 FROM research_jobs WHERE state IN "
                            "('queued','claimed','running','cancel_requested','uncertain')").fetchone()
    return project,mission,trial,job,held

if mode=='preflight':
    with company.db.transaction() as conn:
        project,mission,trial,job,held=exact(conn)
        schema=TechnicalDisposition.model_json_schema()
        assert set(schema['required'])=={'trial_id','failed_job_id','decision','rationale','source_ids'}
        assert schema['properties']['decision']['enum']==['retire_candidate','wait']
        report={'state':'prepared_image_import_and_actual_hold_verified','model_call_created':False,
                'scientific_execution_created':False,'database_mutated':False,
                'original_failure_verified':True,'independent_hold_preserved':True,
                'schema_required':schema['required'],'actual_stage_prompt_projected':False}
elif mode=='attach':
    # DDL is additive, serialized and committed before context preparation opens
    # separate authorization connections. The owned pause prevents scheduling.
    with company.db.transaction() as conn:
        pause=conn.execute('SELECT paused_until,reason FROM runtime_control WHERE id=1').fetchone()
        assert pause['reason']==prefix and pause['paused_until'] and pause['paused_until']>datetime.now(pause['paused_until'].tzinfo)
        exact(conn)
        conn.execute('SELECT pg_advisory_xact_lock(71350219)')
        conn.execute(files('quant_company.research').joinpath('mission_schema.sql').read_text())
    with company.db.transaction() as conn:
        project,mission,trial,job,held=exact(conn)
        before={key:fingerprint(value) for key,value in [('mission',mission),('trial',trial),('job',job),('hold',held['result'])]}
        qualification_failure(company,conn,mission,trial)
        source=conn.execute('SELECT * FROM sources WHERE id=%s',(identity,)).fetchone()
        duplicate=bool(source)
        if source:
            assert source['content']==content and source['approved'] and not source['synthetic']
            assert str(source['project_id'])==manifest['project_id']
        else:
            conn.execute('''INSERT INTO sources(id,title,uri,content,available_at,project_id,approved,synthetic)
                VALUES (%s,%s,%s,%s,%s,%s,true,false)''',
                (identity,'Verified frozen callback and first archive contract','operator-receipt:'+packet['job_id'],
                 content,datetime.fromisoformat(packet['observed_at']),manifest['project_id']))
        evidence={'source_ids':[identity],'content_sha256':{identity:content_hash},'operation_commit':manifest['commit']}
        context=dict(held['context'])
        old=context.get('_technical_review_evidence')
        assert old is None or old==evidence,'existing_review_binding_changed'
        if old is None:
            context['_technical_review_evidence']=evidence
            conn.execute('UPDATE research_mission_stages SET context=%s,updated_at=now() WHERE id=%s',
                         (Jsonb(context),held['id']))
            company._event(conn,'research_technical_review_evidence_attached',{'stage_id':str(held['id']),
                'trial_id':packet['trial_id'],'job_id':packet['job_id'],'source_id':identity,'content_sha256':content_hash,
                'actor':'operator:root','operation_commit':manifest['commit'],'disposition_supplied':False},manifest['project_id'])
        after={key:fingerprint(value) for key,value in [('mission',conn.execute('SELECT * FROM research_missions WHERE id=%s',(mission['id'],)).fetchone()),
              ('trial',conn.execute('SELECT * FROM research_mission_trials WHERE id=%s',(trial['id'],)).fetchone()),
              ('job',conn.execute('SELECT * FROM research_jobs WHERE id=%s',(job['id'],)).fetchone()),
              ('hold',conn.execute('SELECT result FROM research_mission_stages WHERE id=%s',(held['id'],)).fetchone()['result'])]}
        assert before==after
        snapshot=store.snapshot(conn,manifest['mission_id'],public=False)
        assert snapshot['stage']['stage']=='technical_review' and snapshot['stage']['held_stage_id']==manifest['stage_id']
        report={'state':'verified_technical_review_evidence_attached','source_id':identity,'content_sha256':content_hash,
                'duplicate_source':duplicate,'duplicate_binding':old is not None,
                'stage':snapshot['stage'],'held_repair_attempt':held['attempt'],'held_repair_retry_at':None,
                'original_failure_and_hold_preserved':True,'mission_trial_job_and_counters_preserved':before==after,
                'model_call_created':False,'scientific_execution_created':False,'disposition_supplied':False,
                'additive_migration_verified':True,'database_mutated':old is None or not duplicate}
else:
    with company.db.transaction() as conn:
        prepared_snapshot=store.snapshot(conn,manifest['mission_id'],public=False)
    assert prepared_snapshot['stage']['stage']=='technical_review'
    backend=MissionBackend(company)
    extra=backend.context(prepared_snapshot,None)
    with company.db.transaction() as conn:
        project,mission,trial,job,held=exact(conn)
        snapshot=store.snapshot(conn,manifest['mission_id'],public=False)
        assert snapshot['stage']==prepared_snapshot['stage']
        pause=conn.execute('SELECT paused_until,reason FROM runtime_control WHERE id=1').fetchone()
        assert pause['reason']==prefix and pause['paused_until'] and pause['paused_until']>datetime.now(pause['paused_until'].tzinfo)
        assert extra['review_evidence']['source_ids']==[identity]
        assert extra['failed_build']['job_id']==packet['job_id']
        assert extra['failed_build']['failure_source_ids']==[packet['failure_source_id']]
        assert set(extra['required_technical_reads'])=={extra['independent_hold_file'],extra['failed_build']['manifest_file']}
        relevant={entry['source_id'] for entry in extra['evidence_sources']}
        assert {identity,packet['failure_source_id']}<=relevant
        controller=MissionController(company,backend=backend)
        row=controller._schedule(conn,project,snapshot,extra)
        task=conn.execute('SELECT * FROM tasks WHERE id=%s',(row['task_id'],)).fetchone()
        role,prompt=stage_prompt(company,conn,task)
        payload=json.loads(prompt.split('MISSION DATA JSON:\n',1)[1])
        assert task['agent']==role.id=='director' and row['stage']=='technical_review'
        assert set(payload['output_schema']['required'])=={'trial_id','failed_job_id','decision','rationale','source_ids'}
        assert payload['review_evidence']['source_ids']==[identity] and len(prompt)<=90000
        report={'state':'actual_image_technical_review_prompt_projected','transaction_rolled_back':True,
                'synthetic_task_projection':True,'actual_independent_staff_decision':False,
                'schema_and_source_bindings_verified':True,'failed_code_hashes_verified':True,
                'original_failure_and_hold_preserved':True,'model':role.model,'reasoning_effort':role.reasoning_effort,
                'prompt_characters':len(prompt),'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
                'required_reads':extra['required_technical_reads'],'model_call_created':False,
                'scientific_execution_created':False,'disposition_supplied':False,'database_mutated':False,
                'immutable_context_files_prepared':True}
        conn.rollback()
print(json.dumps(report))
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "attach", "probe"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / "manifest.json").read_bytes())
    assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == manifest["payload_files"][Path(__file__).name]
    rows = json.loads(subprocess.check_output([
        "docker", "inspect", "quant-company-worker-1", "quant-company-postgres-1"]))
    owner, postgres = rows
    network = sorted(set(owner["NetworkSettings"]["Networks"]) & set(postgres["NetworkSettings"]["Networks"]))[0]
    prepared = json.loads((Path("/var/lib/quant-company/releases")
                           / ("technical-review-" + manifest["commit"] + "-prepared.json")).read_bytes())
    image = prepared["images"]["worker"]["image_id"]
    descriptor, name = tempfile.mkstemp(prefix="technical-review-env-", dir="/var/lib/quant-company/releases")
    env_path = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            for value in owner["Config"]["Env"]:
                assert "\n" not in value
                stream.write(value + "\n")
            stream.write("TECHNICAL_REVIEW_MODE=" + args.mode + "\n")
        result = subprocess.run([
            "docker", "run", "--rm", "-i", "--memory", "256m", "--network", network,
            "--volumes-from", owner["Id"], "--env-file", str(env_path),
            "--mount", "type=bind,src=" + str(root) + ",dst=/operator,readonly",
            "--entrypoint", "python", image, "/app/entrypoint.py", "python", "-",
        ], input=CODE, capture_output=True, text=True, timeout=180)
        if result.returncode:
            error = root / (args.mode + "-provision.private-error.log")
            error.write_text(result.stderr)
            error.chmod(0o600)
            raise RuntimeError("technical_review_provision_failed_check_private_host_receipt")
        print(json.dumps(json.loads(result.stdout), indent=2))
    finally:
        env_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
