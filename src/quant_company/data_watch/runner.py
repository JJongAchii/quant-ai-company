import asyncio

from temporalio import activity

from ..lake_tools import query_lake
from .core import CoreChecks
from .store import DataWatchStore


class DataWatchRunner:
    def __init__(self, company, query=None):
        self.store = DataWatchStore(company)
        self.query = query or query_lake

    async def tick(self):
        if not self.store.authorized():
            return {"state": "disabled"}
        lake = self.store.company.settings.company_lake_uri
        claim = await asyncio.to_thread(self.store.claim_inventory)
        if claim:
            receipt = await asyncio.to_thread(self.query, lake, "lake_catalog", {})
            await asyncio.to_thread(self.store.save_inventory, claim, receipt)
        for item in await asyncio.to_thread(self.store.claim_descriptions):
            receipt = await asyncio.to_thread(self.query, lake, "lake_describe", {"dataset": item["dataset"]})
            await asyncio.to_thread(self.store.save_description, item, receipt)
        await asyncio.to_thread(CoreChecks(self.store).plan)
        return await asyncio.to_thread(self.store.report)

    @activity.defn(name="company_data_watch_tick")
    async def activity_tick(self):
        return await self.tick()
