from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError


@workflow.defn
class TechFeedCollectionWorkflow:
    @workflow.run
    async def run(self):
        for _ in range(100):
            try:
                await workflow.execute_activity(
                    "company_tech_feed_collect", start_to_close_timeout=timedelta(minutes=8),
                    retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5)))
            except ActivityError:
                workflow.logger.exception("Tech feed collection interrupted; durable checkpoints retained")
            await workflow.sleep(600)
        workflow.continue_as_new()
