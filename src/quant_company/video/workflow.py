from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError


@workflow.defn
class VideoWorkflow:
    @workflow.run
    async def run(self):
        for _ in range(100):
            result = {}
            try:
                result = await workflow.execute_activity("company_video_tick",
                    start_to_close_timeout=timedelta(minutes=18), heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=2, initial_interval=timedelta(seconds=30)))
            except ActivityError:
                workflow.logger.error("Video worker interrupted; recover durable receipts before external effects")
            await workflow.sleep(2 if result.get("state") not in {None, "idle", "blocked", "uncertain"} else 30)
        workflow.continue_as_new()
