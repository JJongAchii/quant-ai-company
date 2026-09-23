from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError


@workflow.defn
class CompanyTurnWorkflow:
    """Durable inference turn. No credentials, prompt, or artifact bodies in workflow history."""

    def __init__(self):
        self.account_revision = 0

    @workflow.signal
    def model_account_changed(self, revision: int):
        self.account_revision = max(self.account_revision, revision)

    @workflow.run
    async def run(self, turn_id: str) -> dict:
        for attempt in range(48):
            revision = self.account_revision
            try:
                result = await workflow.execute_activity(
                    "company_execute_turn", turn_id,
                    start_to_close_timeout=timedelta(minutes=20), heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(initial_interval=timedelta(seconds=2), maximum_attempts=3),
                )
            except ActivityError:
                # Transient infrastructure failures do not exhaust a scientific task.
                # The next iteration reconciles the same durable request after backoff.
                await workflow.sleep(timedelta(seconds=60))
                continue
            if result["state"] != "defer":
                return result
            delay = timedelta(seconds=max(1, result.get("seconds", 30)))
            if workflow.patched(f"owner-account-wake-v1-{attempt}"):
                try:
                    await workflow.wait_condition(lambda previous=revision: self.account_revision > previous, timeout=delay)
                except TimeoutError:
                    pass
            else:
                await workflow.sleep(delay)
        workflow.continue_as_new(turn_id)
