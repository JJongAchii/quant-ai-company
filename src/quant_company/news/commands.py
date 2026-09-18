import asyncio
from datetime import UTC, datetime

from ..company import Company
from .contracts import load_sources
from .feeds import fetch_feed
from .runner import NewsCollector, NewsEditor
from .store import NewsStore


async def command(settings, action):
    if action == "probe":
        results = []
        for source in load_sources(settings.news_sources_file):
            if not source.enabled:
                results.append({"source": source.id, "skipped": "source_disabled", "usage_note": source.usage_note})
                continue
            receipt = await asyncio.to_thread(fetch_feed, source)
            entries = receipt.pop("entries", [])
            results.append({"source": source.id, "feed_url": source.feed_url, **receipt,
                            "entry_count": len(entries), "dated_count": sum(e["published_at"] is not None for e in entries),
                            "latest_publication": max((e["published_at"] for e in entries if e["published_at"]), default=None),
                            "summary_use_configured": source.use_for_summary, "usage_note": source.usage_note})
        return {"checked_at": datetime.now(UTC).isoformat(), "network": "real-public-rss",
                "model": "not-called", "slack": "not-connected", "sources": results}
    company = Company(settings)
    if action == "status":
        return NewsStore(company).status()
    if action == "collect":
        return await NewsCollector(company).tick()
    return await NewsEditor(company).tick()
