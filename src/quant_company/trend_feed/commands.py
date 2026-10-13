import asyncio

from ..company import Company, as_json
from . import schedule
from .contracts import GOOGLE_RSS
from .runner import TrendFeedCollector
from .sources import NaverClient, fetch_google
from .store import TrendFeedStore


async def command(settings, action):
    if action == "probe":
        receipt = await asyncio.to_thread(fetch_google)
        return as_json({"checked_at": schedule.utcnow(), "network": "real-public-google-rss", "url": GOOGLE_RSS,
                       **{k: v for k, v in receipt.items() if k not in {"raw_xml", "entries"}},
                       "entries": len(receipt.get("entries", [])), "sample": receipt.get("entries", [])[:3],
                       "naver_configured": bool(NaverClient(settings).credentials()),
                       "naver": "not-called; validated by bounded scheduled enrichment",
                       "database": "not-connected", "model": "not-called", "slack": "not-connected"})
    company = Company(settings)
    store = TrendFeedStore(company)
    if action == "status":
        return store.status()
    if action == "preview":
        return store.preview()
    return await TrendFeedCollector(company).tick()
