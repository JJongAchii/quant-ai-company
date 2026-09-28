import asyncio

from ..company import Company, as_json
from . import schedule
from .contracts import SOURCES, HousingNotice
from .render import render
from .runner import HousingFeedCollector
from .sources import collect_source
from .store import HousingFeedStore


async def command(settings, action):
    if action == "probe":
        at, results = schedule.utcnow(), []
        for source in SOURCES:
            receipt = await asyncio.to_thread(collect_source, source, at.astimezone(schedule.KST).date())
            entries = receipt.pop("entries", [])
            results.append({"source": source, **receipt, "notices": len(entries),
                            "preview": [render(HousingNotice.model_validate(n), at) for n in entries]})
        return as_json({"checked_at": at, "network": "real-public-official-pages",
                        "slack": "not-connected", "model": "not-called", "sources": results})
    company = Company(settings)
    return HousingFeedStore(company).status() if action == "status" else await HousingFeedCollector(company).tick()
