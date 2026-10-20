"""Real local PostgreSQL and Temporal; YouTube reads and claude-runtime are simulated fixtures."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from quant_company.runtime import make_video_analytics_worker, start_video_questions
from quant_company.video.analytics import AnalyticsRunner
from quant_company.video.analytics_workflow import VideoAnalyticsWorkflow, VideoQuestionWorkflow

from .test_briefing import brief  # noqa: F401
from .test_temporal import temporal_environment  # noqa: F401
from .test_video import video  # noqa: F401
from .test_video_analytics_runner import SimulatedClient, SimulatedProvider
from .test_video_analytics_store import analytics  # noqa: F401


@pytest.mark.integration
async def test_analytics_ticks_on_its_own_queue_and_replays(analytics, temporal_environment):  # noqa: F811
    analytics.settings.temporal_task_queue = "analytics-" + uuid4().hex
    youtube = SimulatedClient()
    runner = AnalyticsRunner(analytics.company, client=youtube, provider=SimulatedProvider())
    client = temporal_environment.client
    async with make_video_analytics_worker(client, analytics.company, runner):
        handle = await client.start_workflow(VideoAnalyticsWorkflow.run, id=analytics.settings.temporal_task_queue,
                                             task_queue=analytics.settings.temporal_task_queue + "-video-analytics")
        async with asyncio.timeout(15):
            while len(youtube.created) < 2:
                await asyncio.sleep(0.1)
        history = await handle.fetch_history()
        await Replayer(workflows=[VideoAnalyticsWorkflow]).replay_workflow(history)
        await handle.terminate()
    assert youtube.created == ["channel_reach_basic_a1", "channel_reach_combined_a1"]


@pytest.mark.integration
async def test_the_running_production_history_replays_on_the_new_workflow():
    # Recorded from the workflow as released on 2026-10-15 (one tick, then the 30-minute timer).
    history = WorkflowHistory.from_json(
        "company-video-analytics-v1", Path(__file__).with_name("fixtures").joinpath(
            "video_analytics_workflow_tick_history.json").read_text())
    await Replayer(workflows=[VideoAnalyticsWorkflow]).replay_workflow(history)


@pytest.mark.integration
async def test_a_question_started_by_dispatch_is_answered_on_the_analytics_queue(brief, video, analytics,  # noqa: F811
                                                                                 temporal_environment):  # noqa: F811
    from .test_video_analytics_questions import AnsweringProvider, card_delivered, reply
    from .test_video_analytics_runner import outbox

    analytics.settings.temporal_task_queue = "analytics-" + uuid4().hex
    provider = AnsweringProvider()
    _, runner = await card_delivered(brief, video, analytics, provider)
    question = reply(analytics)["video_question"]
    client = temporal_environment.client
    async with make_video_analytics_worker(client, analytics.company, runner):
        assert await start_video_questions(client, analytics.company) == 1
        assert await start_video_questions(client, analytics.company) == 0  # started once
        handle = client.get_workflow_handle(f"company-video-question-{question}")
        async with asyncio.timeout(15):
            assert await handle.result() == "answered"
        await Replayer(workflows=[VideoQuestionWorkflow]).replay_workflow(await handle.fetch_history())
    assert outbox(analytics)[-1]["thread_ts"] == "300.001"


class PostingRunner(AnalyticsRunner):
    def __init__(self, company, cards):
        super().__init__(company, client=SimulatedClient(), provider=SimulatedProvider())
        self.cards = cards

    async def tick(self, at=None):
        return {"state": "ok", "cards": self.cards}


@pytest.mark.integration
@pytest.mark.parametrize("cards,seconds", [(2, 120), (0, 1800), ("failed:HTTPError", 1800)])
async def test_the_next_tick_comes_soon_after_cards_went_out(analytics, temporal_environment, cards, seconds):  # noqa: F811
    analytics.settings.temporal_task_queue = "analytics-" + uuid4().hex
    client = temporal_environment.client
    async with make_video_analytics_worker(client, analytics.company, PostingRunner(analytics.company, cards)):
        handle = await client.start_workflow(VideoAnalyticsWorkflow.run, id=analytics.settings.temporal_task_queue,
                                             task_queue=analytics.settings.temporal_task_queue + "-video-analytics")
        async with asyncio.timeout(15):
            while True:
                timers = [e.timer_started_event_attributes for e in (await handle.fetch_history()).events
                          if e.HasField("timer_started_event_attributes")]
                if timers:
                    break
                await asyncio.sleep(0.1)
        await handle.terminate()
    assert timers[0].start_to_fire_timeout.ToSeconds() == seconds
