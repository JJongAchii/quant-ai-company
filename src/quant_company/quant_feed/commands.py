import asyncio

from ..company import Company, as_json
from .contracts import load_sources
from .feeds import collect
from .runner import QuantFeedCollector, QuantFeedEditor
from .store import QuantFeedStore


async def command(settings, action, *, live=False):
    if action == "probe":
        results = []
        for source in load_sources(settings.quant_feed_sources_file):
            if not source.enabled:
                continue
            receipt = await asyncio.to_thread(collect, source)
            entries = receipt.pop("entries", [])
            results.append({"source": source.id, **receipt, "entries": len(entries), "sample": entries[:2]})
        return as_json({"network": "real-public-sources", "model": "not-called", "slack": "not-connected", "sources": results})
    company = Company(settings)
    if action == "status":
        return QuantFeedStore(company).status()
    if not live:
        raise ValueError("quant_preview_requires_explicit_live_subscription_flag")
    if settings.quant_feed_publish_enabled:
        raise ValueError("quant_preview_requires_publication_disabled")
    collected = await QuantFeedCollector(company).tick()
    reviewed = await QuantFeedEditor(company).tick()
    return {"mode": "live-subscription-preview", "publication_enabled": False,
            "collection": collected, "review": reviewed, "status": QuantFeedStore(company).status()}
