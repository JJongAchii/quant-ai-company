"""News-worker loops (ADR 0049): wiring, waits, the shared model lane and frozen-request recovery."""

import asyncio
from datetime import datetime, timedelta

import pytest

from quant_company.execution import FixtureProvider, TurnExecutor
from quant_company.loops import attempt, run
from quant_company.runtime import digest_wait, news_loops, worker_loops
from quant_company.trend_feed import schedule

from .test_news import news, ready_article, reply  # noqa: F401
from .test_news_scope import discovery, search_reply  # noqa: F401


def by_name(loops, name):
    return next(loop for loop in loops if loop.name == name)


def flags(company, **values):
    settings = company.settings
    settings.company_news_enabled = settings.news_search_enabled = False
    settings.briefing_enabled = settings.trend_feed_enabled = False
    settings.trend_feed_publication_hours, settings.trend_feed_on_demand_enabled = [8], False
    for key, value in values.items():
        setattr(settings, key, value)
    return [loop.name for loop in news_loops(company)]


@pytest.mark.integration
def test_news_worker_runs_a_loop_only_for_each_enabled_feature(company):
    assert flags(company) == []
    assert flags(company, company_news_enabled=True) == ["news-collection", "news-editorial"]
    assert flags(company, company_news_enabled=True, news_search_enabled=True) == [
        "news-collection", "news-editorial", "news-discovery"]
    assert flags(company, news_search_enabled=True) == []  # search belongs to the news feature
    assert flags(company, briefing_enabled=True) == ["brief-collection", "brief-editorial"]
    assert flags(company, trend_feed_enabled=True) == ["trend-collection", "trend-digest", "trend-editorial"]
    assert flags(company, trend_feed_enabled=True, trend_feed_publication_hours=[8, 14, 20]) == [
        "trend-collection", "trend-publication", "trend-editorial"]
    assert flags(company, trend_feed_enabled=True, trend_feed_on_demand_enabled=True) == [
        "trend-collection", "trend-publication", "trend-editorial"]


@pytest.mark.integration
async def test_every_news_worker_model_call_shares_one_lane_and_collectors_do_not(company):
    flags(company, company_news_enabled=True, news_search_enabled=True, briefing_enabled=True,
          trend_feed_enabled=True, trend_feed_publication_hours=[8, 14, 20])
    loops = news_loops(company)
    model = by_name(loops, "news-editorial").lane
    assert all(by_name(loops, name).lane is model
               for name in ("news-discovery", "brief-editorial", "trend-editorial"))
    assert by_name(loops, "news-collection").lane is None and by_name(loops, "brief-collection").lane is None
    collection = by_name(loops, "trend-collection").lane
    assert by_name(loops, "trend-publication").lane is collection and collection is not model
    await model.acquire()
    assert model.locked()  # one model call at a time, as the news-model queue's worker ran
    await collection.acquire()
    assert not collection.locked()  # collection and publication may run side by side
    await collection.acquire()
    assert collection.locked()


@pytest.mark.integration
def test_news_worker_waits_timeouts_and_retries_match_the_former_workflows(company):
    flags(company, company_news_enabled=True, news_search_enabled=True, briefing_enabled=True,
          trend_feed_enabled=True, trend_feed_publication_hours=[8, 14, 20])
    loops = news_loops(company)
    collection = by_name(loops, "news-collection")
    assert [collection.wait(result) for result in (
        {"state": "collected"}, {"state": "idle", "article_fetched": True}, {"state": "idle"}, None)] == [2, 2, 30, 60]
    editorial = by_name(loops, "news-editorial")
    assert [editorial.wait(result) for result in (
        {"state": "completed", "next_delay": 2}, {"next_delay": 0}, {"next_delay": 900}, {}, None)] == [2, 1, 300, 300, 300]
    brief = by_name(loops, "brief-editorial")
    assert [brief.wait(result) for result in ({"state": "completed"}, {"state": "waiting"}, None)] == [1, 30, 30]
    assert {loop.name: loop.wait({"state": "idle"}) for loop in loops if loop.name not in {
        "news-collection", "news-editorial", "brief-editorial"}} == {
        "news-discovery": 300, "brief-collection": 30, "trend-collection": 30, "trend-publication": 30,
        "trend-editorial": 60}
    assert {loop.name: (loop.timeout, loop.attempts) for loop in loops} == {
        "news-collection": (120, 3), "news-editorial": (480, 3), "news-discovery": (480, 3),
        "brief-collection": (300, 3), "brief-editorial": (87 * 60, 3), "trend-collection": (540, 3),
        "trend-publication": (60, 3), "trend-editorial": (480, 3)}


