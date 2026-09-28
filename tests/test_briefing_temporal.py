import asyncio
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.worker import Replayer, Worker

from quant_company.briefing.workflow import BriefCollectionWorkflow, BriefEditorialWorkflow
from quant_company.runtime import make_news_model_worker

from .test_briefing import brief, response, seed  # noqa: F401
from .test_temporal import temporal_environment  # noqa: F401


@pytest.mark.integration
async def test_real_temporal_postgres_brief_write_review_and_replay(brief, temporal_environment):  # noqa: F811
    store, clock = brief
    edition = seed(brief)
    store.company.settings.temporal_task_queue = "brief-runtime-"+uuid4().hex
    called = []

    class Provider:
        async def run(self, request):
            called.append(request.request_id)
            return response(request.model_dump())

    client = temporal_environment.client
    async with make_news_model_worker(client, store.company, Provider()):
        handle = await client.start_workflow(BriefEditorialWorkflow.run, id="brief-test-"+uuid4().hex,
            task_queue=store.company.settings.temporal_task_queue+"-news-model")
        try:
            async with asyncio.timeout(25):
                while True:
                    with store.db.transaction() as conn:
                        state = conn.execute("SELECT state FROM brief_editions WHERE id=%s", (edition.id,)).fetchone()["state"]
                    if state == "ready":
                        break
                    await asyncio.sleep(0.1)
            assert called == [f"news-brief-{edition.id}-write", f"news-brief-{edition.id}-review"]
            clock["at"] = edition.due_at
            store.flush()
            assert store.status()["deliveries"][0]["status"] == "pending"
            history = await handle.fetch_history()
            before = len(called)
            await Replayer(workflows=[BriefEditorialWorkflow]).replay_workflow(history)
            assert len(called) == before
        finally:
            await handle.cancel()


@pytest.mark.integration
async def test_brief_collection_timer_survives_worker_restart(temporal_environment):  # noqa: F811
    called = []
    resumed = asyncio.Event()

    @activity.defn(name="company_brief_collect")
    async def collect():
        called.append(1)
        if len(called) > 1:
            resumed.set()
        return {"state": "idle"}

    client = temporal_environment.client
    queue = "brief-timer-"+uuid4().hex
    async with Worker(client, task_queue=queue, workflows=[BriefCollectionWorkflow], activities=[collect]):
        handle = await client.start_workflow(BriefCollectionWorkflow.run, id=queue, task_queue=queue)
        async with asyncio.timeout(10):
            while True:
                history = await handle.fetch_history()
                if any(e.HasField("timer_started_event_attributes") for e in history.events):
                    break
                await asyncio.sleep(0.1)
    async with Worker(client, task_queue=queue, workflows=[BriefCollectionWorkflow], activities=[collect]):
        try:
            await asyncio.wait_for(resumed.wait(), timeout=40)
            await Replayer(workflows=[BriefCollectionWorkflow]).replay_workflow(await handle.fetch_history())
        finally:
            await handle.cancel()


@pytest.mark.integration
async def test_real_temporal_data_worker_freezes_numbers_for_model_consumer(brief, temporal_environment):  # noqa: F811
    from datetime import timedelta

    from quant_company.briefing.data import BriefDataCollector
    from quant_company.briefing.workflow import BriefDataWorkflow
    from quant_company.runtime import make_brief_data_worker

    from .test_briefing import definition
    from .test_briefing_quality import snapshot

    store, clock = brief
    edition = definition("pm")
    clock["at"] = edition.cutoff-timedelta(minutes=10)
    store.company.settings.temporal_task_queue = "brief-data-test-"+uuid4().hex
    reader = BriefDataCollector(store.company, lambda root, e: snapshot(e))
    client = temporal_environment.client
    queue = store.company.settings.temporal_task_queue+"-brief-data"
    async with make_brief_data_worker(client, store.company, reader):
        handle = await client.start_workflow(BriefDataWorkflow.run, id=queue, task_queue=queue)
        try:
            async with asyncio.timeout(15):
                while True:
                    with store.db.transaction() as conn:
                        row = conn.execute("SELECT market_data FROM brief_editions WHERE id=%s", (edition.id,)).fetchone()
                    if row and row["market_data"]:
                        break
                    await asyncio.sleep(0.1)
            seed(brief, edition)
            request = store.prepare()["request"]
            assert '"data-kospi"' in request["prompt"] and '"data-kosdaq"' in request["prompt"]
            await Replayer(workflows=[BriefDataWorkflow]).replay_workflow(await handle.fetch_history())
        finally:
            await handle.cancel()
