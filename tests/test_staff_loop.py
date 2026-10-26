"""Real PostgreSQL: the staff practice loop and the rest of the worker's loops (ADR 0049)."""

import asyncio

import pytest

from quant_company.execution import FixtureProvider, TurnExecutor
from quant_company.loops import attempt
from quant_company.runtime import worker_loops
from quant_company.staff.store import StaffStore

from .test_staff_development import oracle_answer, reply


@pytest.mark.integration
async def test_staff_retry_recovers_frozen_request_without_double_grade(company):
    store = StaffStore(company)
    identity = store.enqueue("data", "UHUMAN")
    seen = []

    async def tick():
        ready = await asyncio.to_thread(store.prepare)
        seen.append(ready)
        if len(seen) == 1:
            # Simulate loss after reserving an immutable request, before saving its response.
            raise RuntimeError("simulated worker interruption")
        if ready["state"] == "ready":
            response = reply(ready["request"], answer=oracle_answer(company, identity))
            return await asyncio.to_thread(store.commit, identity, response)
        return ready

    # The staff loop's retry policy: three attempts five seconds apart (no wait needed for the second here).
    result = await attempt("staff", tick, timeout=480, attempts=3, retry_seconds=0)
    assert result is not None and seen[0]["request"] == seen[1]["request"]
    with company.db.transaction() as conn:
        assert conn.execute("SELECT state FROM staff_runs WHERE id=%s", (identity,)).fetchone()["state"] == "completed"
        assert conn.execute("SELECT count(*) AS n FROM staff_calls").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0


def names(loops):
    return [loop.name for loop in loops]


@pytest.mark.integration
def test_worker_runs_turns_staff_accounts_and_research_loops(company):
    settings = company.settings
    settings.company_staff_development_enabled = settings.model_accounts_enabled = True
    settings.company_research_enabled = settings.tech_feed_enabled = True
    settings.tech_feed_dedicated_worker = True
    loops, turns = worker_loops(company, TurnExecutor(company, FixtureProvider()))
    assert names(loops) == ["turns", "staff", "accounts", "research"]
    by_name = {loop.name: loop for loop in loops}
    # Staff practice and turns take one model call at a time; account commands never wait behind them.
    assert by_name["staff"].lane is turns.lane and by_name["accounts"].lane is None
    assert by_name["staff"].attempts == by_name["accounts"].attempts == by_name["research"].attempts == 3
    control = by_name["accounts"].tick.__self__
    assert control.wake == turns.wake
    settings.tech_feed_dedicated_worker = False
    assert names(worker_loops(company)[0])[-1] == "tech-feed"
    settings.company_staff_development_enabled = settings.model_accounts_enabled = False
    settings.company_research_enabled = settings.tech_feed_enabled = False
    assert names(worker_loops(company)[0]) == ["turns"]


@pytest.mark.integration
def test_worker_loop_waits_follow_the_former_workflows(company):
    settings = company.settings
    settings.company_staff_development_enabled = settings.model_accounts_enabled = True
    settings.company_research_enabled = True
    loops = {loop.name: loop for loop in worker_loops(company, TurnExecutor(company, FixtureProvider()))[0]}
    assert loops["turns"].wait({"state": "idle"}) == 1 and loops["turns"].wait({"state": "ran"}) == 0
    assert loops["staff"].wait({"state": "running"}) == 3 and loops["staff"].wait({"state": "idle"}) == 60
    assert loops["staff"].wait(None) == 60 and loops["accounts"].wait(None) == 2
    assert loops["research"].wait({"state": "awaiting_audit"}) == 2
    assert loops["research"].wait({"state": "idle"}) == 30 and loops["research"].wait(None) == 60
