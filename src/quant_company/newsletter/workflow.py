from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError


@workflow.defn
class NewsletterWorkflow:
    """The email writer on its own queue in the video worker: a long render or a stuck script never delays it."""

    @workflow.run
    async def run(self):
        for _ in range(200):
            result = {}
            try:
                # One claude-runtime turn of at most a two-minute wait; the frozen request is asked again by its ID.
                result = await workflow.execute_activity(
                    "company_newsletter_tick", start_to_close_timeout=timedelta(minutes=10),
                    retry_policy=RetryPolicy(maximum_attempts=2, initial_interval=timedelta(seconds=30)))
            except ActivityError:
                workflow.logger.error("Newsletter tick interrupted; the next tick resumes from the database")
            await workflow.sleep(60 if (result or {}).get("state") in {None, "idle", "failed"} else 15)
        workflow.continue_as_new()
