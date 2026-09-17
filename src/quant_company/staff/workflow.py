from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError


@workflow.defn
class StaffDevelopmentWorkflow:
    """One durable low-priority loop; no prompts or answers in Temporal history."""

    @workflow.run
    async def run(self):
        for _ in range(100):
            try:
                result = await workflow.execute_activity(
                    "company_staff_tick", start_to_close_timeout=timedelta(minutes=8),
                    heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5)))
                delay = 3 if result["state"] in {"running", "completed"} else 60
            except ActivityError:
                workflow.logger.exception("Staff development tick failed; durable inputs retained")
                delay = 60
            await workflow.sleep(delay)
        workflow.continue_as_new()
