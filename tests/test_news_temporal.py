import asyncio
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.worker import Replayer, Worker

from quant_company.execution import TurnExecutor
from quant_company.news.store import NewsStore
from quant_company.news.workflow import NewsCollectionWorkflow, NewsDiscoveryWorkflow, NewsEditorialWorkflow
from quant_company.runtime import dispatch_once, make_news_collector, make_worker

from .test_news import news, ready_article, reply  # noqa: F401
from .test_news_scope import discovery, search_reply  # noqa: F401
from .test_temporal import temporal_environment  # noqa: F401


@pytest.mark.integration
async def test_news_search_recovers_frozen_request_without_repeated_spending(discovery, temporal_environment):  # noqa: F811
    seen = []
    completed = asyncio.Event()

    @activity.defn(name="company_news_discover")
    async def discover():
        ready = await asyncio.to_thread(discovery.prepare)
        seen.append(ready)
        if len(seen) == 1:
            raise RuntimeError("simulated worker loss after search freeze")
        result = await asyncio.to_thread(discovery.commit, search_reply(ready))
        completed.set()
        return result

    queue = "news-search-recovery-"+uuid4().hex
    async with Worker(temporal_environment.client, task_queue=queue, workflows=[NewsDiscoveryWorkflow], activities=[discover]):
        handle = await temporal_environment.client.start_workflow(NewsDiscoveryWorkflow.run, id=queue, task_queue=queue)
        await asyncio.wait_for(completed.wait(), timeout=20)
        assert seen[0]["request"] == seen[1]["request"]
        await Replayer(workflows=[NewsDiscoveryWorkflow]).replay_workflow(await handle.fetch_history())
        await handle.cancel()
    with discovery.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM news_searches").fetchone()["n"] == 1
        assert conn.execute("SELECT reserved FROM daily_usage").fetchone()["reserved"] == 1
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0


@pytest.mark.integration
async def test_news_frozen_review_recovers_and_temporal_history_replays(news, temporal_environment):  # noqa: F811
    ready_article(news)
    seen = []

    @activity.defn(name="company_news_review")
    async def review():
        ready = await asyncio.to_thread(NewsStore(news.company).prepare_review)
        seen.append(ready)
        if len(seen) == 1:
            raise RuntimeError("simulated worker loss after request freeze")
        if ready["state"] == "ready":
            return await asyncio.to_thread(news.commit_review, reply(ready["request"]))
        return ready

    queue = "news-recovery-"+uuid4().hex
    async with Worker(temporal_environment.client, task_queue=queue, workflows=[NewsEditorialWorkflow], activities=[review]):
        handle = await temporal_environment.client.start_workflow(NewsEditorialWorkflow.run, id=queue, task_queue=queue)
        async with asyncio.timeout(30):
            while True:
                with news.db.transaction() as conn:
                    done = conn.execute("SELECT 1 FROM news_reviews WHERE state='completed'").fetchone()
                if done:
                    break
                await asyncio.sleep(0.1)
        assert seen[0]["request"] == seen[1]["request"]
        history = await handle.fetch_history()
        await Replayer(workflows=[NewsEditorialWorkflow]).replay_workflow(history)
        await handle.cancel()
    with news.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM news_reviews").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 1


@pytest.mark.integration
async def test_collection_timer_survives_worker_restart(temporal_environment):  # noqa: F811
    calls = []
    completed = asyncio.Event()

    @activity.defn(name="company_news_collect")
    async def collect():
        calls.append(1)
        if len(calls) > 1:
            completed.set()
        return {"state": "collected"}

    client = temporal_environment.client
    queue = "news-collector-"+uuid4().hex
    async with Worker(client, task_queue=queue, workflows=[NewsCollectionWorkflow], activities=[collect]):
        handle = await client.start_workflow(NewsCollectionWorkflow.run, id=queue, task_queue=queue)
        async with asyncio.timeout(10):
            while True:
                history = await handle.fetch_history()
                if any(e.HasField("timer_started_event_attributes") for e in history.events):
                    break
                await asyncio.sleep(0.1)
    async with Worker(client, task_queue=queue, workflows=[NewsCollectionWorkflow], activities=[collect]):
        await asyncio.wait_for(completed.wait(), timeout=15)
        await Replayer(workflows=[NewsCollectionWorkflow]).replay_workflow(await handle.fetch_history())
        await handle.cancel()


@pytest.mark.integration
async def test_runtime_collects_while_model_is_busy(news, temporal_environment):  # noqa: F811
    ready_article(news)
    entered, release, collected = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Provider:
        async def run(self, request):
            entered.set()
            await release.wait()
            return reply(request.model_dump())

    class Collector:
        @activity.defn(name="company_news_collect")
        async def activity_tick(self):
            await entered.wait()
            collected.set()
            return {"state": "idle"}

    company = news.company
    company.settings.temporal_task_queue = "news-runtime-" + uuid4().hex
    client = temporal_environment.client
    async with (make_worker(client, company, TurnExecutor(company, Provider())),
                make_news_collector(client, company, Collector())):
        try:
            await dispatch_once(client, company)
            await dispatch_once(client, company)
            await asyncio.wait_for(collected.wait(), timeout=15)
            assert entered.is_set() and not release.is_set()
        finally:
            release.set()
            for identity in ("company-news-collection-v1", "company-news-editorial-v1"):
                await client.get_workflow_handle(identity).cancel()
