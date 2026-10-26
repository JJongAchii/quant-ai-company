"""Real PostgreSQL: the research reconciliation loop and turn-driven research stages across a worker restart."""

import asyncio

import pytest

from quant_company.company import Company
from quant_company.execution import FixtureProvider, TurnExecutor
from quant_company.loops import TurnLoop, attempt
from quant_company.runtime import worker_loops

from .test_research_runner import returned
from .test_research_store import row_for


def research_loop(company):
    return next(loop for loop in worker_loops(company, TurnExecutor(company, FixtureProvider()))[0]
                if loop.name == "research")


async def tick(loop):
    return await attempt(loop.name, loop.tick, timeout=loop.timeout, attempts=loop.attempts, retry_seconds=0)


def turn_loop(company, provider):
    return TurnLoop(company, TurnExecutor(company, provider), asyncio.Semaphore(1))


def oldest_queued(company):
    with company.db.transaction() as conn:
        return str(conn.execute("SELECT id FROM turns WHERE status='queued' ORDER BY created_at LIMIT 1")
                   .fetchone()["id"])


async def finish_turn(turns, company, identity, seconds=20):
    """Tick the worker's turn loop until this turn has completed (a due time in the database is waited out)."""
    async with asyncio.timeout(seconds):
        while True:
            await turns.tick()
            with company.db.transaction() as conn:
                status = conn.execute("SELECT status FROM turns WHERE id=%s", (identity,)).fetchone()["status"]
            if status == "completed":
                return
            assert status in {"queued", "running", "waiting"}, status
            await asyncio.sleep(0.05)


@pytest.mark.integration
async def test_research_report_survives_worker_restart(research, credentials, monkeypatch, tmp_path):
    row, _ = returned(research, credentials, monkeypatch, tmp_path)
    loop = research_loop(research)
    async with asyncio.timeout(20):
        while row_for(research, row["id"])["state"] != "completed":
            await tick(loop)
    restarted = Company(research.settings, research.roles)
    # The restarted worker's next reconciliation finds the report already published.
    assert (await tick(research_loop(restarted)))["state"] == "idle"
    with restarted.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM artifacts").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM events WHERE kind='research_report_ready'").fetchone()["n"] == 1
