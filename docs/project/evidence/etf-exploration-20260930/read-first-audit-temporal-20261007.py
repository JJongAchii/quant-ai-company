"""Read main queue pollers and fixed workflow activity metadata; omit payloads."""

import json
import pathlib
import subprocess

code = r'''
import asyncio
import hashlib
import json
import re
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from quant_company.config import Settings
from quant_company.runtime import connect

async def main():
    settings = Settings()
    client = await connect(settings)
    result = {"task_queue": settings.temporal_task_queue, "workflows": [], "pollers": {}}
    for identity in ["company-turn-8520ba60-39ff-52e3-8af4-bb3a02ffcad0",
        "company-turn-e2988906-53c9-532b-acad-4afa31c0ca98", "company-staff-development-v1"]:
        description = await client.get_workflow_handle(identity).describe()
        history = await client.get_workflow_handle(identity).fetch_history()
        events = []
        for event in history.events[-15:]:
            item = {"id": event.event_id, "type": event.event_type, "at": event.event_time.ToJsonString()}
            if event.HasField("workflow_task_scheduled_event_attributes"):
                item["task_queue"] = event.workflow_task_scheduled_event_attributes.task_queue.name
            if event.HasField("workflow_task_started_event_attributes"):
                item["worker_identity"] = event.workflow_task_started_event_attributes.identity
            if event.HasField("activity_task_completed_event_attributes"):
                values = await client.data_converter.decode(event.activity_task_completed_event_attributes.result.payloads)
                item["activity_results"] = [{key: value[key] for key in ["state", "reason", "seconds"] if key in value}
                    for value in values if isinstance(value, dict)]
            if event.HasField("workflow_task_failed_event_attributes"):
                attributes = event.workflow_task_failed_event_attributes
                item["failure_cause"] = attributes.cause
                failure = attributes.failure
                chain = []
                while failure.message or failure.stack_trace:
                    chain.append({"type": failure.application_failure_info.type,
                        "message_sha256": hashlib.sha256(failure.message.encode()).hexdigest(),
                        "source_frames": re.findall(r'File "(/app/src/quant_company/[^"\n]+)", line (\d+)', failure.stack_trace),
                        "quoted_identifiers": [v for v in re.findall(r"'([A-Za-z_][A-Za-z0-9_.]*)'", failure.message)
                            if len(v) < 80]})
                    if not failure.HasField("cause"):
                        break
                    failure = failure.cause
                item["failure"] = chain
            events.append(item)
        result["workflows"].append({"id": identity, "status": description.status.name,
            "type": description.workflow_type, "task_queue": description.task_queue,
            "recent_history": events,
            "pending_activities": [{"id": x.activity_id, "type": x.activity_type.name,
                "state": x.state, "attempt": x.attempt,
                "scheduled_at": x.scheduled_time.ToJsonString(),
                "last_started_at": x.last_started_time.ToJsonString(),
                "last_failure_type": x.last_failure.application_failure_info.type,
                "last_failure_message_sha256": hashlib.sha256(x.last_failure.message.encode()).hexdigest()}
                for x in description.raw_description.pending_activities]})
    for kind in [TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW, TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY]:
        queue = await client.workflow_service.describe_task_queue(DescribeTaskQueueRequest(
            namespace=settings.temporal_namespace, task_queue=TaskQueue(name=settings.temporal_task_queue),
            task_queue_type=kind))
        result["pollers"][str(kind)] = [{"identity": p.identity, "last_access_at": p.last_access_time.ToJsonString()}
            for p in queue.pollers]
    print(json.dumps(result))
asyncio.run(main())
'''
completed = subprocess.run(["docker", "exec", "-i", "quant-company-worker-1", "python",
    "/app/entrypoint.py", "python", "-"], input=code, capture_output=True, text=True, timeout=60)
if completed.returncode:
    error = pathlib.Path("/var/lib/quant-company/releases/first-audit-temporal-read-20261007.private-error.log")
    error.write_text(completed.stderr)
    error.chmod(0o600)
    raise RuntimeError("temporal_metadata_read_failed_check_private_host_receipt")
print(json.dumps(json.loads(completed.stdout), indent=2))