def test_the_legacy_trend_digest_polls_its_catch_up_hour_then_waits_for_the_next_morning():
    kst = schedule.KST
    before = datetime(2026, 10, 7, 7, 0, tzinfo=kst)
    assert digest_wait(None, before) == 60 + 59 * 60  # until the 08:00 slot opens
    assert digest_wait({"state": "queued"}, datetime(2026, 10, 7, 8, 10, tzinfo=kst)) == 60  # inside 08:00-09:00
    after = datetime(2026, 10, 7, 10, 0, tzinfo=kst)
    expected = schedule.next_publication(after + timedelta(seconds=60)) - (after + timedelta(seconds=60))
    assert digest_wait(None, after) == 60 + expected.total_seconds() == 22 * 3600


class LostOnce:
    """The first call is lost with its worker after the request was frozen; later calls answer."""

    def __init__(self, answer):
        self.answer, self.requests = answer, []

    async def run(self, request):
        self.requests.append(request.model_dump())
        if len(self.requests) == 1:
            raise RuntimeError("simulated worker loss after request freeze")
        return self.answer(request)


@pytest.mark.integration
async def test_news_search_retry_reuses_the_frozen_request_without_spending_again(discovery):  # noqa: F811
    provider = LostOnce(lambda request: search_reply({"request": request.model_dump()}))
    loop = by_name(news_loops(discovery.company, provider), "news-discovery")
    assert loop.attempts == 3
    result = await attempt(loop.name, loop.tick, timeout=loop.timeout, attempts=loop.attempts, retry_seconds=0,
                           lane=loop.lane)
    assert result["state"] == "completed"
    assert len(provider.requests) == 2 and provider.requests[0] == provider.requests[1]
    with discovery.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM news_searches").fetchone()["n"] == 1
        assert conn.execute("SELECT reserved FROM daily_usage").fetchone()["reserved"] == 1
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0


@pytest.mark.integration
async def test_a_restarted_news_worker_commits_the_frozen_review_once(news):  # noqa: F811
    ready_article(news)
    lost = LostOnce(lambda request: pytest.fail("the lost worker never answers"))
    first = by_name(news_loops(news.company, lost), "news-editorial")
    assert await attempt(first.name, first.tick, timeout=first.timeout, lane=first.lane) is None
    answered = LostOnce(lambda request: reply(request.model_dump()))
    answered.requests.append("lost before restart")  # the restarted process answers on its first call
    restarted = by_name(news_loops(news.company, answered), "news-editorial")
    result = await attempt(restarted.name, restarted.tick, timeout=restarted.timeout, lane=restarted.lane)
    assert result is not None
    assert lost.requests[0] == answered.requests[1]
    with news.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM news_reviews WHERE state='completed'").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM news_reviews").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 1


async def stop(*tasks):
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.integration
async def test_news_editor_and_search_take_turns_in_one_model_slot(discovery):  # noqa: F811
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

    loops = news_loops(discovery.company, Provider())
    tasks = [asyncio.create_task(run(by_name(loops, name))) for name in ("news-editorial", "news-discovery")]
    try:
        await asyncio.wait_for(entered.wait(), timeout=15)
        await asyncio.sleep(0.3)
        assert len(calls) == 1 and peak == 1
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=15)
        assert len(calls) == 2 and peak == 1
    finally:
        release.set()
        await stop(*tasks)


@pytest.mark.integration
async def test_news_and_collection_continue_while_a_company_turn_holds_the_model(news):  # noqa: F811
    ready_article(news)
    entered, release, collected, company_entered = asyncio.Event(), asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Provider:
        async def run(self, request):
            if not request.request_id.startswith("news-"):
                company_entered.set()
                await release.wait()
                return await FixtureProvider().run(request)
            entered.set()
            await release.wait()
            return reply(request.model_dump())

    async def collect():
        await entered.wait()
        collected.set()
        return {"state": "idle"}

    company = news.company
    result = company.ingest(event_key="parallel-research", owner="UHUMAN", text="User research", agent="data")
    loops = news_loops(company, Provider())
    by_name(loops, "news-collection").tick = collect
    worker, _ = worker_loops(company, TurnExecutor(company, Provider()))
    tasks = [asyncio.create_task(run(loop)) for loop in (*worker, *loops)]
    try:
        await asyncio.wait_for(collected.wait(), timeout=15)
        await asyncio.wait_for(company_entered.wait(), timeout=15)
        assert entered.is_set() and not release.is_set()
        release.set()
        async with asyncio.timeout(15):
            while company.project_state(result["project_id"])["tasks"][0]["status"] != "completed":
                await asyncio.sleep(0.05)
    finally:
        release.set()
        await stop(*tasks)
