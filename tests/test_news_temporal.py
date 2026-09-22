import asyncio
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.worker import Replayer, Worker

from quant_company.execution import FixtureProvider, TurnExecutor
from quant_company.news.store import NewsStore
from quant_company.news.workflow import NewsCollectionWorkflow, NewsDiscoveryWorkflow, NewsEditorialWorkflow
from quant_company.runtime import dispatch_once, make_news_collector, make_news_model_worker, make_worker

from .legacy_news_workflows import LegacyNewsDiscoveryWorkflow, LegacyNewsEditorialWorkflow
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

    queue = "news-search-recovery-"+uuid4().hex+"-news-model"
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

    queue = "news-recovery-"+uuid4().hex+"-news-model"
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
async def test_runtime_news_and_collection_continue_while_company_model_is_busy(news, temporal_environment):  # noqa: F811
    ready_article(news)
    entered, release, collected = asyncio.Event(), asyncio.Event(), asyncio.Event()
    company_entered = asyncio.Event()

    class Provider:
        async def run(self, request):
            if not request.request_id.startswith("news-"):
                company_entered.set()
                await release.wait()
                return await FixtureProvider().run(request)
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
    company.ingest(event_key="parallel-research", owner="UHUMAN", text="User research", agent="data")
    client = temporal_environment.client
    async with (make_worker(client, company, TurnExecutor(company, Provider())),
                make_news_model_worker(client, company, Provider()),
                make_news_collector(client, company, Collector())):
        try:
            await dispatch_once(client, company)
            await dispatch_once(client, company)
            await asyncio.wait_for(collected.wait(), timeout=15)
            await asyncio.wait_for(company_entered.wait(), timeout=15)
            assert entered.is_set() and company_entered.is_set() and not release.is_set()
        finally:
            release.set()
            with company.db.transaction() as conn:
                turn_id = str(conn.execute("SELECT id FROM turns").fetchone()["id"])
            await asyncio.wait_for(client.get_workflow_handle("company-turn-"+turn_id).result(), timeout=15)
            for identity in ("company-news-collection-v1", "company-news-editorial-v1"):
                await client.get_workflow_handle(identity).cancel()


@pytest.mark.integration
async def test_screening_fast_timer_survives_worker_restart(temporal_environment):  # noqa: F811
    calls = []
    completed = asyncio.Event()

    @activity.defn(name="company_news_review")
    async def review():
        calls.append(1)
        if len(calls) > 1:
            completed.set()
        return {"state": "completed", "next_delay": 2}

    client = temporal_environment.client
    queue = "news-screen-timer-"+uuid4().hex+"-news-model"
    async with Worker(client, task_queue=queue, workflows=[NewsEditorialWorkflow], activities=[review]):
        handle = await client.start_workflow(NewsEditorialWorkflow.run, id=queue, task_queue=queue)
        async with asyncio.timeout(10):
            while True:
                history = await handle.fetch_history()
                timers = [e.timer_started_event_attributes for e in history.events if e.HasField("timer_started_event_attributes")]
                if timers:
                    assert timers[-1].start_to_fire_timeout.seconds == 2
                    break
                await asyncio.sleep(0.1)
    async with Worker(client, task_queue=queue, workflows=[NewsEditorialWorkflow], activities=[review]):
        await asyncio.wait_for(completed.wait(), timeout=15)
        await Replayer(workflows=[NewsEditorialWorkflow]).replay_workflow(await handle.fetch_history())
        await handle.cancel()


