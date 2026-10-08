"""Restore one held audit to await its employee's citation correction.

Default: production PostgreSQL probe, fully rolled back. --apply schedules one
new ordinary correction turn on the same meaning attempt with all reads retained.
The operator never edits the previous proposal, decides a finding, calls a model,
reruns science, changes an audit scope or publishes a report.
"""

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--apply", action="store_true")
args = parser.parse_args()
native_path = Path("/var/lib/quant-company/codex/jobs/9e2bf588-30c0-531d-ab51-0f2426f4a9d4.json")
native_bytes = native_path.read_bytes()
native_sha = hashlib.sha256(native_bytes).hexdigest()
assert native_sha == "93305c1f747533a4456ed5d132a9caca827a8f87370fd63813eab23e37e8e199"
native = json.loads(native_bytes)
assert native["state"] == "complete"
container = json.loads(subprocess.check_output([
    "docker", "inspect", "quant-company-api-1",
], text=True, timeout=15))[0]
assert container["Id"] == "4b5a355ff1c5cfc5a578fc07e53cb99ea350c50c15cb183a2fcd8b2bedd28ebf"
assert container["Image"] == "sha256:84527ad82f04910d1e4cb018792ba8bb76f049385def73f961ba096a4566c93e"
assert container["State"]["Running"] and container["State"]["Health"]["Status"] == "healthy"
database_container = json.loads(subprocess.check_output([
    "docker", "inspect", "quant-company-postgres-1",
], text=True, timeout=15))[0]
networks = sorted(set(container["NetworkSettings"]["Networks"])
                  & set(database_container["NetworkSettings"]["Networks"]))
assert len(networks) == 1
available_kib = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()
                         if line.startswith("MemAvailable:")))
assert available_kib > 524288
operator_runtime = {"parent_container_id": container["Id"], "image_id": container["Image"],
    "disposable_container": "quant-company-meaning-citation-operator-20261007", "memory_limit_bytes": 268435456,
    "production_resource_limits_changed": False, "environment_and_mounts": "same trusted API environment and volumes",
    "secret_values_printed_or_persisted_in_repository": False}
payload = {"apply": args.apply, "native_receipt_sha256": native_sha,
           "input_digest": native["input_digest"], "response": native["result"], "operator_runtime": operator_runtime}

