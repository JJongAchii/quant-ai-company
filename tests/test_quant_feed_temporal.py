import asyncio
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.worker import Replayer, Worker

from quant_company.quant_feed.workflow import (
    QuantFeedCollectionWorkflow,
    QuantFeedEditorialWorkflow,
    editorial_delay,
)
from quant_company.runtime import dispatch_once, make_quant_collector, make_quant_model_worker

from .test_quant_feed import brief, original, quant, response  # noqa: F401
from .test_temporal import temporal_environment  # noqa: F401


def test_quant_editorial_cadence_finishes_one_document_then_paces_candidates():
    assert editorial_delay({"state": "completed", "document_state": "ready"}) == 20
    assert editorial_delay({"state": "idle"}) == 300
    assert editorial_delay({"state": "defer"}) == 300
    assert editorial_delay({"state": "completed", "document_state": "held"}) == 1800
    assert editorial_delay({"state": "blocked"}) == 1800


@pytest.mark.integration
async def test_quant_frozen_request_survives_retry_restart_and_replay(quant, temporal_environment):  # noqa: F811
    original(quant)
    calls, complete = [], asyncio.Event()

    @activity.defn(name="company_quant_feed_review")
    async def review():
        ready = await asyncio.to_thread(quant.prepare)
        calls.append(ready)
        if len(calls) == 1:
            raise RuntimeError("Simulated crash after model request persisted")
        result = await asyncio.to_thread(quant.commit, response(ready, brief()))
        complete.set()
        return result

    client = temporal_environment.client
    queue = "quant-retry-" + uuid4().hex
    async with Worker(client, task_queue=queue, workflows=[QuantFeedEditorialWorkflow], activities=[review]):
        handle = await client.start_workflow(QuantFeedEditorialWorkflow.run, id=queue, task_queue=queue)
        await asyncio.wait_for(complete.wait(), 20)
    assert calls[0] == calls[1]
    await Replayer(workflows=[QuantFeedEditorialWorkflow]).replay_workflow(await handle.fetch_history())
    with quant.db.transaction() as conn:
        assert conn.execute("SELECT reserved FROM daily_usage").fetchone()["reserved"] == 1
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0
    # Restart consumes the next durable stage, not another editorial call.
    started = asyncio.Event()

    class Critic:
        async def run(self, request):
            from .test_quant_feed import critique

            assert "Critique the offered draft afresh" in request.prompt
            started.set()
            return response({"request": request.model_dump()}, critique())

    quant.company.settings.temporal_task_queue = "quant-restarted-" + uuid4().hex
    async with make_quant_model_worker(client, quant.company, Critic()):
        second = await client.start_workflow(QuantFeedEditorialWorkflow.run, id=queue + "-restart",
                                             task_queue=quant.company.settings.temporal_task_queue + "-quant-model")
        await asyncio.wait_for(started.wait(), 10)
        async with asyncio.timeout(10):
            while not quant.status()["deliveries"]:
                await asyncio.sleep(0.1)
        await second.cancel()
    await handle.cancel()
    assert len(quant.status()["deliveries"]) == 1


@pytest.mark.integration
async def test_quant_dispatch_queues_and_collection_history(quant, temporal_environment):  # noqa: F811
    client = temporal_environment.client
    quant.company.settings.temporal_task_queue = "quant-dispatch-" + uuid4().hex
    completed = asyncio.Event()

    class Collector:
        @activity.defn(name="company_quant_feed_collect")
        async def activity_tick(self):
            completed.set()
            return {"state": "synthetic-collection"}

    async with make_quant_collector(client, quant.company, Collector()):
        await dispatch_once(client, quant.company)
        await dispatch_once(client, quant.company)
        await asyncio.wait_for(completed.wait(), 10)
        collection = client.get_workflow_handle("company-quant-feed-collection-v1")
        review = client.get_workflow_handle("company-quant-feed-editorial-v1")
        assert (await review.describe()).task_queue.endswith("-quant-model")
        await Replayer(workflows=[QuantFeedCollectionWorkflow]).replay_workflow(await collection.fetch_history())
        await collection.cancel()
        await review.cancel()
