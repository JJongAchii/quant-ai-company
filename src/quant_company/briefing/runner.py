import asyncio
import contextlib

from temporalio import activity

from ..contracts import ProviderFault, ProviderRequest, ProviderResponse
from ..execution import provider_for
from . import schedule
from .contracts import BriefEdition
from .inputs import collect
from .store import BriefStore


class BriefCollector:
    def __init__(self, company, collector=collect):
        self.store, self.collector = BriefStore(company), collector

    async def tick(self):
        row = await asyncio.to_thread(self.store.claim_collection)
        if not row:
            return {"state": "idle"}
        bundle = await asyncio.to_thread(self.collector, self.store.company, BriefEdition.model_validate(row["definition"]),
                                          row["bundle"], row["candidates"], at=schedule.utcnow())
        await asyncio.to_thread(self.store.save_collection, row, bundle)
        return {"state": "collected", "edition_id": str(row["id"])}

    @activity.defn(name="company_brief_collect")
    async def activity_tick(self):
        return await self.tick()


class BriefEditor:
    def __init__(self, company, provider=None):
        self.store = BriefStore(company)
        self.provider = provider if provider is not None else provider_for(company)

    async def tick(self, heartbeat=False):
        ready = await asyncio.to_thread(self.store.prepare)
        if ready["state"] != "ready":
            return ready
        request = ProviderRequest.model_validate(ready["request"])
        task = asyncio.create_task(self.provider.run(request))
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=5)
                if heartbeat:
                    activity.heartbeat({"request_id": request.request_id})
            response = ProviderResponse.model_validate(await task)
            if response.request_id != request.request_id:
                raise ValueError("brief_response_identity_mismatch")
            return await asyncio.to_thread(self.store.commit, response)
        except ProviderFault as exc:
            await asyncio.to_thread(self.store.fault, request.request_id, exc.code, exc.retry_after_seconds)
            return {"state": "defer" if exc.code in {"busy", "quota", "unavailable"} else "blocked", "reason": exc.code}
        except ValueError:
            await asyncio.to_thread(self.store.fault, request.request_id, "invalid_brief_response")
            return {"state": "blocked", "reason": "invalid_brief_response"}
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    @activity.defn(name="company_brief_review")
    async def activity_tick(self):
        return await self.tick(heartbeat=True)