@pytest.mark.integration
async def test_news_editor_and_search_share_one_slot_without_company_worker(discovery, temporal_environment):  # noqa: F811
    ready_article(discovery)
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls, active, peak = [], 0, 0

    class Provider:
        async def run(self, request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            calls.append(request.request_id)
            entered.set()
            try:
                await release.wait()
                return search_reply({"request": request.model_dump()}) if request.web_search else reply(request.model_dump())
            finally:
                active -= 1
                if len(calls) == 2:
                    finished.set()

    company, client = discovery.company, temporal_environment.client
    queue = "news-single-slot-"+uuid4().hex
    company.settings.temporal_task_queue = queue
    company.ingest(event_key="paused-research-worker", owner="UHUMAN", text="User research", agent="data")
    async with make_news_model_worker(client, company, Provider()):
        handles = [await client.start_workflow(kind.run, id=queue+suffix, task_queue=queue+"-news-model")
                   for kind, suffix in ((NewsEditorialWorkflow, "-editor"), (NewsDiscoveryWorkflow, "-search"))]
        try:
            await asyncio.wait_for(entered.wait(), timeout=15)
            async with asyncio.timeout(15):
                while True:
                    histories = [await handle.fetch_history() for handle in handles]
                    scheduled = [event.activity_task_scheduled_event_attributes.task_queue.name
                                 for history in histories for event in history.events
                                 if event.HasField("activity_task_scheduled_event_attributes")]
                    if len(scheduled) == 2:
                        assert scheduled == [queue+"-news-model"]*2
                        break
                    await asyncio.sleep(0.05)
            assert len(calls) == 1 and peak == 1
            release.set()
            await asyncio.wait_for(finished.wait(), timeout=15)
            assert len(calls) == 2 and peak == 1
        finally:
            release.set()
            for handle in handles:
                await handle.cancel()


@pytest.mark.integration
@pytest.mark.parametrize(("legacy", "current", "activity_name"), [
    (LegacyNewsEditorialWorkflow, NewsEditorialWorkflow, "company_news_review"),
    (LegacyNewsDiscoveryWorkflow, NewsDiscoveryWorkflow, "company_news_discover"),
])
async def test_pre_lane_workflow_history_still_replays(temporal_environment, legacy, current, activity_name):  # noqa: F811
    @activity.defn(name=activity_name)
    async def tick():
        return {"state": "idle"}

    queue, client = "news-legacy-"+uuid4().hex, temporal_environment.client
    async with Worker(client, task_queue=queue, workflows=[legacy], activities=[tick]):
        handle = await client.start_workflow(legacy.run, id=queue, task_queue=queue)
        try:
            async with asyncio.timeout(15):
                while True:
                    history = await handle.fetch_history()
                    if any(event.HasField("timer_started_event_attributes") for event in history.events):
                        break
                    await asyncio.sleep(0.05)
            await Replayer(workflows=[current]).replay_workflow(history)
        finally:
            await handle.cancel()


@pytest.mark.integration
async def test_running_legacy_editor_continues_same_workflow_id_on_news_queue(temporal_environment):  # noqa: F811
    calls = []
    advanced = asyncio.Event()

    @activity.defn(name="company_news_review")
    async def tick():
        calls.append(activity.info().task_queue)
        if len(calls) == 2:
            advanced.set()
        return {"state": "idle", "next_delay": 2}

    queue, client = "news-cutover-"+uuid4().hex, temporal_environment.client
    async with Worker(client, task_queue=queue, workflows=[LegacyNewsEditorialWorkflow], activities=[tick]):
        handle = await client.start_workflow(LegacyNewsEditorialWorkflow.run, id=queue, task_queue=queue)
        async with asyncio.timeout(15):
            while True:
                history = await handle.fetch_history()
                if any(event.HasField("timer_started_event_attributes") for event in history.events):
                    break
                await asyncio.sleep(0.05)
        original_run = (await handle.describe()).run_id
    async with (Worker(client, task_queue=queue, workflows=[NewsEditorialWorkflow], activities=[tick]),
                Worker(client, task_queue=queue+"-news-model", workflows=[NewsEditorialWorkflow],
                       activities=[tick], max_concurrent_activities=1)):
        try:
            await asyncio.wait_for(advanced.wait(), timeout=15)
            assert calls[:2] == [queue, queue+"-news-model"]
            current = client.get_workflow_handle(queue)
            assert (await current.describe()).run_id != original_run
            assert (await current.fetch_history()).events[0].workflow_execution_started_event_attributes.continued_execution_run_id == original_run
            await Replayer(workflows=[NewsEditorialWorkflow]).replay_workflow(await current.fetch_history())
            await Replayer(workflows=[NewsEditorialWorkflow]).replay_workflow(await handle.fetch_history())
        finally:
            await client.get_workflow_handle(queue).cancel()
