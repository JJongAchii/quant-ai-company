import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.worker import Replayer, Worker

from quant_company.data_watch.runner import DataWatchRunner
from quant_company.data_watch.workflow import DataWatchWorkflow
from quant_company.runtime import dispatch_once, make_data_watch_worker

from .test_data_watch import catalog, descriptor, rows, tick, watch  # noqa: F401
from .test_temporal import temporal_environment  # noqa: F401


@pytest.mark.integration
async def test_data_watch_retry_restart_replay_keeps_one_committed_summary(watch, temporal_environment):  # noqa: F811
    first, retried = asyncio.Event(), asyncio.Event()
    calls = []

    @activity.defn(name="company_data_watch_tick")
    async def collect():
        calls.append(1)
        await tick(watch)
        if len(calls) == 1:
            first.set()
            raise RuntimeError("Synthetic worker loss after database commit")
        retried.set()
        return {"state": "reported"}

    queue = "data-watch-test-" + uuid4().hex
    client = temporal_environment.client
    args = dict(task_queue=queue, workflows=[DataWatchWorkflow], activities=[collect],
                graceful_shutdown_timeout=timedelta(seconds=1))
    async with Worker(client, **args):
        handle = await client.start_workflow(DataWatchWorkflow.run, id=queue, task_queue=queue)
        await asyncio.wait_for(first.wait(), 15)
    async with Worker(client, **args):
        await asyncio.wait_for(retried.wait(), 20)
        async with asyncio.timeout(10):
            while True:
                history = await handle.fetch_history()
                timers = [e for e in history.events if e.HasField("timer_started_event_attributes")]
                if timers:
                    break
                await asyncio.sleep(.1)
        assert timers[-1].timer_started_event_attributes.start_to_fire_timeout.seconds == 60
        await Replayer(workflows=[DataWatchWorkflow]).replay_workflow(history)
        await handle.cancel()
    assert len(rows(watch, "outbox")) == len(rows(watch, "data_watch_inventory")) == 1
    assert not rows(watch, "turns")


@pytest.mark.integration
async def test_runtime_dispatches_data_watch_without_model_capacity(watch, temporal_environment, monkeypatch):  # noqa: F811
    monkeypatch.setattr("quant_company.execution.provider_for", lambda *_: pytest.fail("model called"))
    data = catalog(watch)
    runner = DataWatchRunner(watch.company, lambda _, name, args:
                            data if name == "lake_catalog" else descriptor(data["data"]["datasets"][0]))
    watch.company.settings.temporal_task_queue = "data-watch-runtime-" + uuid4().hex
    client = temporal_environment.client
    async with make_data_watch_worker(client, watch.company, runner):
        assert await dispatch_once(client, watch.company) == 0
        assert await dispatch_once(client, watch.company) == 0
        async with asyncio.timeout(15):
            while not rows(watch, "outbox"):
                await asyncio.sleep(.1)
        await client.get_workflow_handle("company-data-watch-v1").cancel()
    assert not rows(watch, "turns")
