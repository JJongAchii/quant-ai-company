from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from .schedule import next_publication


@workflow.defn
class TrendFeedCollectionWorkflow:
    @workflow.run
    async def run(self):
        for _ in range(100):
            try:
                await workflow.execute_activity("company_trend_feed_collect",
                    start_to_close_timeout=timedelta(minutes=9), heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5)))
            except ActivityError:
                workflow.logger.exception("Trend collection interrupted; durable inputs retained")
            await workflow.sleep(30)
        workflow.continue_as_new()


@workflow.defn
class TrendFeedEditorialWorkflow:
    @workflow.run
    async def run(self):
        for _ in range(100):
            try:
                await workflow.execute_activity("company_trend_feed_edit",
                    start_to_close_timeout=timedelta(minutes=8), heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5)))
            except ActivityError:
                workflow.logger.exception("Trend edit interrupted; no replacement for uncertain model work")
            await workflow.sleep(60)
        workflow.continue_as_new()


@workflow.defn
class TrendFeedDigestWorkflow:
    @workflow.run
    async def run(self):
        for _ in range(30):
            at = workflow.now()
            target = next_publication(at)
            if target > at:
                await workflow.sleep(target - at)
            try:
                await workflow.execute_activity("company_trend_feed_finalize",
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5)))
            except ActivityError:
                workflow.logger.exception("Trend publication interrupted; stable daily slot retained")
            # Poll during the catch-up hour, then sleep to the following morning.
            await workflow.sleep(60)
        workflow.continue_as_new()


@workflow.defn
class TrendFeedPublicationWorkflow:
    """Separate history from the legacy morning timer; handles due slots and owner requests."""

    @workflow.run
    async def run(self):
        for _ in range(100):
            try:
                await workflow.execute_activity("company_trend_feed_finalize",
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5)))
            except ActivityError:
                workflow.logger.exception("Trend publication interrupted; committed slot retained")
            await workflow.sleep(30)
        workflow.continue_as_new()