code = r'''
import hashlib,json
from datetime import UTC,datetime
from pathlib import Path

from psycopg.types.json import Jsonb
from quant_company.company import Company,fingerprint
from quant_company.config import Settings
from quant_company.contracts import ProviderRequest,ProviderResponse
from quant_company.providers.codex_runner import request_digest
from quant_company.research.controller import stage_prompt,stage_role
from quant_company.research.missions import MissionStore
from quant_company.research.program_contracts import MeaningReview
from quant_company.task_control import held,waiting_router

payload=json.loads(PAYLOAD_LITERAL)
company=Company(Settings())
operation='first-trial-meaning-citation-correction:8987ff99:20261007'
project_id='9aac0de4-2b97-5195-a720-287d324234f3'
mission_id='2ce40574-6368-5cef-b706-b4e67441b3de'
trial_id='de3597b0-e358-5a58-af5e-9f3c79df53e9'
audit_id='5e5e2013-373c-5868-9fc9-242985e085b3'
meaning_id='8987ff99-b738-5927-8ba2-0545cdab6884'
request_id='9e2bf588-30c0-531d-ab51-0f2426f4a9d4'
diagnostic='stage_contract_rejected: Test conclusion cites an artifact outside the validated package'
expected_paths={
 'mission/history.json',
 'evidence/challenges/a9d38fd5-6c52-4e1d-a326-b78dd4b4c903.json',
 'evidence/challenge_responses/bed77124-564d-4ba9-a625-777308b11cdb.json',
 'evidence/challenge_responses/a3e207ea-d615-4b40-96c0-1b92d1ae5124.json',
 'evidence/rejections/a3e207ea-d615-4b40-96c0-1b92d1ae5124.json',
 'evidence/rejections/6d9a3c7b-52e4-4f81-b267-85d094eb6a30.json',
}

class RollbackProbe(Exception):
    pass

def read_entry(entry):
    content=Path(entry['path']).read_bytes()
    assert hashlib.sha256(content).hexdigest()==entry['sha256']
    return content

result=None
try:
    with company.db.transaction() as conn:
        project=company._project(conn,project_id)
        assert project['revision']==5 and project['status']=='active'
        assert not waiting_router(conn,project_id)
        existing=conn.execute("SELECT id,detail FROM events WHERE project_id=%s AND kind=%s "
            "AND detail->>'operation_id'=%s ORDER BY id LIMIT 1",
            (project_id,'research_meaning_citation_correction_scheduled',operation)).fetchone()
        if existing:
            result={**existing['detail'],'event_id':existing['id'],'already_committed':True}
        else:
            program=conn.execute('SELECT * FROM research_programs WHERE id=%s FOR UPDATE',
                ('f7deaf96-e677-5afe-93d4-18ac387043bb',)).fetchone()
            assert program['state']=='active' and program['revision']==5
            assert program['manifest_digest']=='c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db'
            assert program['approval_event_id'].startswith('slack:')
            mission=conn.execute('SELECT * FROM research_missions WHERE id=%s FOR UPDATE',(mission_id,)).fetchone()
            assert mission['state']=='active' and mission['revision']==5 and mission['cumulative_trials']==1
            assert mission['project_id']==project['id'] and mission['program_id']==program['id']
            snapshot=MissionStore(company).snapshot(conn,mission_id,public=False)
            assert snapshot['stage']['stage']=='audit' and snapshot['stage']['trial_id']==trial_id
            audit=conn.execute('SELECT * FROM research_mission_stages WHERE id=%s FOR UPDATE',(audit_id,)).fetchone()
            meaning=conn.execute('SELECT * FROM research_mission_stages WHERE id=%s FOR UPDATE',(meaning_id,)).fetchone()
            assert audit['stage']=='audit' and audit['actor']=='validator' and audit['attempt']==1
            assert audit['state']=='waiting' and audit['error']=='audit_contract_requires_remediation'
            assert audit['context']['_audit_hold']=={'reason':'audit_contract_requires_remediation','diagnostic':diagnostic}
            assert audit['result'] is not None and audit['retry_at'] is None
            assert meaning['stage']=='meaning' and meaning['actor']=='financial_strategist' and meaning['attempt']==1
            assert meaning['state']=='received' and meaning['error'] is None and meaning['result'] is not None
            assert str(meaning['task_id'])=='b01abe83-f235-55a6-8dab-a3e6bf3a37c6'
            assert meaning['mission_id']==audit['mission_id']==mission['id']
            task=conn.execute('SELECT * FROM tasks WHERE id=%s FOR UPDATE',(meaning['task_id'],)).fetchone()
            original_role=stage_role(company,meaning['actor']).model_dump(mode='json')
            assert task['kind']=='research_stage' and task['agent']=='financial_strategist'
            assert task['status']=='completed' and task['turn_count']==80 and task['priority']==100
            assert task['revision']==5 and task['error'] is None and not held(conn,project,task)
            turn=conn.execute('SELECT * FROM turns WHERE id=%s FOR UPDATE',(request_id,)).fetchone()
            assert turn['task_id']==task['id'] and turn['sequence']==80 and turn['status']=='completed'
            response=ProviderResponse.model_validate(payload['response'])
            assert response.request_id==request_id and response.provider=='codex' and response.thread_id
            assert turn['response']==response.model_dump(mode='json')
            request=ProviderRequest.model_validate(turn['request'])
            assert request_digest(request)==payload['input_digest']
            assert len(response.decision.artifacts)==1 and response.decision.status=='complete'
            proposed=json.loads(response.decision.artifacts[0].content)
            assert proposed==meaning['result']
            value=MeaningReview.model_validate(proposed)
            assert str(value.trial_id)==trial_id
            outcome=conn.execute('SELECT digest FROM research_mission_outcomes WHERE trial_id=%s',(trial_id,)).fetchone()
            assert value.outcome_digest==outcome['digest']
            assert len(value.tests)==1 and str(value.tests[0].challenge_id)=='a9d38fd5-6c52-4e1d-a326-b78dd4b4c903'
            assert set(value.tests[0].evidence_paths)==expected_paths
            root=Path(audit['context']['_audit']['root'])
            verification_path=root/'audits/f405eb0f-5168-5309-b875-8e15eed1bb53.verification.json'
            verification_bytes=verification_path.read_bytes()
            verification_sha=hashlib.sha256(verification_bytes).hexdigest()
            assert verification_sha=='4e8c5645a8a69e2ad44471ac96d43cff62f3ff1c63dda4ab590a8d2c2f66b185'
            verification=json.loads(verification_bytes)
            assert verification['verdict']=='pass' and verification['violations']==[] and verification['receipt_violations']==[]
            assert verification['qlab_commit']=='37abfceb07f5b684d7c3210548fecef156e3f9fc'
            assert verification['binding']==audit['context']['_audit']['binding']
            scope=verification['scope_files']
            assert not expected_paths.intersection(scope)
            for name,digest in {**scope,**verification['audit_files']}.items():
                assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest
            mappings=meaning['context']['_private_files']
            reads=conn.execute('SELECT * FROM research_stage_reads WHERE stage_id=%s AND attempt=1 ORDER BY id',
                (meaning_id,)).fetchall()
            assert len(reads)==79
            assert all(mappings[item['path']]['sha256']==item['sha256'] for item in reads)
            required=meaning['context']['required_meaning_reads']
            assert len(required)==21
            assert all(any(item['path']==name and item['next_offset'] is None for item in reads) for name in required)
            for name in {item['path'] for item in reads}:
                read_entry(mappings[name])
            aliases={}
            for alias,entry in mappings.items():
                canonical=alias.removeprefix('audit/')
                if canonical in scope:
                    assert entry['sha256']==scope[canonical] and Path(entry['path'])==root/canonical
                    aliases[alias]=canonical
            assert len(aliases)==20
            protected_turns=conn.execute('SELECT * FROM turns WHERE task_id=%s ORDER BY sequence',(task['id'],)).fetchall()
            protected_audit_turns=conn.execute('SELECT * FROM turns WHERE task_id=%s ORDER BY sequence',(audit['task_id'],)).fetchall()
            protected_jobs=conn.execute('SELECT * FROM research_jobs WHERE mission_id=%s ORDER BY id',(mission_id,)).fetchall()
            protected_reservations=conn.execute('SELECT * FROM research_program_reservations WHERE program_id=%s ORDER BY job_id',
                (program['id'],)).fetchall()
            assert all(job['state']=='received' for job in protected_jobs) and len(protected_jobs)==1
            assert conn.execute('SELECT count(*) AS n FROM research_meaning_reviews WHERE trial_id=%s',(trial_id,)).fetchone()['n']==0
            assert conn.execute('SELECT count(*) AS n FROM research_mission_publications WHERE trial_id=%s',(trial_id,)).fetchone()['n']==0
            context={**meaning['context'],'meaning_citation_navigation':{
                'observed_contract_error':diagnostic,
                'prior_submission_request_id':request_id,
                'prior_submission':proposed,
                'canonical_citable_evidence_paths':sorted(scope),
                'read_alias_to_canonical_evidence_path':aliases,
                'rejected_citations':sorted(expected_paths),
                'rule':'TestAssessment.evidence_paths must contain only exact canonical paths from the validated package scope. '
                    'read_stage_file uses the existing available_files aliases; the citation and read namespaces differ. '
                    'General mission/evidence files and review/feedback.json are context, not validated package citation paths. '
                    'The schema permits an unresolved test to have evidence_paths=[], while addressed needs actual artifact paths. '
                    'Independently justify each test disposition; no external path is made valid by renaming it. '
                    'All prior submissions and completed reads remain preserved. Return a corrected MeaningReview for the same '
                    'trial and outcome digest without claiming any new test, execution, approval or publication.',
            }}
            conn.execute("UPDATE research_mission_stages SET state='running',context=%s,error=%s,updated_at=now() WHERE id=%s",
                (Jsonb(context),'meaning_citation_contract_correction_requested',meaning_id))
            new_turn_id=company._new_turn(conn,task)
            new_turn=conn.execute('SELECT * FROM turns WHERE id=%s',(new_turn_id,)).fetchone()
            assert new_turn['sequence']==81 and new_turn['status']=='queued' and new_turn['request'] is None
            new_task=conn.execute('SELECT * FROM tasks WHERE id=%s',(task['id'],)).fetchone()
            role,prompt=stage_prompt(company,conn,new_task,new_turn)
            assert len(prompt)<=90000 and 'meaning_citation_navigation' in prompt
            if (role.id!='financial_strategist' or role.model!=request.model
                    or role.reasoning_effort!=request.reasoning_effort
                    or stage_role(company,meaning['actor']).model_dump(mode='json')!=original_role):
                print(json.dumps({'guard':'role_configuration','expected_actor':meaning['actor'],
                    'original_id':original_role['id'],'actual_id':role.id,'model':role.model,
                    'reasoning_effort':role.reasoning_effort,'previous_request_model':request.model,
                    'previous_request_reasoning_effort':request.reasoning_effort,
                    'changed_fields':sorted(key for key in original_role if role.model_dump(mode='json').get(key)!=original_role[key])}))
                raise AssertionError('role_configuration_guard')
            audit_context=dict(audit['context'])
            audit_context.pop('_audit_hold')
            conn.execute("UPDATE research_mission_stages SET state='received',context=%s,error=NULL,updated_at=now() WHERE id=%s",
                (Jsonb(audit_context),audit_id))
            conn.execute('UPDATE research_stage_attempts SET error=NULL WHERE stage_id=%s AND attempt=1',(audit_id,))
            assert fingerprint(conn.execute('SELECT * FROM turns WHERE task_id=%s AND sequence<=80 ORDER BY sequence',
                (task['id'],)).fetchall())==fingerprint(protected_turns)
            assert fingerprint(conn.execute('SELECT * FROM turns WHERE task_id=%s ORDER BY sequence',
                (audit['task_id'],)).fetchall())==fingerprint(protected_audit_turns)
            assert fingerprint(conn.execute('SELECT * FROM research_stage_reads WHERE stage_id=%s AND attempt=1 ORDER BY id',
                (meaning_id,)).fetchall())==fingerprint(reads)
            assert fingerprint(conn.execute('SELECT * FROM research_jobs WHERE mission_id=%s ORDER BY id',
                (mission_id,)).fetchall())==fingerprint(protected_jobs)
            assert fingerprint(conn.execute('SELECT * FROM research_program_reservations WHERE program_id=%s ORDER BY job_id',
                (program['id'],)).fetchall())==fingerprint(protected_reservations)
            assert conn.execute('SELECT result FROM research_mission_stages WHERE id=%s',(meaning_id,)).fetchone()['result']==proposed
            assert conn.execute('SELECT result FROM research_mission_stages WHERE id=%s',(audit_id,)).fetchone()['result']==audit['result']
            assert verification_path.read_bytes()==verification_bytes
            detail={'schema_version':1,'operation_id':operation,'observed_at':datetime.now(UTC).isoformat(),
                'operator_runtime':payload['operator_runtime'],
                'project_id':project_id,'program_id':str(program['id']),'program_digest':program['manifest_digest'],
                'mission_id':mission_id,'trial_id':trial_id,'audit_stage_id':audit_id,'meaning_stage_id':meaning_id,
                'task_id':str(task['id']),'attempt':1,'previous_request_id':request_id,
                'original_native_receipt_sha256':payload['native_receipt_sha256'],'original_input_digest':payload['input_digest'],
                'original_proposal_digest':fingerprint(proposed),'original_audit_hold':audit['context']['_audit_hold'],
                'verification_sha256':verification_sha,'citation_scope_digest':verification['scope_digest'],
                'canonical_citable_paths':sorted(scope),'read_aliases':aliases,'rejected_citations':sorted(expected_paths),
                'required_reads_complete':21,'unchanged_read_receipts':79,'prompt_characters':len(prompt),
                'following_turn_id':str(new_turn_id),'following_sequence':81,'priority_unchanged':100,
                'role_configuration_preserved':True,'base_role_model':role.model,
                'base_role_reasoning_effort':role.reasoning_effort,'previous_request_model':request.model,
                'previous_request_reasoning_effort':request.reasoning_effort,'model_assignment_changed_by_operator':False,
                'previous_requests_responses_and_results_preserved':True,'audit_scope_and_verdict_unchanged':True,
                'signed_science_job_and_budget_unchanged':True,'provider_calls_by_operator':0,
                'queued_employee_correction_turns':1,'new_scientific_jobs':0,'scientific_decision_applied':False,
                'manual_publication':False,'source_rollout':False,
                'authority':'Existing owner deploy/continue/monitor instructions; concrete root review in PR105 before apply',
                'state':'meaning_citation_correction_queued_normal_publication_gates_restored'}
            if not payload['apply']:
                result={**detail,'state':'production_transaction_probe_passed_rolled_back','applied':False}
                raise RollbackProbe
            event=conn.execute('INSERT INTO events(project_id,kind,detail) VALUES(%s,%s,%s) RETURNING id',
                (project_id,'research_meaning_citation_correction_scheduled',Jsonb(detail))).fetchone()
            result={**detail,'event_id':event['id'],'already_committed':False,'applied':True}
except RollbackProbe:
    pass
print(json.dumps(result,ensure_ascii=False,sort_keys=True))
'''.replace("PAYLOAD_LITERAL", repr(json.dumps(payload, ensure_ascii=False)))

