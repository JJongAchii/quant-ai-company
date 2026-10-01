from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

COLLECTION_DELAY_SECONDS = 300
EDITORIAL_IDLE_DELAY_SECONDS = 300
EDITORIAL_CANDIDATE_DELAY_SECONDS = 1800
EDITORIAL_FOLLOWUP_DELAY_SECONDS = 20


def editorial_delay(result):
    if result.get("state") == "held" and result.get("reason") == "quant_context_limit":
        return EDITORIAL_FOLLOWUP_DELAY_SECONDS
    if result.get("document_state") == "ready":
        return EDITORIAL_FOLLOWUP_DELAY_SECONDS
    if result.get("state") in {"idle", "defer"}:
        return EDITORIAL_IDLE_DELAY_SECONDS
    return EDITORIAL_CANDIDATE_DELAY_SECONDS


@workflow.defn
class QuantFeedCollectionWorkflow:
    @workflow.run
    async def run(self):
        for tick in range(100):
            try:
                await workflow.execute_activity("company_quant_feed_collect", start_to_close_timeout=timedelta(minutes=8),
                                                retry_policy=RetryPolicy(maximum_attempts=3))
            except ActivityError:
                workflow.logger.exception("Quant source interrupted; durable source leases retained")
            delay = (COLLECTION_DELAY_SECONDS
                     if workflow.patched(f"quant-collection-sustainable-cadence-v1-{tick}") else 15)
            await workflow.sleep(delay)
        workflow.continue_as_new()


@workflow.defn
class QuantFeedEditorialWorkflow:
    @workflow.run
    async def run(self):
        for tick in range(100):
            result = {}
            try:
                result = await workflow.execute_activity(
                    "company_quant_feed_review", start_to_close_timeout=timedelta(minutes=18),
                    heartbeat_timeout=timedelta(seconds=45), retry_policy=RetryPolicy(maximum_attempts=3))
            except ActivityError:
                workflow.logger.exception("Quant review interrupted; frozen request ID retained")
            delay = (editorial_delay(result)
                     if workflow.patched(f"quant-editorial-sustainable-cadence-v1-{tick}") else 20)
            await workflow.sleep(delay)
        workflow.continue_as_new()
