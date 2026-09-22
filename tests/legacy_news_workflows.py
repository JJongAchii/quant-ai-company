"""Pre-news-lane workflow definitions, kept only to produce real replay histories."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError


@workflow.defn(name="NewsEditorialWorkflow")
class LegacyNewsEditorialWorkflow:
    @workflow.run
    async def run(self):
        for _ in range(100):
            result = {}
            try:
                result = await workflow.execute_activity(
                    "company_news_review", start_to_close_timeout=timedelta(minutes=8),
                    heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5)))
            except ActivityError:
                workflow.logger.exception("News review interrupted; frozen request retained")
            delay = max(1, min(300, result.get("next_delay", 300))) if workflow.patched("news-editorial-schedule-v2") else 300
            await workflow.sleep(delay)
        workflow.continue_as_new()


@workflow.defn(name="NewsDiscoveryWorkflow")
class LegacyNewsDiscoveryWorkflow:
    @workflow.run
    async def run(self):
        for _ in range(100):
            try:
                await workflow.execute_activity(
                    "company_news_discover", start_to_close_timeout=timedelta(minutes=8),
                    heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5)))
            except ActivityError:
                workflow.logger.exception("News discovery interrupted; frozen request retained")
            await workflow.sleep(300)
        workflow.continue_as_new()
