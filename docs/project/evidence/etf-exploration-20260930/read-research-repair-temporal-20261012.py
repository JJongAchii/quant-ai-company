"""Read the repair's actual Temporal timer and selected-account signal revisions."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

CODE = r"""
import asyncio,json
from quant_company.company import Company
from quant_company.config import Settings
from quant_company.runtime import connect

async def main():
    settings=Settings()
    company=Company(settings)
    with company.db.transaction() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        row=conn.execute('''SELECT u.id,u.status,u.sequence,u.request IS NOT NULL AS request_frozen,
            p.profile,p.revision FROM research_mission_stages s JOIN turns u ON u.task_id=s.task_id
            CROSS JOIN model_account_policy p WHERE s.id='2d0ec857-8601-5cca-927d-f7f51877696d'
            AND p.id=1 ORDER BY u.sequence DESC LIMIT 1''').fetchone()
    client=await connect(settings)
    identity='company-turn-'+str(row['id'])
    handle=client.get_workflow_handle(identity)
    description=await handle.describe()
    history=await handle.fetch_history()
    timers={}
    signal_revisions=[]
    activity_results=[]
    for event in history.events:
        if event.HasField('timer_started_event_attributes'):
            a=event.timer_started_event_attributes
            timers[a.timer_id]={'timer_id':a.timer_id,'started_at':event.event_time.ToJsonString(),
                'seconds':a.start_to_fire_timeout.seconds,'nanos':a.start_to_fire_timeout.nanos}
        if event.HasField('timer_fired_event_attributes'):
            timers.pop(event.timer_fired_event_attributes.timer_id,None)
        if event.HasField('timer_canceled_event_attributes'):
            timers.pop(event.timer_canceled_event_attributes.timer_id,None)
        if event.HasField('workflow_execution_signaled_event_attributes'):
            a=event.workflow_execution_signaled_event_attributes
            if a.signal_name=='model_account_changed':
                values=await client.data_converter.decode(a.input.payloads)
                signal_revisions.extend(v for v in values if type(v) is int)
        if event.HasField('activity_task_completed_event_attributes'):
            values=await client.data_converter.decode(event.activity_task_completed_event_attributes.result.payloads)
            activity_results.extend({k:v[k] for k in ('state','reason','seconds') if k in v}
                                    for v in values if isinstance(v,dict))
    print(json.dumps({'workflow_id':identity,'workflow_state':description.status.name,
        'turn_id':str(row['id']),'turn_state':row['status'],'turn_sequence':row['sequence'],
        'request_frozen':row['request_frozen'],'selected_profile':row['profile'],
        'selected_account_revision':row['revision'],'received_account_signal_revisions':signal_revisions,
        'pending_timers':list(timers.values()),'recent_activity_results':activity_results[-4:],
        'database_mutated':False,'temporal_mutated':False}))

asyncio.run(main())
"""


def main():
    completed = subprocess.run([
        "docker", "exec", "-i", "quant-company-worker-1", "python",
        "/app/entrypoint.py", "python", "-",
    ], input=CODE, capture_output=True, text=True, timeout=45)
    if completed.returncode:
        error = Path("/var/lib/quant-company/releases/research-repair-temporal-read-20261012.private-error.log")
        error.write_text(completed.stderr)
        error.chmod(0o600)
        raise RuntimeError("temporal_metadata_read_failed_check_private_host_receipt")
    result = json.loads(completed.stdout)
    result["observed_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
