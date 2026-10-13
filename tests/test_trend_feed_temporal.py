import asyncio
from datetime import UTC, timedelta
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.worker import Replayer, Worker

from quant_company.runtime import dispatch_once, make_trend_collector
from quant_company.trend_feed import schedule
from quant_company.trend_feed.runner import TrendFeedCollector
from quant_company.trend_feed.workflow import (
    TrendFeedCollectionWorkflow,
    TrendFeedDigestWorkflow,
    TrendFeedPublicationWorkflow,
)

from .test_temporal import temporal_environment  # noqa: F401
from .test_trend_feed import ingest, morning, outgoing, trend  # noqa: F401
from .test_trend_feed_recurring import clock, complete_request, recurring


@pytest.mark.integration
async def test_request_publisher_commit_survives_restart_and_replays(trend, temporal_environment):  # noqa: F811
    recurring(trend)
    clock(trend, 10)
    await complete_request(trend, "temporal-owner-request", finalize=False)
    committed, retried = asyncio.Event(), asyncio.Event()
    attempts = []

    @activity.defn(name="company_trend_feed_finalize")
    async def finalize():
        attempts.append(1)
        result = trend.finalize()
        if len(attempts) == 1:
            committed.set()
            raise RuntimeError("Simulated crash after PostgreSQL/outbox commit")
        retried.set()
        return result

    queue = "trend-publication-restart-" + uuid4().hex
    client = temporal_environment.client
    args = {"task_queue": queue, "workflows": [TrendFeedPublicationWorkflow], "activities": [finalize],
            "graceful_shutdown_timeout": timedelta(seconds=1)}
    async with Worker(client, **args):
        handle = await client.start_workflow(TrendFeedPublicationWorkflow.run, id=queue, task_queue=queue)
        await asyncio.wait_for(committed.wait(), 15)
    async with Worker(client, **args):
        await asyncio.wait_for(retried.wait(), 20)
        async with asyncio.timeout(10):
            while True:
                history = await handle.fetch_history()
                if any(e.HasField("timer_started_event_attributes") for e in history.events):
                    break
                await asyncio.sleep(0.1)
        await Replayer(workflows=[TrendFeedPublicationWorkflow]).replay_workflow(history)
        await handle.cancel()
    assert len(outgoing(trend)) == 1 and len(attempts) == 2


@pytest.mark.integration
async def test_runtime_starts_new_publisher_without_replacing_legacy_history(trend, temporal_environment):  # noqa: F811
    recurring(trend)
    trend.company.settings.temporal_task_queue = "trend-publication-runtime-" + uuid4().hex
    client = temporal_environment.client
    collector = TrendFeedCollector(trend.company, fetcher=lambda *_: {"ok": True, "entries": []})
    async with make_trend_collector(client, trend.company, collector):
        assert await dispatch_once(client, trend.company) == 0
        assert await dispatch_once(client, trend.company) == 0
        handle = client.get_workflow_handle("company-trend-feed-publication-v2")
        async with asyncio.timeout(15):
            while True:
                history = await handle.fetch_history()
                if any(e.HasField("timer_started_event_attributes") for e in history.events):
                    break
                await asyncio.sleep(0.1)
        await Replayer(workflows=[TrendFeedPublicationWorkflow]).replay_workflow(history)
        for identity in ("company-trend-feed-collection-v1", "company-trend-feed-editorial-v1",
                         "company-trend-feed-publication-v2"):
            await client.get_workflow_handle(identity).cancel()
    assert not outgoing(trend)


