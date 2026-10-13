from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError


@workflow.defn
class VideoAnalyticsWorkflow:
    """Every 30 minutes on its own queue, so a long video render never holds up the reads and posts."""

    @workflow.run
    async def run(self):
        for _ in range(200):
            try:
                # Up to a few claude-runtime waits of two minutes each; every stage is a read or keyed by a stable ID.
                await workflow.execute_activity("company_video_analytics_tick", start_to_close_timeout=timedelta(minutes=30),
                                                retry_policy=RetryPolicy(maximum_attempts=2,
                                                                         initial_interval=timedelta(seconds=30)))
            except ActivityError:
                workflow.logger.error("Video analytics tick failed; the next tick resumes from the database")
            await workflow.sleep(timedelta(minutes=30))
        workflow.continue_as_new()
