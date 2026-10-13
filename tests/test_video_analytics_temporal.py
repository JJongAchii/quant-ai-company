"""Real local PostgreSQL and Temporal; YouTube reads and claude-runtime are simulated fixtures."""

import asyncio
from uuid import uuid4

import pytest
from temporalio.worker import Replayer

from quant_company.runtime import make_video_analytics_worker
from quant_company.video.analytics import AnalyticsRunner
from quant_company.video.analytics_workflow import VideoAnalyticsWorkflow

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
