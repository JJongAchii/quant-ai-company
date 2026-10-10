"""Real local PostgreSQL and Temporal; the adaptation model is a synthetic fixture."""

import asyncio
from uuid import uuid4

import pytest
from temporalio.worker import Replayer

from quant_company.runtime import make_video_worker
from quant_company.video.runner import VideoRunner
from quant_company.video.workflow import VideoWorkflow

from .test_temporal import temporal_environment  # noqa: F401
from .test_video import SimulatedProvider, brief, new_job, video  # noqa: F401


@pytest.mark.integration
async def test_video_receipts_survive_worker_restart_and_history_replay(brief, video, temporal_environment):  # noqa: F811
    store, _ = video
    job = new_job(brief, video)
    store.settings.temporal_task_queue = 'video-'+uuid4().hex
    provider = SimulatedProvider()
    client = temporal_environment.client
    runner = VideoRunner(store.company, provider=provider)
    async with make_video_worker(client, store.company, runner):
        handle = await client.start_workflow(VideoWorkflow.run, id=store.settings.temporal_task_queue,
            task_queue=store.settings.temporal_task_queue+'-video')
        async with asyncio.timeout(15):
            while store.get(job['id'])['state'] == 'queued':
                await asyncio.sleep(.1)
    # Simulate stopping the lane at an operator checkpoint; restart must not repeat the committed plan.
    store.settings.video_enabled = False
    async with make_video_worker(client, store.company, VideoRunner(store.company, provider=provider)):
        try:
            await asyncio.sleep(3)
            history = await handle.fetch_history()
            await Replayer(workflows=[VideoWorkflow]).replay_workflow(history)
            assert len(provider.calls) == 1
            with store.db.transaction() as conn:
                assert conn.execute("SELECT count(*) AS n FROM video_effects WHERE job_id=%s AND state='completed'",
                                    (job['id'],)).fetchone()['n'] == 1
        finally:
            await handle.cancel()
