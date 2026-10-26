"""Trend feed loops (ADR 0049): a committed publication is never posted twice after a retry or a restart, and the
publisher keeps its own slot while enrichment waits."""

import asyncio
from datetime import UTC

import pytest

from quant_company.loops import attempt, run
from quant_company.runtime import news_loops
from quant_company.trend_feed import schedule
from quant_company.trend_feed.runner import TrendFeedCollector

from .test_trend_feed import ingest, morning, outgoing, trend  # noqa: F401
from .test_trend_feed_recurring import clock, complete_request, recurring


def by_name(loops, name):
    return next(loop for loop in loops if loop.name == name)


def crash_after_commit(tick, calls):
    async def finalize():
        calls.append(1)
        result = await tick()
        if len(calls) == 1:
            raise RuntimeError("Simulated crash after PostgreSQL/outbox commit")
        return result
    return finalize


@pytest.mark.integration
async def test_owner_request_publication_is_posted_once_after_a_retry(trend):  # noqa: F811
    recurring(trend)
    clock(trend, 10)
    await complete_request(trend, "loop-owner-request", finalize=False)
    loop = by_name(news_loops(trend.company), "trend-publication")
    calls = []
    result = await attempt(loop.name, crash_after_commit(loop.tick, calls), timeout=loop.timeout,
                           attempts=loop.attempts, retry_seconds=0, lane=loop.lane)
    assert result is not None and len(calls) == 2
    assert len(outgoing(trend)) == 1


@pytest.mark.integration
async def test_committed_digest_is_posted_once_after_a_restart(trend):  # noqa: F811
    ingest(trend)
    morning(trend)
    trend.freeze()
    trend.clock[0] = schedule.times(trend.clock[0])[1].astimezone(UTC)
    calls = []
    lost = by_name(news_loops(trend.company), "trend-digest")
    assert await attempt(lost.name, crash_after_commit(lost.tick, calls), timeout=lost.timeout,
                         lane=lost.lane) is None
    assert len(outgoing(trend)) == 1
    restarted = by_name(news_loops(trend.company), "trend-digest")
    result = await attempt(restarted.name, crash_after_commit(restarted.tick, calls), timeout=restarted.timeout,
                           lane=restarted.lane)
    assert result is not None and len(calls) == 2
    assert len(outgoing(trend)) == 1
    assert 60 <= restarted.wait(result) <= 60 + 24 * 3600  # digest_wait: catch-up minute or the next morning


@pytest.mark.integration
async def test_recurring_runtime_runs_the_publisher_and_posts_nothing_without_a_due_slot(trend):  # noqa: F811
    recurring(trend)
    loops = news_loops(trend.company)
    assert [loop.name for loop in loops] == ["trend-collection", "trend-publication", "trend-editorial"]
    publication = by_name(loops, "trend-publication")
    assert await attempt(publication.name, publication.tick, timeout=publication.timeout,
                         lane=publication.lane) is not None
    assert not outgoing(trend)


@pytest.mark.integration
async def test_trend_collection_runs_without_company_model_work(trend):  # noqa: F811
    loops = news_loops(trend.company)
    assert [loop.name for loop in loops] == ["trend-collection", "trend-digest", "trend-editorial"]
    collection = by_name(loops, "trend-collection")
    collector = TrendFeedCollector(trend.company, fetcher=lambda *_: {"ok": True, "entries": []})
    assert await attempt(collection.name, collector.tick, timeout=collection.timeout,
                         lane=collection.lane) is not None
    with trend.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM turns").fetchone()["n"] == 0
    assert not outgoing(trend)


@pytest.mark.integration
async def test_publisher_runs_while_enrichment_is_waiting(trend):  # noqa: F811
    ingest(trend)
    morning(trend)
    trend.freeze()
    trend.clock[0] = schedule.times(trend.clock[0])[1].astimezone(UTC)
    waiting, release = asyncio.Event(), asyncio.Event()

    async def slow_collection():
        waiting.set()
        await release.wait()
        return {"state": "collected"}

    loops = news_loops(trend.company)
    collection, digest = by_name(loops, "trend-collection"), by_name(loops, "trend-digest")
    assert collection.lane is digest.lane
    collection.tick = slow_collection
    task = asyncio.create_task(run(collection))
    try:
        await asyncio.wait_for(waiting.wait(), 10)
        result = await asyncio.wait_for(attempt(digest.name, digest.tick, timeout=digest.timeout,
                                                lane=digest.lane), 10)
        assert result is not None
        assert len(outgoing(trend)) == 1 and "주제 분류를 확인하지 못해" in trend.preview()["text"]
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
