from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

TICK = timedelta(minutes=30)
# Right after cards went out: their full diagnosis follows in the thread once Slack has confirmed them.
FOLLOW_UP = timedelta(minutes=2)
# A question waits this long between tries while an episode is in production (about seven hours in all).
QUESTION_WAIT, QUESTION_TRIES = timedelta(minutes=5), 84


@workflow.defn
class VideoAnalyticsWorkflow:
    """Every 30 minutes on its own queue, so a long video render never holds up the reads and posts."""

    @workflow.run
    async def run(self):
        for _ in range(200):
            result = None
            try:
                # Up to a few claude-runtime waits of two minutes each; every stage is a read or keyed by a stable ID.
                result = await workflow.execute_activity(
                    "company_video_analytics_tick", start_to_close_timeout=timedelta(minutes=30),
                    retry_policy=RetryPolicy(maximum_attempts=2, initial_interval=timedelta(seconds=30)))
            except ActivityError:
                workflow.logger.error("Video analytics tick failed; the next tick resumes from the database")
            # Histories from before the follow-up tick replay with the plain 30 minutes.
            cards = result.get("cards") if isinstance(result, dict) else None
            soon = workflow.patched("follow-up-tick") and isinstance(cards, int) and cards > 0
            await workflow.sleep(FOLLOW_UP if soon else TICK)
        workflow.continue_as_new()


@workflow.defn
class VideoQuestionWorkflow:
    """One owner question in an analytics thread, answered on the analytics queue. While an episode is in production
    (or claude-runtime is busy) it asks again every few minutes; after about seven hours the thread is told."""

    @workflow.run
    async def run(self, question_id: str) -> str:
        for _ in range(QUESTION_TRIES):
            try:
                state = await workflow.execute_activity(
                    "company_video_analytics_answer", question_id, start_to_close_timeout=timedelta(minutes=30),
                    retry_policy=RetryPolicy(maximum_attempts=2, initial_interval=timedelta(seconds=30)))
            except ActivityError:
                state = "retry"
            if state not in {"waiting", "retry"}:
                return state
            await workflow.sleep(QUESTION_WAIT)
        return await workflow.execute_activity(
            "company_video_analytics_give_up", question_id, start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=30)))
