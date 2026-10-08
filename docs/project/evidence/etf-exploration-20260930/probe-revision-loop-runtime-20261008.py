"""Read actual stage prompts in bounded disposable parent/patch image containers.

Credentials stay in a protected temporary host env file and mounted API secrets.
The read projection always rolls back its transaction: model-policy reads use
FOR SHARE, which PostgreSQL disallows in a transaction marked READ ONLY.
No model call or stage mutation occurs.
"""

import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

CODE = r'''
import hashlib,json,os,pathlib
from quant_company.company import Company
from quant_company.config import Settings
import quant_company.research.controller as controller
company=Company(Settings())
with company.db.transaction() as conn:
    identity=os.environ.get('REPAIR_PROBE_STAGE_ID')
    if identity:
        row=conn.execute('SELECT * FROM research_mission_stages WHERE id=%s',(identity,)).fetchone()
    else:
        row=conn.execute("SELECT * FROM research_mission_stages WHERE mission_id=%s AND stage IN ('proposal','challenge','selection') AND state='running' ORDER BY created_at DESC LIMIT 1",
                         ('2ce40574-6368-5cef-b706-b4e67441b3de',)).fetchone()
    assert row and row['stage'] in ('proposal','challenge','selection') and row['state']=='running'
    task=conn.execute('SELECT * FROM tasks WHERE id=%s',(row['task_id'],)).fetchone()
    role,prompt=controller.stage_prompt(company,conn,task)
    context=json.loads(prompt.split('MISSION DATA JSON:\n',1)[1])
    proposal_id=context['mission']['stage'].get('proposal_id')
    ids=([str(c['id']) for c in conn.execute('SELECT id FROM research_mission_challenges WHERE mission_id=%s AND proposal_id=%s ORDER BY created_at,id',
                                           (row['mission_id'],proposal_id)).fetchall()] if proposal_id else [])
    scoped=context.get('selection_contract')
    if scoped:
        assert scoped['current_challenge_ids']==ids and scoped['proposal_id']==proposal_id
        schema=context['output_schema']
        assert schema['properties']['responses']['minItems']==schema['properties']['responses']['maxItems']==len(ids)
        assert schema['$defs']['ChallengeResponse']['properties']['challenge_id']['enum']==ids
        assert context['stage_capabilities']['available_actions']==['read_stage_file','complete_stage_artifact']
    capability=context.get('stage_capabilities')
    if capability:
        assert capability['current_stage']==row['stage']
        assert capability['available_actions']==['read_stage_file','complete_stage_artifact']
    assert len(prompt)<=90000
    code=pathlib.Path(controller.__file__).read_bytes()
    conn.rollback()
    print(json.dumps({'state':'readonly_actual_stage_prompt_verified','stage_id':str(row['id']),
                      'task_id':str(task['id']),'stage':row['stage'],'actor':row['actor'],
                      'attempt':row['attempt'],'current_proposal_id':proposal_id,
                      'current_challenge_ids':ids,'model':role.model,'reasoning_effort':role.reasoning_effort,
                      'prompt_characters':len(prompt),'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
                      'controller_sha256':hashlib.sha256(code).hexdigest(),'scoped_navigation_present':bool(scoped),
                      'stage_capabilities_present':bool(capability),'selection_schema_verified':bool(scoped),
                      'effective_model_policy_preserved':b'model_assignments_enabled' in code,
                      'database_mutated':False,'transaction_rolled_back':True,'model_call_created':False}))
'''


def main():
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / "manifest.json").read_bytes())
    prepared = json.loads((Path("/var/lib/quant-company/releases")
                           / ("revision-loop-" + manifest["commit"] + "-prepared.json")).read_bytes())
    rows = json.loads(subprocess.check_output(["docker", "inspect", "quant-company-api-1", "quant-company-postgres-1"]))
    api, postgres = rows
    network = sorted(set(api["NetworkSettings"]["Networks"]) & set(postgres["NetworkSettings"]["Networks"]))[0]
    descriptor, name = tempfile.mkstemp(prefix="revision-loop-probe-", dir="/var/lib/quant-company/releases")
    env_path = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            for value in api["Config"]["Env"]:
                assert "\n" not in value
                stream.write(value + "\n")
        reports = []
        for mode, image in (("parent", api["Image"]), ("patch", prepared["images"]["api"]["tag"])):
            value = subprocess.run([
                "docker", "run", "--rm", "-i", "--memory", "256m", "--network", network,
                "--volumes-from", api["Id"] + ":ro", "--env-file", str(env_path),
                "--entrypoint", "python", image, "/app/entrypoint.py", "python", "-",
            ], input=CODE, capture_output=True, text=True, timeout=90)
            if value.returncode:
                error = root / (mode + "-probe.private-error.log")
                error.write_text(value.stderr)
                error.chmod(0o600)
                raise RuntimeError("readonly_prompt_probe_failed_check_private_host_receipt")
            report = json.loads(value.stdout)
            reports.append({"mode": mode, **report})
            if mode == "parent":
                with env_path.open("a") as stream:
                    stream.write("REPAIR_PROBE_STAGE_ID=" + report["stage_id"] + "\n")
        parent, patched = reports
        for key in ("stage_id", "task_id", "current_proposal_id", "current_challenge_ids", "model", "reasoning_effort"):
            assert parent[key] == patched[key], "existing_role_or_review_scope_changed"
        assert patched["stage_capabilities_present"] and patched["effective_model_policy_preserved"]
        assert patched["controller_sha256"] == manifest["payload_files"]["runtime_controller.py"]
        print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), "state": "parent_and_patch_readonly_probes_passed",
                          "reports": reports, "role_and_current_review_scope_preserved": True,
                          "production_applied": False}, indent=2))
    finally:
        env_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
