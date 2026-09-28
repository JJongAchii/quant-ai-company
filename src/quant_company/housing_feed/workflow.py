from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError


@workflow.defn
class HousingFeedWorkflow:
    @workflow.run
    async def run(self):
        for _ in range(100):
            try:
                await workflow.execute_activity("company_housing_feed_collect",
                    start_to_close_timeout=timedelta(minutes=12),
                    retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5)))
            except ActivityError:
                workflow.logger.exception("Housing feed interrupted; durable checkpoints retained")
            await workflow.sleep(300)
        workflow.continue_as_new()
