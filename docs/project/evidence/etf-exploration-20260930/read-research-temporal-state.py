"""Read pending activity identities/timing; never export workflow payloads."""

import json
import pathlib
import subprocess

code = r'''
import asyncio,json
from quant_company.config import Settings
from quant_company.runtime import connect
async def main():
    client=await connect(Settings())
    h=client.get_workflow_handle("company-turn-55aa649b-75b0-5c2c-8763-86990be2c3aa")
    d=await h.describe()
    history=await h.fetch_history()
    recent=[]
    for event in history.events:
        if event.HasField('activity_task_completed_event_attributes'):
            attrs=event.activity_task_completed_event_attributes
            values=await client.data_converter.decode(attrs.result.payloads)
            for value in values:
                if isinstance(value,dict):
                    recent.append({key:value[key] for key in ['state','reason','seconds'] if key in value})
    print(json.dumps({"workflow_id":d.id,"run_id":d.run_id,"status":d.status.name,
        "recent_activity_results":recent[-5:],
        "pending_activities":[{"id":x.activity_id,"type":x.activity_type.name,"state":x.state,
            "attempt":x.attempt,"scheduled_time":x.scheduled_time.ToJsonString(),
            "last_started_time":x.last_started_time.ToJsonString(),"last_failure_type":x.last_failure.application_failure_info.type}
            for x in d.raw_description.pending_activities]}))
asyncio.run(main())
'''
completed = subprocess.run(["docker", "exec", "-i", "quant-company-worker-1", "python", "/app/entrypoint.py", "python", "-"],
    input=code, capture_output=True, text=True, timeout=60)
if completed.returncode:
    private = pathlib.Path('/var/lib/quant-company/releases/research-temporal-read.private-error.log')
    private.write_text(completed.stderr)
    private.chmod(0o600)
    raise RuntimeError('temporal_metadata_read_failed_check_private_host_receipt')
print(json.dumps(json.loads(completed.stdout), indent=2))
