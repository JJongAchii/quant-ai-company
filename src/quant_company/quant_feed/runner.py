import asyncio
import contextlib

from temporalio import activity

from ..contracts import ProviderFault, ProviderRequest, ProviderResponse
from ..execution import provider_for
from .contracts import QuantSource
from .feeds import collect
from .originals import fetch_original
from .store import QuantFeedStore


class QuantFeedCollector:
    def __init__(self, company, source_fetcher=collect, original_fetcher=fetch_original):
        self.store = QuantFeedStore(company)
        self.source_fetcher, self.original_fetcher = source_fetcher, original_fetcher

    async def tick(self):
        if not self.store.authorized():
            return {"state": "paused"}
        result = {"state": "idle"}
        source = await asyncio.to_thread(self.store.claim_source)
        if source:
            receipt = await asyncio.to_thread(self.source_fetcher, QuantSource.model_validate(source["config"]))
            result = await asyncio.to_thread(self.store.save_source, source, receipt)
        candidate = await asyncio.to_thread(self.store.claim_candidate)
        if candidate:
            spec = self.store.sources()[candidate["source_id"]]
            receipt = await asyncio.to_thread(self.original_fetcher, candidate["url"], spec)
            result["original"] = await asyncio.to_thread(self.store.save_original, candidate, receipt)
        return result

    @activity.defn(name="company_quant_feed_collect")
    async def activity_tick(self):
        return await self.tick()


class QuantFeedEditor:
    def __init__(self, company, provider=None):
        self.store = QuantFeedStore(company)
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
                raise ValueError("quant_response_identity_mismatch")
            return await asyncio.to_thread(self.store.commit, response)
        except ProviderFault as exc:
            await asyncio.to_thread(self.store.fault, request.request_id, exc.code, exc.retry_after_seconds)
            return {"state": "defer" if exc.code in {"quota", "busy", "unavailable"} else "blocked", "reason": exc.code}
        except ValueError:
            await asyncio.to_thread(self.store.fault, request.request_id, "invalid_quant_proposal")
            return {"state": "blocked", "reason": "invalid_quant_proposal"}
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    @activity.defn(name="company_quant_feed_review")
    async def activity_tick(self):
        return await self.tick(heartbeat=True)
