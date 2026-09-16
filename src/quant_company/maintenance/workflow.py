from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError


@workflow.defn
class MaintenanceWorkflow:
    @workflow.run
    async def run(self, poll_seconds: int):
        for _ in range(100):
            try:
                await workflow.execute_activity(
                    "company_maintenance_tick", start_to_close_timeout=timedelta(minutes=10),
                    retry_policy=RetryPolicy(maximum_attempts=1))
            except ActivityError:
                # A DB/worker outage is recorded in history. Resume the same durable job on the next tick.
                workflow.logger.exception("Maintenance tick failed; durable job retained")
            await workflow.sleep(poll_seconds)
        workflow.continue_as_new(poll_seconds)