@pytest.mark.integration
async def test_committed_digest_retry_restart_and_history_replay(trend, temporal_environment, monkeypatch):  # noqa: F811
    ingest(trend)
    morning(trend)
    trend.freeze()
    trend.clock[0] = schedule.times(trend.clock[0])[1].astimezone(UTC)
    monkeypatch.setattr(schedule, "next_publication", lambda at: at)
    committed, retried = asyncio.Event(), asyncio.Event()
    attempts = []

    @activity.defn(name="company_trend_feed_finalize")
    async def finalize():
        attempts.append(1)
        result = trend.finalize()
        if len(attempts) == 1:
            committed.set()
            raise RuntimeError("Simulated crash after PostgreSQL/outbox commit")
        retried.set()
        return result

    queue = "trend-restart-" + uuid4().hex
    client = temporal_environment.client
    args = {"task_queue": queue, "workflows": [TrendFeedDigestWorkflow], "activities": [finalize],
            "graceful_shutdown_timeout": timedelta(seconds=1)}
    async with Worker(client, **args):
        handle = await client.start_workflow(TrendFeedDigestWorkflow.run, id=queue, task_queue=queue)
        await asyncio.wait_for(committed.wait(), 15)
    async with Worker(client, **args):
        await asyncio.wait_for(retried.wait(), 20)
        async with asyncio.timeout(10):
            while True:
                history = await handle.fetch_history()
                if any(e.HasField("timer_started_event_attributes") for e in history.events):
                    break
                await asyncio.sleep(0.1)
        await Replayer(workflows=[TrendFeedDigestWorkflow]).replay_workflow(history)
        await handle.cancel()
    assert len(outgoing(trend)) == 1 and len(attempts) == 2


@pytest.mark.integration
async def test_runtime_starts_isolated_workflows_and_collection_replays(trend, temporal_environment):  # noqa: F811
    trend.company.settings.temporal_task_queue = "trend-runtime-" + uuid4().hex
    client = temporal_environment.client
    collector = TrendFeedCollector(trend.company, fetcher=lambda *_: {"ok": True, "entries": []})
    async with make_trend_collector(client, trend.company, collector):
        assert await dispatch_once(client, trend.company) == 0
        assert await dispatch_once(client, trend.company) == 0
        handle = client.get_workflow_handle("company-trend-feed-collection-v1")
        async with asyncio.timeout(15):
            while True:
                history = await handle.fetch_history()
                if any(e.HasField("timer_started_event_attributes") for e in history.events):
                    break
                await asyncio.sleep(0.1)
        await Replayer(workflows=[TrendFeedCollectionWorkflow]).replay_workflow(history)
        for identity in ("collection", "digest", "editorial"):
            await client.get_workflow_handle(f"company-trend-feed-{identity}-v1").cancel()
    with trend.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM turns").fetchone()["n"] == 0
    assert not outgoing(trend)


@pytest.mark.integration
async def test_publisher_runs_while_enrichment_is_waiting(trend, temporal_environment, monkeypatch):  # noqa: F811
    ingest(trend)
    morning(trend)
    trend.freeze()
    trend.clock[0] = schedule.times(trend.clock[0])[1].astimezone(UTC)
    monkeypatch.setattr(schedule, "next_publication", lambda at: at)
    waiting, release, published = asyncio.Event(), asyncio.Event(), asyncio.Event()

    @activity.defn(name="company_trend_feed_collect")
    async def slow_collection():
        waiting.set()
        await release.wait()
        return {"state": "collected"}

    @activity.defn(name="company_trend_feed_finalize")
    async def finalize():
        result = trend.finalize()
        published.set()
        return result

    queue = "trend-independence-" + uuid4().hex
    client = temporal_environment.client
    async with Worker(client, task_queue=queue, workflows=[TrendFeedCollectionWorkflow, TrendFeedDigestWorkflow],
                      activities=[slow_collection, finalize], max_concurrent_activities=2):
        collection = await client.start_workflow(TrendFeedCollectionWorkflow.run, id=queue + "-collect", task_queue=queue)
        await asyncio.wait_for(waiting.wait(), 10)
        digest = await client.start_workflow(TrendFeedDigestWorkflow.run, id=queue + "-digest", task_queue=queue)
        await asyncio.wait_for(published.wait(), 10)
        assert len(outgoing(trend)) == 1 and "주제 분류를 확인하지 못해" in trend.preview()["text"]
        release.set()
        await collection.cancel()
        await digest.cancel()
