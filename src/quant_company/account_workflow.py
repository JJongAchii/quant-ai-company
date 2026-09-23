from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError


@workflow.defn
class AccountControlWorkflow:
    """Dedicated queue: owner commands must not wait behind model inference."""

    @workflow.run
    async def run(self):
        for _ in range(500):
            try:
                await workflow.execute_activity("company_accounts_tick",
                    start_to_close_timeout=timedelta(seconds=60),
                    retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=2)))
            except ActivityError:
                workflow.logger.warning("Account control tick failed; pending commands retained")
            await workflow.sleep(2)
        workflow.continue_as_new()
