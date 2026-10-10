import asyncio
from datetime import timedelta

from ..company import Company
from ..news.feeds import fetch_feed
from ..web_fetch import fetch
from . import schedule
from .inputs import calendars, registrations
from .runner import BriefCollector, BriefEditor
from .store import BriefStore


async def command(settings, action):
    if action == "data-probe":
        from .data import query
        from .data_reader import summarize

        at = schedule.utcnow()
        changes = schedule.overrides(settings)
        edition = next(e for offset in range(9) for e in schedule.editions(
            at.astimezone(schedule.KST).date()+timedelta(days=offset), settings.briefing_channel_id,
            settings.briefing_owner_user, changes, us_close_anchor=settings.briefing_us_close_enabled,
            kr_close_anchor=schedule.kr_close_minutes(settings)) if e.cutoff > at)
        krx_close = {"krx_close": True} if settings.briefing_kr_close_enabled and edition.kind == "pm" else {}
        snapshot = await asyncio.to_thread(query, settings.company_lake_uri, edition, **krx_close)
        result = await asyncio.to_thread(summarize, snapshot, edition, changes)
        return {"mode": "actual_qdata_api_read", "edition": edition.model_dump(mode="json"),
                "snapshot": snapshot, "derived": result, "model": "not_called", "slack": "not_connected"}
    if action == "probe":
        async def calendar_probe(identity, publisher, url):
            receipt, _ = await asyncio.to_thread(fetch, url)
            return {"source": identity, "publisher": publisher, "url": url,
                    "ok": receipt.get("ok", False), "error": receipt.get("error"),
                    "sha256": receipt.get("original_sha256"), "retrieved_at": receipt.get("retrieved_at"),
                    "characters": len(receipt.get("content", ""))}

        calendar_results = await asyncio.gather(*(calendar_probe(*entry) for entry in calendars(schedule.utcnow().date())))
        specs = [s for s in registrations(settings) if s.kind == "media" and any(
            n in s.id for n in ("cnbc", "yonhap", "yna"))][:6]
        receipts = await asyncio.gather(*(asyncio.to_thread(fetch_feed, s) for s in specs))
        feeds = [{"source": s.id, "url": s.feed_url, "ok": r.get("ok", False), "error": r.get("error"),
                  "entries": len(r.get("entries", [])), "sha256": r.get("sha256")} for s, r in zip(specs, receipts, strict=True)]
        return {"checked_at": schedule.utcnow().isoformat(), "network": "real-public-sources",
                "model": "not-called", "slack": "not-connected", "calendars": calendar_results, "feeds": feeds,
                "meaning": "Connectivity only; not core quote coverage, review, or five-day qualification."}
    company = Company(settings)
    if action == "qualify":
        from .qualification import qualify

        return await asyncio.to_thread(qualify, company)
    if action == "data":
        from .data import BriefDataCollector

        return await BriefDataCollector(company).tick()
    if action in {"status", "preview"}:
        result = BriefStore(company).status(include_rendered=action == "preview")
        if action == "preview":
            return {"mode": "stored-preview-read-only", "publish_enabled": result["publish_enabled"],
                    "editions": [e for e in result["editions"] if e["rendered"]][:4],
                    "note": "No model call or Slack message. Run enabled collector/editor or CLI collect/review to produce editions."}
        return result
    if action == "collect":
        return await BriefCollector(company).tick()
    return await BriefEditor(company).tick()
