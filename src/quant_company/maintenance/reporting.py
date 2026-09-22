"""Lightweight reporting activity, scheduled independently of model/CI work."""

import asyncio

from temporalio import activity

from .cases import refresh
from .store import Store


class Reporter:
    def __init__(self, company, config):
        self.company, self.config = company, config

    def report(self):
        Store(self.company, self.config).report_reviews()
        count = refresh(self.company)
        with self.company.db.transaction() as conn:
            conn.execute("""UPDATE maintenance_control SET runtime=runtime ||
                jsonb_build_object('reporter_heartbeat_at',now(),'report_seconds',60) WHERE id=1""")
        return {"cases_checked": count}

    @activity.defn(name="company_maintenance_report")
    async def tick(self):
        return await asyncio.to_thread(self.report)
