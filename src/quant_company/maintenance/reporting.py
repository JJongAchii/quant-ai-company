"""Lightweight reporting loop, scheduled independently of model/CI work."""

import asyncio

from .cases import refresh
from .store import Store


class Reporter:
    def __init__(self, company, config):
        self.company, self.config = company, config

    def report(self):
        # Switched off, the Maintainer posts nothing; existing cards keep their last text.
        result = {"state": "disabled"}
        if self.config.enabled:
            Store(self.company, self.config).report_reviews()
            result = {"cases_checked": refresh(self.company)}
        with self.company.db.transaction() as conn:
            conn.execute("""UPDATE maintenance_control SET runtime=runtime ||
                jsonb_build_object('reporter_heartbeat_at',now(),'report_seconds',60) WHERE id=1""")
        return result

    async def tick(self):
        return await asyncio.to_thread(self.report)
