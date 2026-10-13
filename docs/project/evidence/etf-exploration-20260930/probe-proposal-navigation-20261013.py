"""Project actual eligible identities in the prepared image; always roll back."""

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

CODE = r"""
import hashlib,json
from pathlib import Path
from quant_company.company import Company
from quant_company.config import Settings
from quant_company.research.controller import stage_prompt
import quant_company.research.controller as controller
company=Company(Settings())
manifest=json.loads(Path('/operator/manifest.json').read_bytes())
with company.db.transaction() as conn:
    row=conn.execute('SELECT * FROM research_mission_stages WHERE id=%s',(manifest['stage_id'],)).fetchone()
    assert row['stage']=='proposal' and row['actor']=='researcher_kr'
    assert row['state'] in ('waiting','running','received')
    assert str(row['mission_id'])==manifest['mission_id']
    task=conn.execute('SELECT * FROM tasks WHERE id=%s',(row['task_id'],)).fetchone()
    mission=conn.execute('SELECT * FROM research_missions WHERE id=%s',(manifest['mission_id'],)).fetchone()
    program=conn.execute('SELECT * FROM research_programs WHERE id=%s',(manifest['program_id'],)).fetchone()
    assert mission['state']==program['state']=='active'
    assert mission['manifest_digest']==manifest['mission_digest'] and mission['approval_event_id']
    assert program['manifest_digest']==manifest['program_digest'] and program['approval_event_id']
    trial=conn.execute('SELECT * FROM research_mission_trials WHERE id=%s',(manifest['closed_trial_id'],)).fetchone()
    assert trial['technical_closed_at'] is not None and trial['state']=='technical_waiting' and trial['result_id'] is None
    disposition=conn.execute('SELECT digest FROM research_technical_dispositions WHERE trial_id=%s',(trial['id'],)).fetchone()
    assert disposition['digest']==manifest['disposition_digest']
    role,prompt=stage_prompt(company,conn,task)
    context=json.loads(prompt.split('MISSION DATA JSON:\n',1)[1])
    contract=context['proposal_contract']
    expected=[str(r['id']) for r in conn.execute('''SELECT t.id FROM research_mission_trials t
        JOIN research_mission_interpretations i ON i.trial_id=t.id
        WHERE t.mission_id=%s AND t.result_id IS NOT NULL ORDER BY t.ordinal DESC,t.id''',(row['mission_id'],)).fetchall()]
    supersedes=[str(r['id']) for r in conn.execute('''SELECT p.id FROM research_mission_proposals p
        JOIN research_mission_rejections r ON r.proposal_id=p.id JOIN research_missions m ON m.id=p.mission_id
        WHERE p.mission_id=%s AND p.cycle=m.cycle ORDER BY p.created_at,p.id''',(row['mission_id'],)).fetchall()]
    assert contract['allowed_predecessor_trial_ids']==expected==[manifest['reported_trial_id']]
    assert contract['required_latest_predecessor_trial_id']==manifest['reported_trial_id']
    assert contract['allowed_supersedes_proposal_ids']==supersedes
    assert manifest['closed_trial_id'] not in expected and str(trial['proposal_id']) not in supersedes
    schema=context['output_schema']['properties']
    assert schema['predecessor_trial_ids']['items']['enum']==expected
    assert schema['predecessor_trial_ids']['contains']=={'const':manifest['reported_trial_id']}
    assert schema['supersedes_proposal_id']['anyOf'][0]['enum']==supersedes
    assert role.id=='researcher_kr' and role.model=='gpt-6-astra' and role.reasoning_effort=='high'
    assert len(prompt)<=90000
    code=Path(controller.__file__).read_bytes()
    assert hashlib.sha256(code).hexdigest()==manifest['payload_files']['runtime_controller.py']
    assert b'model_assignments_enabled' in code
    scope={key:mission[key] for key in ('cycle','cycle_trials','cumulative_trials','manifest_digest')}
    assert scope['cycle']==scope['cycle_trials']==scope['cumulative_trials']==1
    conn.rollback()
    print(json.dumps({'state':'actual_image_proposal_contract_verified','stage_id':str(row['id']),
        'task_id':str(task['id']),'stage_state':row['state'],'attempt':row['attempt'],
        'model':role.model,'reasoning_effort':role.reasoning_effort,'proposal_contract':contract,
        'previous_contract_error':context.get('previous_contract_error'),'last_error':context.get('last_error'),
        'prompt_characters':len(prompt),'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
        'technical_disposition_preserved':True,'scope_and_counters':scope,
        'consumer_guard_changed':False,'transaction_rolled_back':True,'database_mutated':False,
        'model_call_created':False,'scientific_execution_created':False}))
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "probe"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / "manifest.json").read_bytes())
    assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == manifest["payload_files"][Path(__file__).name]
    worker, postgres = json.loads(subprocess.check_output([
        "docker", "inspect", "quant-company-worker-1", "quant-company-postgres-1"]))
    network = sorted(set(worker["NetworkSettings"]["Networks"]) & set(postgres["NetworkSettings"]["Networks"]))[0]
    prepared = json.loads((Path("/var/lib/quant-company/releases") /
                          ("proposal-navigation-" + manifest["commit"] + "-prepared.json")).read_bytes())
    descriptor, name = tempfile.mkstemp(prefix="proposal-navigation-env-", dir="/var/lib/quant-company/releases")
    env_path = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            for value in worker["Config"]["Env"]:
                assert "\n" not in value
                stream.write(value + "\n")
        result = subprocess.run([
            "docker", "run", "--rm", "-i", "--memory", "256m", "--network", network,
            "--volumes-from", worker["Id"] + ":ro", "--env-file", str(env_path),
            "--mount", "type=bind,src=" + str(root) + ",dst=/operator,readonly",
            "--entrypoint", "python", prepared["images"]["worker"]["image_id"],
            "/app/entrypoint.py", "python", "-",
        ], input=CODE, capture_output=True, text=True, timeout=90)
        if result.returncode:
            error = root / (args.mode + "-probe.private-error.log")
            error.write_text(result.stderr)
            error.chmod(0o600)
            raise RuntimeError("proposal_contract_probe_failed_check_private_host_receipt")
        print(json.dumps(json.loads(result.stdout), indent=2))
    finally:
        env_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
