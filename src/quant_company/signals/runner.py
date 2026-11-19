"""The signals loop of the brief-data worker. Each tick resumes from PostgreSQL: first the interim post of every slot
past its fallback time, then schedule the slots, freeze the snapshot and ask the Analyst for every slot that is due,
resume the Analyst tasks a fault or a thread revision stopped, put the tasks of entered slots behind the others,
close the tasks of exited slots, grade the closed slots and post the evening scorecard. A failing step or slot is
logged and skipped, so nothing holds back an interim post (R10)."""

import asyncio
import logging

from ..briefing import schedule
from ..briefing.data_reader import fetch_chart
from . import grading
from .lake import read_kospi
from .schedule import kst_day
from .snapshot import read_snapshot
from .store import SignalStore

LOG = logging.getLogger(__name__)


class SignalRunner:
    def __init__(self, company, *, fetch=fetch_chart, reader=read_kospi):
        self.store, self.fetch, self.reader = SignalStore(company), fetch, reader

    async def tick(self):
        if not self.store.authorized():
            return {"state": "paused"}
        at = schedule.utcnow()
        counts = {"fallbacks": await self.step("fallbacks", self.store.send_fallbacks, at),
                  "scheduled": await self.step("schedule", self.store.schedule, at),
                  "asked": await self.ask_due(at),
                  "retried": await self.step("retries", self.store.retry, at),
                  "demoted": await self.step("demotion", self.store.demote, at),
                  "retired": await self.step("retirement", self.store.retire, at),
                  "graded": await self.step("grading", self.grade, at),
                  "reported": await self.step("report", self.store.report, at)}
        return {"state": "ran" if any(counts.values()) else "idle", **counts}

    async def step(self, name, call, at) -> int:
        try:
            return await asyncio.to_thread(call, at)
        except Exception as exc:  # noqa: BLE001 - the remaining steps still run (R10)
            LOG.error("Signals %s step failed: %s", name, type(exc).__name__)
            return 0

    def grade(self, at) -> int:
        """Grade every due slot from one lake read of the days they need and one KODEX 200 chart, then store the
        returns. What is still pending 14 days after its grading day is excluded (grading.expire)."""
        due = self.store.grading_due(at)
        if not due:
            return 0
        dates = sorted({day for row in due for day in (row["session_day"], row["exit_day"])})
        lake = self.reader(self.store.company.settings.company_lake_uri, dates)
        chart = self.fetch("069500.KS", params=grading.KODEX_PARAMS)
        graded, today = [], kst_day(at)
        for row in due:
            try:
                returns = grading.grade_slot(row, lake, chart)
            except Exception as exc:  # noqa: BLE001 - this slot waits for the next attempt; the others are graded
                LOG.error("Signal grading failed for %s: %s", row["id"], type(exc).__name__)
                returns = []
            graded += grading.expire(row, returns, today)
        return self.store.save_returns(graded)

    async def ask_due(self, at):
        try:
            rows = await asyncio.to_thread(self.store.due_asks, at)
        except Exception as exc:  # noqa: BLE001 - the remaining steps still run (R10)
            LOG.error("Signals asks step failed: %s", type(exc).__name__)
            return 0
        asked = 0
        for row in rows:
            try:
                # The quote reads run outside any transaction; ask() then records the slot once under its row lock.
                snapshot = await asyncio.to_thread(read_snapshot, row["kind"], self.fetch, clock=schedule.utcnow)
                asked += await asyncio.to_thread(self.store.ask, row, snapshot, at)
            except Exception as exc:  # noqa: BLE001 - one slot's failure never holds back another (R10)
                LOG.error("Signal ask failed for %s: %s", row["id"], type(exc).__name__)
        return asked
