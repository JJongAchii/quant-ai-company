import asyncio

from temporalio import activity

from .contracts import TechFeedSource
from .feeds import fetch
from .store import TechFeedStore


class TechFeedCollector:
    def __init__(self, company, fetcher=fetch):
        self.store = TechFeedStore(company)
        self.fetcher = fetcher

    async def tick(self):
        if not self.store.authorized():
            return {"state": "paused"}
        claimed = await asyncio.to_thread(self.store.claim_sources)
        semaphore = asyncio.Semaphore(2)

        async def collect(row):
            async with semaphore:
                receipt = await asyncio.to_thread(self.fetcher, TechFeedSource.model_validate(row["config"]),
                                                  row["etag"], row["modified"])
                result = await asyncio.to_thread(self.store.save, row, receipt)
                return {"source": row["id"], **result}

        return {"state": "collected" if claimed else "idle", "sources": await asyncio.gather(*(collect(r) for r in claimed))}

    @activity.defn(name="company_tech_feed_collect")
    async def activity_tick(self):
        return await self.tick()
