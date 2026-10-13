"""Read the second result audit's actual Temporal timer and selected-account signal revisions."""

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

CODE = r"""
import asyncio,json
from temporalio.api.enums.v1 import EventType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
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
            CROSS JOIN model_account_policy p WHERE s.id='d2ea3e11-20b4-5ddb-8b08-12dd55c26879'
            AND p.id=1 ORDER BY u.sequence DESC LIMIT 1''').fetchone()
        turns=conn.execute('''SELECT id,status,sequence,attempts,error,created_at,updated_at,due_at,
            request IS NOT NULL AS request_frozen,response IS NOT NULL AS response_received
            FROM turns WHERE task_id=(SELECT task_id FROM turns WHERE id=%s)
            ORDER BY sequence DESC LIMIT 4''',(row['id'],)).fetchall()
        database_waits=conn.execute('''SELECT pid,state,wait_event_type,wait_event,
            extract(epoch FROM now()-query_start)::integer AS query_age_seconds,
            left(ltrim(query),12) AS query_kind,pg_blocking_pids(pid) AS blocking_pids
            FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()
            AND (state<>'idle' OR cardinality(pg_blocking_pids(pid))>0)
            ORDER BY query_start LIMIT 20''').fetchall()
    client=await connect(settings)
    identity='company-turn-'+str(row['id'])
    handle=client.get_workflow_handle(identity)
    description=await handle.describe()
    history=await handle.fetch_history()
    activity_events=[]
    for event in history.events:
        if event.HasField('activity_task_scheduled_event_attributes'):
            a=event.activity_task_scheduled_event_attributes
            activity_events.append({'event_id':event.event_id,'type':'scheduled',
                'activity_id':a.activity_id,'activity_type':a.activity_type.name,
                'task_queue':a.task_queue.name,
                'start_to_close_seconds':a.start_to_close_timeout.seconds,
                'schedule_to_close_seconds':a.schedule_to_close_timeout.seconds})
        if event.HasField('activity_task_timed_out_event_attributes'):
            a=event.activity_task_timed_out_event_attributes
            activity_events.append({'event_id':event.event_id,'type':'timed_out',
                'scheduled_event_id':a.scheduled_event_id,'started_event_id':a.started_event_id,
                'timeout_type':a.failure.timeout_failure_info.timeout_type})
    pollers={}
    for kind,label in ((1,'workflow'),(2,'activity')):
        response=await client.workflow_service.describe_task_queue(DescribeTaskQueueRequest(
            namespace=client.namespace,task_queue=TaskQueue(name=description.task_queue),
            task_queue_type=kind))
        pollers[label]=[{'identity':p.identity,'last_access':p.last_access_time.ToJsonString()}
                       for p in response.pollers]
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
        'task_queue':description.task_queue,'pollers':pollers,'activity_events':activity_events,
        'recent_turns':turns,'database_waits':database_waits,
        'last_events':[{'id':e.event_id,'type':EventType.Name(e.event_type),
                        'at':e.event_time.ToJsonString()} for e in history.events[-20:]],
        'turn_id':str(row['id']),'turn_state':row['status'],'turn_sequence':row['sequence'],
        'request_frozen':row['request_frozen'],'selected_profile':row['profile'],
        'selected_account_revision':row['revision'],'received_account_signal_revisions':signal_revisions,
        'pending_timers':list(timers.values()),'recent_activity_results':activity_results[-4:],
        'database_mutated':False,'temporal_mutated':False},default=str))

asyncio.run(main())
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--turn-id', type=UUID)
    args = parser.parse_args()
    code = CODE
    if args.turn_id:
        code = code.replace('AND p.id=1', f"AND u.id='{args.turn_id}' AND p.id=1")
    completed = subprocess.run([
        "docker", "exec", "-i", "quant-company-worker-1", "python",
        "/app/entrypoint.py", "python", "-",
    ], input=code, capture_output=True, text=True, timeout=45)
    if completed.returncode:
        error = Path("/var/lib/quant-company/releases/second-result-audit-temporal-read-20261013.private-error.log")
        error.write_text(completed.stderr)
        error.chmod(0o600)
        raise RuntimeError("temporal_metadata_read_failed_check_private_host_receipt")
    result = json.loads(completed.stdout)
    result["observed_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
