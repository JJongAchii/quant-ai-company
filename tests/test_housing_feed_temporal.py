import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.worker import Replayer, Worker

from quant_company.housing_feed.workflow import HousingFeedWorkflow

from .test_housing_feed import collect, housing, outgoing  # noqa: F401
from .test_temporal import temporal_environment  # noqa: F401


@pytest.mark.integration
async def test_housing_commit_survives_temporal_retry_restart_and_replay(housing, temporal_environment):  # noqa: F811
    first, second = asyncio.Event(), asyncio.Event()
    calls = []

    @activity.defn(name="company_housing_feed_collect")
    async def tick():
        collect(housing)
        calls.append(1)
        if len(calls) == 1:
            first.set()
            raise RuntimeError("Simulated process loss after committed publication")
        second.set()
        return {"state": "collected"}

    queue = "housing-fixture-" + uuid4().hex
    client = temporal_environment.client
    args = dict(task_queue=queue, workflows=[HousingFeedWorkflow], activities=[tick],
                graceful_shutdown_timeout=timedelta(seconds=1))
    async with Worker(client, **args):
        handle = await client.start_workflow(HousingFeedWorkflow.run, id=queue, task_queue=queue)
        await asyncio.wait_for(first.wait(), 15)
    async with Worker(client, **args):
        await asyncio.wait_for(second.wait(), 20)
        async with asyncio.timeout(10):
            while True:
                history = await handle.fetch_history()
                timers = [e for e in history.events if e.HasField("timer_started_event_attributes")]
                if timers:
                    break
                await asyncio.sleep(0.1)
        assert timers[-1].timer_started_event_attributes.start_to_fire_timeout.seconds == 300
        await Replayer(workflows=[HousingFeedWorkflow]).replay_workflow(history)
        await handle.cancel()
    assert len(calls) == 2 and len(outgoing(housing)) == 1
