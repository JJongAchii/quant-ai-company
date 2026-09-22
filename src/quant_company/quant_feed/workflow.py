from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError


@workflow.defn
class QuantFeedCollectionWorkflow:
    @workflow.run
    async def run(self):
        for _ in range(100):
            try:
                await workflow.execute_activity("company_quant_feed_collect", start_to_close_timeout=timedelta(minutes=8),
                                                retry_policy=RetryPolicy(maximum_attempts=3))
            except ActivityError:
                workflow.logger.exception("Quant source interrupted; durable source leases retained")
            await workflow.sleep(15)
        workflow.continue_as_new()


@workflow.defn
class QuantFeedEditorialWorkflow:
    @workflow.run
    async def run(self):
        for _ in range(100):
            try:
                await workflow.execute_activity("company_quant_feed_review", start_to_close_timeout=timedelta(minutes=18),
                                                heartbeat_timeout=timedelta(seconds=45),
                                                retry_policy=RetryPolicy(maximum_attempts=3))
            except ActivityError:
                workflow.logger.exception("Quant review interrupted; frozen request ID retained")
            await workflow.sleep(20)
        workflow.continue_as_new()
