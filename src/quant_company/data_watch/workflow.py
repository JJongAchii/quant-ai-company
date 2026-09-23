from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError


@workflow.defn
class DataWatchWorkflow:
    @workflow.run
    async def run(self):
        for _ in range(100):
            try:
                await workflow.execute_activity("company_data_watch_tick", start_to_close_timeout=timedelta(minutes=2),
                                                retry_policy=RetryPolicy(maximum_attempts=3))
            except ActivityError:
                workflow.logger.error("Data watch tick failed; activity history preserves the failure")
            await workflow.sleep(60)
        workflow.continue_as_new()
