import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.worker import Replayer, Worker

from quant_company.runtime import dispatch_once, make_tech_feed_collector
from quant_company.tech_feed.runner import TechFeedCollector
from quant_company.tech_feed.workflow import TechFeedCollectionWorkflow

from .test_tech_feed import outgoing, queue, rss, tech  # noqa: F401
from .test_temporal import temporal_environment  # noqa: F401


@pytest.mark.integration
async def test_committed_publication_survives_activity_retry_and_worker_restart(tech, temporal_environment):  # noqa: F811
    first, retried = asyncio.Event(), asyncio.Event()
    calls = []

    @activity.defn(name="company_tech_feed_collect")
    async def collect():
        calls.append(1)
        queue(tech)
        if len(calls) == 1:
            first.set()
            raise RuntimeError("Simulated worker loss after database commit")
        retried.set()
        return {"state": "collected"}

    task_queue = "tech-feed-test-" + uuid4().hex
    client = temporal_environment.client
    args = {"task_queue": task_queue, "workflows": [TechFeedCollectionWorkflow], "activities": [collect],
            "graceful_shutdown_timeout": timedelta(seconds=1)}
    async with Worker(client, **args):
        handle = await client.start_workflow(TechFeedCollectionWorkflow.run, id=task_queue, task_queue=task_queue)
        await asyncio.wait_for(first.wait(), 15)
    async with Worker(client, **args):
        await asyncio.wait_for(retried.wait(), 20)
        async with asyncio.timeout(10):
            while True:
                history = await handle.fetch_history()
                timers = [e for e in history.events if e.HasField("timer_started_event_attributes")]
                if timers:
                    break
                await asyncio.sleep(0.1)
        assert timers[-1].timer_started_event_attributes.start_to_fire_timeout.seconds == 600
        await Replayer(workflows=[TechFeedCollectionWorkflow]).replay_workflow(history)
        await handle.cancel()
    assert len(calls) == 2 and len(outgoing(tech)) == 1
    with tech.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM turns").fetchone()["n"] == 0


@pytest.mark.integration
async def test_dispatch_starts_dedicated_collection_without_model_worker(tech, temporal_environment, monkeypatch):  # noqa: F811
    def forbidden(*args, **kwargs):
        pytest.fail("No model provider belongs in the tech feed worker")

    monkeypatch.setattr("quant_company.execution.provider_for", forbidden)
    from quant_company.tech_feed.feeds import parse_feed

    collector = TechFeedCollector(tech.company, lambda source, *_: {
        "ok": True, "entries": parse_feed(rss(tech.clock[0]), source)[0]})
    tech.company.settings.temporal_task_queue = "tech-feed-runtime-" + uuid4().hex
    client = temporal_environment.client
    async with make_tech_feed_collector(client, tech.company, collector):
        assert await dispatch_once(client, tech.company) == 0
        assert await dispatch_once(client, tech.company) == 0
        async with asyncio.timeout(15):
            while True:
                if tech.status()["sources"] and tech.status()["sources"][0]["initialized_at"]:
                    break
                await asyncio.sleep(0.1)
        await client.get_workflow_handle("company-tech-feed-collection-v1").cancel()
    assert outgoing(tech) == []


@pytest.mark.integration
async def test_dedicated_worker_has_its_own_queue_and_no_model_calls(tech, temporal_environment, monkeypatch):  # noqa: F811
    from quant_company.tech_feed.worker import make_worker

    monkeypatch.setattr("quant_company.execution.provider_for", lambda *_: pytest.fail("Model construction forbidden"))
    tech.company.settings.temporal_task_queue = "tech-feed-isolated-" + uuid4().hex
    tech.company.settings.tech_feed_dedicated_worker = True
    collector = TechFeedCollector(tech.company, lambda *_: {"ok": True, "entries": []})
    client = temporal_environment.client
    async with make_worker(client, tech.company, collector):
        await dispatch_once(client, tech.company)
        handle = client.get_workflow_handle("company-tech-feed-collection-v1")
        async with asyncio.timeout(15):
            while not tech.status()["sources"]:
                await asyncio.sleep(0.1)
        assert (await handle.describe()).task_queue.endswith("-tech-feed-dedicated")
        await handle.cancel()
    with tech.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM turns").fetchone()["n"] == 0
