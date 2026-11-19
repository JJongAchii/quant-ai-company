"""The signals loop of the brief-data worker. Each tick resumes from PostgreSQL: schedule the slots, then freeze the
snapshot and ask the Analyst for every slot that is due."""

import asyncio

from ..briefing import schedule
from ..briefing.data_reader import fetch_chart
from .snapshot import read_snapshot
from .store import SignalStore


class SignalRunner:
    def __init__(self, company, *, fetch=fetch_chart, reader=None):
        self.store, self.fetch, self.reader = SignalStore(company), fetch, reader

    async def tick(self):
        if not self.store.authorized():
            return {"state": "paused"}
        at = schedule.utcnow()
        counts = {"scheduled": await asyncio.to_thread(self.store.schedule, at)}
        counts["asked"] = await self.ask_due(at)
        return {"state": "ran" if any(counts.values()) else "idle", **counts}

    async def ask_due(self, at):
        asked = 0
        for row in await asyncio.to_thread(self.store.due_asks, at):
            # The quote reads run outside any transaction; ask() then records the slot once under its row lock.
            snapshot = await asyncio.to_thread(read_snapshot, row["kind"], self.fetch, clock=schedule.utcnow)
            asked += await asyncio.to_thread(self.store.ask, row, snapshot, at)
        return asked
