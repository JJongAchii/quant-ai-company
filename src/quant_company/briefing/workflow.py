from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError


@workflow.defn
class BriefDataWorkflow:
    @workflow.run
    async def run(self):
        for _ in range(100):
            try:
                await workflow.execute_activity("company_brief_data", start_to_close_timeout=timedelta(seconds=90),
                    retry_policy=RetryPolicy(maximum_attempts=2, initial_interval=timedelta(seconds=5)))
            except ActivityError:
                workflow.logger.exception("Collected market data unavailable; keep explicit coverage gaps")
            await workflow.sleep(30)
        workflow.continue_as_new()


@workflow.defn
class BriefCollectionWorkflow:
    @workflow.run
    async def run(self):
        for _ in range(100):
            try:
                await workflow.execute_activity("company_brief_collect", start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5)))
            except ActivityError:
                workflow.logger.exception("Brief collection unavailable; deadline handling remains in dispatcher")
            await workflow.sleep(30)
        workflow.continue_as_new()


@workflow.defn
class BriefEditorialWorkflow:
    @workflow.run
    async def run(self):
        for _ in range(100):
            result = {}
            try:
                minutes = 26 if workflow.patched("analyst-phase-budget-v1") else 18
                if workflow.patched('analyst-shared-deadline-v1'):
                    # The request/runtime deadline owns the actual remaining
                    # edition window; this outer timer must not truncate it.
                    minutes = 87
                result = await workflow.execute_activity("company_brief_review", start_to_close_timeout=timedelta(minutes=minutes),
                    heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5)))
            except ActivityError:
                workflow.logger.exception("Brief model interrupted; frozen request and receipts retained")
            await workflow.sleep(1 if result.get("state") == "completed" else 30)
        workflow.continue_as_new()
