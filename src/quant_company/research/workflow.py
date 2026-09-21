from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError


@workflow.defn
class ResearchWorkflow:
    """Persistent reconciliation, with no performance, model prompts or secrets in history."""

    @workflow.run
    async def run(self):
        for _ in range(100):
            try:
                result = await workflow.execute_activity(
                    "company_research_tick", start_to_close_timeout=timedelta(minutes=5),
                    heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5)))
                delay = 2 if result["state"] in {"completed", "awaiting_audit"} else 30
            except ActivityError:
                # Activity attempts are bounded, the durable job is not. Reconcile next cycle.
                delay = 60
            await workflow.sleep(delay)
        workflow.continue_as_new()
