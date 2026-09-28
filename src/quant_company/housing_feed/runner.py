import asyncio

from temporalio import activity

from . import schedule
from .maps import enrich
from .sources import collect_source
from .store import HousingFeedStore


class HousingFeedCollector:
    def __init__(self, company, fetcher=collect_source):
        self.store, self.fetcher = HousingFeedStore(company), fetcher

    async def tick(self):
        if not self.store.authorized():
            return {"state": "paused"}
        claimed = await asyncio.to_thread(self.store.claim_sources)
        results = []
        # Bound public-site load. Each source snapshot commits independently.
        for row in claimed:
            receipt = await asyncio.to_thread(self.fetcher, row["id"], schedule.utcnow().astimezone(schedule.KST).date())
            if receipt.get("ok") and self.store.company.settings.housing_map_panel_enabled:
                previous = await asyncio.to_thread(self.store.map_previous, [n["id"] for n in receipt["entries"]])
                receipt = await asyncio.to_thread(enrich, receipt, previous,
                                                  schedule.utcnow().astimezone(schedule.KST).date())
            result = await asyncio.to_thread(self.store.save, row, receipt)
            results.append({"source": row["id"], **result})
        reminders = await asyncio.to_thread(self.store.reminders)
        return {"state": "collected" if claimed else "idle", "sources": results, "reminders": reminders}

    @activity.defn(name="company_housing_feed_collect")
    async def activity_tick(self):
        return await self.tick()
