"""Tech feed loop (ADR 0049): one committed publication across a retry, no model, dedicated or shared worker."""

from types import SimpleNamespace

import pytest

from quant_company.loops import attempt
from quant_company.runtime import tech_feed_loop, worker_loops
from quant_company.tech_feed import worker
from quant_company.tech_feed.runner import TechFeedCollector

from .test_tech_feed import outgoing, queue, rss, tech  # noqa: F401


def turns(store):
    with store.db.transaction() as conn:
        return conn.execute("SELECT count(*) AS n FROM turns").fetchone()["n"]


@pytest.mark.integration
async def test_committed_publication_survives_a_retry_after_the_commit(tech):  # noqa: F811
    calls = []

    async def collect():
        calls.append(1)
        queue(tech)
        if len(calls) == 1:
            raise RuntimeError("Simulated worker loss after database commit")
        return {"state": "collected"}

    loop = tech_feed_loop(tech.company)
    result = await attempt(loop.name, collect, timeout=loop.timeout, attempts=loop.attempts, retry_seconds=0)
    assert loop.wait(result) == 600 and (loop.timeout, loop.attempts) == (480, 3)
    assert len(calls) == 2 and len(outgoing(tech)) == 1
    assert turns(tech) == 0


@pytest.mark.integration
async def test_the_collector_runs_without_any_model_provider(tech, monkeypatch):  # noqa: F811
    monkeypatch.setattr("quant_company.execution.provider_for",
                        lambda *_: pytest.fail("No model provider belongs in the tech feed worker"))
    from quant_company.tech_feed.feeds import parse_feed

    collector = TechFeedCollector(tech.company, lambda source, *_: {
        "ok": True, "entries": parse_feed(rss(tech.clock[0]), source)[0]})
    loop = tech_feed_loop(tech.company, collector)
    await attempt(loop.name, loop.tick, timeout=loop.timeout)
    assert tech.status()["sources"] and tech.status()["sources"][0]["initialized_at"]
    assert outgoing(tech) == [] and turns(tech) == 0
    # Building the dedicated worker's loops constructs no provider either.
    tech.company.settings.tech_feed_dedicated_worker = True
    assert [loop.name for loop in worker.loops(tech.company)] == ["tech-feed"]


@pytest.mark.integration
def test_the_setting_chooses_the_dedicated_worker_or_the_shared_one(tech):  # noqa: F811
    executor = SimpleNamespace(provider=None)
    tech.company.settings.tech_feed_dedicated_worker = True
    assert [loop.name for loop in worker_loops(tech.company, executor)[0]] == ["turns"]
    assert [loop.name for loop in worker.loops(tech.company)] == ["tech-feed"]
    tech.company.settings.tech_feed_dedicated_worker = False
    assert [loop.name for loop in worker_loops(tech.company, executor)[0]] == ["turns", "tech-feed"]
    assert worker.loops(tech.company) == []  # never two collectors at once
    tech.company.settings.tech_feed_dedicated_worker = True
    tech.company.settings.tech_feed_enabled = False
    assert worker.loops(tech.company) == []
    assert [loop.name for loop in worker_loops(tech.company, executor)[0]] == ["turns"]
