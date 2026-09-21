import asyncio

from ..company import Company, as_json
from . import schedule
from .contracts import load_sources
from .feeds import fetch, render
from .runner import TechFeedCollector
from .store import TechFeedStore


async def command(settings, action):
    if action == "probe":
        results = []
        for source in load_sources(settings.tech_feed_sources_file):
            if not source.enabled:
                results.append({"source": source.id, "state": "disabled"})
                continue
            receipt = await asyncio.to_thread(fetch, source)
            entries = receipt.pop("entries", [])
            results.append({"source": source.id, "feed_url": source.feed_url, **receipt,
                            "entries": len(entries), "dated": sum(e["event_at"] is not None for e in entries),
                            "preview": [render(e, source) for e in entries[:2]]})
        return as_json({"checked_at": schedule.utcnow(), "network": "real-public-rss",
                        "model": "not-called", "slack": "not-connected", "sources": results})
    company = Company(settings)
    if action == "status":
        return TechFeedStore(company).status()
    return await TechFeedCollector(company).tick()
