import asyncio

from temporalio import activity

from ..contracts import ProviderFault, ProviderRequest
from ..execution import provider_for
from .independent_review import IndependentReviewRunner
from .store import StaffStore


class StaffRunner:
    def __init__(self, company, provider=None, review_provider=None):
        self.store = StaffStore(company)
        self.provider = provider if provider is not None else provider_for(company)
        self.reviews = IndependentReviewRunner(company, review_provider)

    async def tick(self, heartbeat=False, manual=False):
        if not manual and not self.store.company.settings.company_staff_development_enabled:
            return {"state": "paused"}
        await asyncio.to_thread(self.store.schedule)
        ready = await asyncio.to_thread(self.store.prepare)
        if ready["state"] == "idle" and self.store.company.settings.company_staff_review_enabled:
            return await self.reviews.tick(heartbeat=heartbeat)
        if ready["state"] != "ready":
            return ready
        task = asyncio.create_task(self.provider.run(ProviderRequest.model_validate(ready["request"])))
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=5)
                if heartbeat:
                    activity.heartbeat({"run_id": ready["run_id"]})
            response = await task
            return await asyncio.to_thread(self.store.commit, ready["run_id"], response)
        except ProviderFault as exc:
            await asyncio.to_thread(self.store.fault, ready["run_id"], exc.code, exc.retry_after_seconds)
            return {"state": "defer" if exc.code in {"busy", "quota", "unavailable"} else "blocked", "reason": exc.code}
        except ValueError:
            await asyncio.to_thread(self.store.fault, ready["run_id"], "invalid_assessment_response")
            return {"state": "blocked", "reason": "invalid_assessment_response"}
        finally:
            # Recover a worker crash using the frozen request ID, never a new inference identity.
            if not task.done():
                task.cancel()

    @activity.defn(name="company_staff_tick")
    async def activity_tick(self):
        return await self.tick(heartbeat=True)
