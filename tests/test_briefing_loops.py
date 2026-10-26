"""Briefing loops (ADR 0049): the edition is written and reviewed in the news-model lane; numbers are frozen by the
standalone brief data loop."""

import asyncio
import contextlib
from datetime import timedelta

import pytest

from quant_company import runtime
from quant_company.briefing.data import BriefDataCollector
from quant_company.loops import attempt, run
from quant_company.runtime import brief_data_loops, news_loops

from .test_briefing import brief, definition, response, seed  # noqa: F401
from .test_briefing_quality import snapshot


def by_name(loops, name):
    return next(loop for loop in loops if loop.name == name)


@pytest.mark.integration
@pytest.mark.parametrize("with_plan", [False, True])
async def test_brief_editorial_loop_writes_and_reviews_the_edition(brief, with_plan):  # noqa: F811
    store, clock = brief
    edition = seed(brief)
    if with_plan:
        from psycopg.types.json import Jsonb

        from .test_briefing_planning import planning_bundle

        with store.db.transaction() as conn:
            conn.execute("UPDATE brief_editions SET bundle=%s WHERE id=%s", (Jsonb(planning_bundle()), edition.id))
    called = []

    class Provider:
        async def run(self, request):
            called.append(request.request_id)
            if request.request_id.endswith("-plan"):
                from .test_briefing_planning import plan

                return response(request.model_dump(), plan("source-1"))
            return response(request.model_dump())

    loop = by_name(news_loops(store.company, Provider()), "brief-editorial")
    # The request/runtime deadline owns the edition window; the loop's own limit never truncates it.
    assert loop.timeout == 87 * 60 and loop.lane is not None
    for _ in range(6):
        assert await attempt(loop.name, loop.tick, timeout=loop.timeout, lane=loop.lane) is not None
        with store.db.transaction() as conn:
            state = conn.execute("SELECT state FROM brief_editions WHERE id=%s", (edition.id,)).fetchone()["state"]
        if state == "ready":
            break
    assert state == "ready"
    phases = ["plan", "write", "review"] if with_plan else ["write", "review"]
    assert called == [f"news-brief-{edition.id}-{phase}" for phase in phases]
    clock["at"] = edition.due_at
    store.flush()
    assert store.status()["deliveries"][0]["status"] == "pending"


async def market_data(store, edition_id):
    async with asyncio.timeout(15):
        while True:
            with store.db.transaction() as conn:
                row = conn.execute("SELECT market_data FROM brief_editions WHERE id=%s", (edition_id,)).fetchone()
            if row and row["market_data"]:
                return row["market_data"]
            await asyncio.sleep(0.05)


@pytest.mark.integration
@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("kind", ["am", "pm"])
async def test_brief_data_loop_freezes_numbers_for_the_model_consumer(brief, monkeypatch, standalone, kind):  # noqa: F811
    store, clock = brief
    edition = definition(kind)
    clock["at"] = edition.cutoff - timedelta(minutes=10)
    reader = BriefDataCollector(store.company, lambda root, e: snapshot(e))
    loops = brief_data_loops(store.company, reader)
    assert [(loop.name, loop.timeout, loop.attempts, loop.wait(None)) for loop in loops] == [("brief-data", 90, 2, 30)]
    if standalone:
        def unrelated(*args, **kwargs):
            raise AssertionError("The standalone brief data process must not start unrelated loops")

        monkeypatch.setattr(runtime, "Company", lambda settings: store.company)
        monkeypatch.setattr(runtime, "brief_data_loops", lambda company: brief_data_loops(company, reader))
        for name in ("worker_loops", "news_loops", "data_watch_loops", "video_loops"):
            monkeypatch.setattr(runtime, name, unrelated)
        process = asyncio.create_task(runtime.brief_data_worker_main(store.company.settings))
    else:
        process = asyncio.create_task(run(loops[0]))
    try:
        await market_data(store, edition.id)
    finally:
        process.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await process
    seed(brief, edition)
    request = store.prepare()["request"]
    assert ('"data-kospi"' in request["prompt"]) == (kind == "am")
    assert ('"data-kosdaq"' in request["prompt"]) == (kind == "am")


@pytest.mark.integration
def test_no_brief_data_loop_without_the_briefing(company):
    company.settings.briefing_enabled = False
    assert brief_data_loops(company) == []
