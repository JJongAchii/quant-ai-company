from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError


@workflow.defn
class CompanyTurnWorkflow:
    """Bounded inference turn. No credentials, prompt, or artifact bodies in workflow history."""

    @workflow.run
    async def run(self, turn_id: str) -> dict:
        for _ in range(48):
            try:
                result = await workflow.execute_activity(
                    "company_execute_turn", turn_id,
                    start_to_close_timeout=timedelta(minutes=8), heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(initial_interval=timedelta(seconds=2), maximum_attempts=3),
                )
            except ActivityError:
                await workflow.execute_activity("company_block_turn", turn_id,
                                                start_to_close_timeout=timedelta(seconds=30),
                                                retry_policy=RetryPolicy(maximum_attempts=3))
                return {"state": "blocked", "reason": "activity_retries_exhausted"}
            if result["state"] != "defer":
                return result
            await workflow.sleep(timedelta(seconds=max(1, result.get("seconds", 30))))
        workflow.continue_as_new(turn_id)
