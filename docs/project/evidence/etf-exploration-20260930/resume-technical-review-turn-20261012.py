"""Wake a paused existing turn with its already-selected account revision.

Requires verified cutover, cleared pause and unchanged signed mission. Never
increments account revision, alters a request, creates a turn or replays a write.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

CODE = r"""
import asyncio,json,os
from quant_company.company import Company
from quant_company.config import Settings
from quant_company.runtime import connect

async def main():
    settings=Settings()
    company=Company(settings)
    commit=os.environ['TECHNICAL_REVIEW_OPERATION_COMMIT']
    apply=os.environ['TECHNICAL_REVIEW_WAKE_APPLY']=='true'
    with company.db.transaction() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        pause=conn.execute('SELECT paused_until FROM runtime_control WHERE id=1').fetchone()
        assert pause['paused_until'] is None,'runtime_still_paused'
        stage=conn.execute('''SELECT s.*,p.revision,p.status AS project_status,
            m.state AS mission_state,m.manifest_digest,r.state AS program_state,r.manifest_digest AS program_digest
            FROM research_mission_stages s JOIN research_missions m ON m.id=s.mission_id
            JOIN research_programs r ON r.id=m.program_id JOIN projects p ON p.id=m.project_id
            WHERE s.id='a711339e-12ab-5aab-b91a-41cba6575beb' ''').fetchone()
        assert stage['revision']==5 and stage['project_status']==stage['mission_state']==stage['program_state']=='active'
        assert stage['manifest_digest']=='4d67e615bfdd78e686389735a66d0644259b771081cc4707019e4c7d972da737'
        assert stage['program_digest']=='c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db'
        assert stage['stage']=='technical_review' and stage['actor']=='director' and stage['state']=='running'
        assert stage['context']['review_evidence']['operation_commit']==commit
        assert not stage['context'].get('_repair_hold'),'independent_hold_preserved'
        row=conn.execute('''SELECT u.id,u.status,u.request IS NOT NULL AS request_frozen,
            u.due_at<=now() AS due,p.profile,p.revision FROM turns u CROSS JOIN model_account_policy p
            WHERE u.task_id=%s AND p.id=1 ORDER BY u.sequence DESC LIMIT 1''',(stage['task_id'],)).fetchone()
    result={'turn_id':str(row['id']),'selected_profile':row['profile'],
        'selected_account_revision':row['revision'],'database_mutated':False,
        'account_selection_changed':False,'request_changed':False,'new_turn_created':False,
        'scientific_execution_supplied':False,'temporal_signal_sent':False}
    if row['request_frozen'] or row['status']!='queued' or not row['due']:
        print(json.dumps({**result,'state':'existing_turn_does_not_need_wake'}))
        return
    client=await connect(settings)
    handle=client.get_workflow_handle('company-turn-'+str(row['id']))
    description=await handle.describe()
    assert description.status.name=='RUNNING'
    history=await handle.fetch_history()
    revisions=[]
    timers=set()
    for event in history.events:
        if event.HasField('timer_started_event_attributes'):
            timers.add(event.timer_started_event_attributes.timer_id)
        if event.HasField('timer_fired_event_attributes'):
            timers.discard(event.timer_fired_event_attributes.timer_id)
        if event.HasField('timer_canceled_event_attributes'):
            timers.discard(event.timer_canceled_event_attributes.timer_id)
        if event.HasField('workflow_execution_signaled_event_attributes'):
            attributes=event.workflow_execution_signaled_event_attributes
            if attributes.signal_name=='model_account_changed':
                values=await client.data_converter.decode(attributes.input.payloads)
                revisions.extend(value for value in values if type(value) is int)
    assert timers and max(revisions,default=0)<row['revision'],'existing_revision_wake_not_eligible'
    if apply:
        await handle.signal('model_account_changed',row['revision'])
    print(json.dumps({**result,'state':'existing_selected_revision_wake_sent' if apply else 'existing_turn_wake_verified',
        'workflow_id':'company-turn-'+str(row['id']),'temporal_signal_sent':apply,'prior_signal_revisions':revisions,
        'pending_timer_count':len(timers)}))

asyncio.run(main())
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    assert re.fullmatch(r"[0-9a-f]{40}", args.commit)
    os.umask(0o077)
    root = Path("/var/lib/quant-company/releases")
    prefix = "technical-review-" + args.commit
    applied = json.loads((root / (prefix + "-applied.json")).read_bytes())
    assert applied["state"] == "technical_review_patch_applied" and applied["pause_restored"]
    assert applied["protected_config_preserved"] and applied["other_service_identities_preserved"]
    prepared = json.loads((root / (prefix + "-prepared.json")).read_bytes())
    rows = json.loads(subprocess.check_output(["docker", "inspect", "quant-company-api-1", "quant-company-worker-1"]))
    for service, row in zip(("api", "worker"), rows, strict=True):
        assert row["Image"] == prepared["images"][service]["image_id"] and row["State"]["Running"]
    receipt = root / (prefix + "-resume-turn.json")
    metadata = {"commit": args.commit, "operator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "recorded_at": datetime.now(UTC).isoformat(), "database_mutated": False,
                "scientific_decision_or_execution_supplied": False}
    if args.apply:
        assert not receipt.exists(), "resume_receipt_exists_inspect_before_any_retry"
        receipt.write_text(json.dumps({**metadata, "state": "wake_intent_recorded"}) + "\n")
    completed = subprocess.run([
        "docker", "exec", "-i", "-e", "TECHNICAL_REVIEW_OPERATION_COMMIT=" + args.commit,
        "-e", "TECHNICAL_REVIEW_WAKE_APPLY=" + str(args.apply).lower(), "quant-company-worker-1",
        "python", "/app/entrypoint.py", "python", "-",
    ], input=CODE, capture_output=True, text=True, timeout=45)
    if completed.returncode:
        private = root / (prefix + "-resume-turn.private-error.log")
        private.write_text(completed.stderr)
        if args.apply:
            receipt.write_text(json.dumps({**metadata, "state": "wake_result_unconfirmed_inspect_history"}) + "\n")
        raise RuntimeError("resume_turn_unconfirmed_inspect_private_receipt_and_workflow_history")
    result = {**metadata, **json.loads(completed.stdout)}
    if args.apply:
        receipt.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