with tempfile.TemporaryDirectory(prefix="quant-company-meaning-citation-") as temporary:
    # Docker reads this protected host-only file; values never reach model prompts or Git.
    env_file = Path(temporary) / "operator.env"
    environment = container["Config"]["Env"]
    assert all("\n" not in entry and "\r" not in entry for entry in environment)
    env_file.write_text("\n".join(environment) + "\n")
    env_file.chmod(0o600)
    completed = subprocess.run([
        "docker", "run", "--rm", "-i", "--name", operator_runtime["disposable_container"],
        "--memory", "256m", "--cpus", "0.5", "--pids-limit", "64", "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--network", networks[0],
        "--env-file", str(env_file), "--volumes-from", "quant-company-api-1",
        "--entrypoint", "python", container["Image"], "/app/entrypoint.py", "python", "-",
    ], input=code, capture_output=True, text=True, timeout=60)
target = Path("/var/lib/quant-company/releases") / (
    "first-trial-meaning-citation-correction-applied-20261007.json" if args.apply
    else "first-trial-meaning-citation-correction-probe-20261007.json")
if completed.returncode:
    private_error = target.with_suffix(".private-error.log")
    diagnostic = completed.stderr + "\n" + completed.stdout
    private_error.write_text(diagnostic)
    private_error.chmod(0o600)
    safe_guard = None
    if completed.stdout.strip():
        candidate = json.loads(completed.stdout)
        if candidate.get("guard") == "role_configuration":
            safe_guard = candidate
    print(json.dumps({"state": "transaction_probe_failed_rolled_back", "role_guard": safe_guard,
        "returncode": completed.returncode, "stdout_characters": len(completed.stdout),
        "stderr_characters": len(completed.stderr),
        "exception_types": re.findall(r"(?m)^(?:[A-Za-z_][A-Za-z0-9_]*\.)*([A-Za-z_][A-Za-z0-9_]*)(?:: |$)", diagnostic),
        "operator_code_lines": re.findall(r'File "<stdin>", line ([0-9]+)', diagnostic)}))
    raise RuntimeError("meaning_citation_correction_failed_check_private_host_log")
assert native_path.read_bytes() == native_bytes
result = json.loads(completed.stdout)
target.write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
target.chmod(0o600)
print(json.dumps(result, indent=2, ensure_ascii=False))
