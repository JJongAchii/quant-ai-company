"""Quant feed loops (ADR 0049): cadence, the quant-feed worker's two loops and a frozen request across a retry."""

import asyncio
from types import SimpleNamespace

import pytest

from quant_company.loops import attempt
from quant_company.quant_feed.runner import COLLECTION_DELAY_SECONDS, QuantFeedEditor, editorial_delay
from quant_company.runtime import quant_feed_loops

from .test_quant_feed import brief, original, quant, response  # noqa: F401


def test_quant_editorial_cadence_finishes_one_document_then_paces_candidates():
    assert editorial_delay({"state": "completed", "document_state": "ready"}) == 20
    assert editorial_delay({"state": "idle"}) == 300
    assert editorial_delay({"state": "defer"}) == 300
    assert editorial_delay({"state": "completed", "document_state": "held"}) == 1800
    assert editorial_delay({"state": "blocked"}) == 1800
    assert editorial_delay({"state": "held", "reason": "quant_context_limit"}) == 20


def test_a_disabled_quant_feed_has_no_loop():
    assert quant_feed_loops(SimpleNamespace(settings=SimpleNamespace(quant_feed_enabled=False))) == []


@pytest.mark.integration
def test_quant_feed_worker_collects_and_edits_on_its_own_cadence(quant):  # noqa: F811
    collection, editorial = quant_feed_loops(quant.company, provider=object())
    assert (collection.name, collection.timeout, collection.attempts) == ("quant-collection", 480, 3)
    assert collection.wait({"state": "collected"}) == COLLECTION_DELAY_SECONDS == 300
    assert (editorial.name, editorial.timeout, editorial.attempts) == ("quant-editorial", 1080, 3)
    assert editorial.wait({"state": "completed", "document_state": "ready"}) == 20
    # Every attempt failed: the editor waits like a finished candidate, never in a tight loop.
    assert editorial.wait(None) == 1800
    assert collection.lane is None and editorial.lane is None


@pytest.mark.integration
async def test_quant_frozen_request_survives_retry_and_restart(quant):  # noqa: F811
    original(quant)
    calls = []

    async def review():
        ready = await asyncio.to_thread(quant.prepare)
        calls.append(ready)
        if len(calls) == 1:
            raise RuntimeError("Simulated crash after model request persisted")
        return await asyncio.to_thread(quant.commit, response(ready, brief()))

    editorial = quant_feed_loops(quant.company, provider=object())[1]
    assert await attempt(editorial.name, review, timeout=editorial.timeout, attempts=editorial.attempts,
                         retry_seconds=0) is not None
    assert calls[0] == calls[1]
    with quant.db.transaction() as conn:
        assert conn.execute("SELECT reserved FROM daily_usage").fetchone()["reserved"] == 1
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0
    # A restarted process consumes the next durable stage, not another editorial call.
    started = asyncio.Event()

    class Critic:
        async def run(self, request):
            from .test_quant_feed import critique

            assert "offered draft afresh" in request.prompt
            started.set()
            return response({"request": request.model_dump()}, critique())

    restarted = QuantFeedEditor(quant.company, Critic())
    async with asyncio.timeout(10):
        while not quant.status()["deliveries"]:
            await restarted.tick()
    assert started.is_set() and len(quant.status()["deliveries"]) == 1
