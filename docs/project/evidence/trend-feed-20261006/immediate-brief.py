"""Explicitly authorized current-time briefing; retain the original daily slot unchanged."""

import asyncio
import json
import re
import sys
from datetime import datetime, timedelta
from uuid import uuid4

from psycopg.types.json import Jsonb

from quant_company.company import Company, as_json, fingerprint, stable
from quant_company.config import Settings
from quant_company.contracts import ProviderResponse
from quant_company.trend_feed import schedule
from quant_company.trend_feed.editor import render, validate_draft
from quant_company.trend_feed.runner import TrendFeedCollector, TrendFeedEditor
from quant_company.trend_feed.sources import article_source, trend_summary
from quant_company.trend_feed.store import LOCK, TrendFeedStore

ID = stable("trend-feed-immediate-check:20261006:C0C6WTA9ECV")
APPROVAL = "chat-user-immediate-brief-inspect-activate-20261006"


def immediate_text(bundle, draft):
    at = datetime.fromisoformat(bundle["cutoff"]).astimezone(schedule.KST)
    lines = render(bundle, draft).splitlines()
    lines[0] = f"*한국 검색 트렌드 · {at:%m/%d %H:%M} 운영 검증 브리핑*"
    return "\n".join(lines)


def check(store, conn, row):
    bundle, draft = row["bundle"], row["draft"]
    calls = conn.execute("SELECT * FROM trend_feed_calls WHERE digest_id=%s ORDER BY stage", (ID,)).fetchall()
    validated = [validate_draft(ProviderResponse.model_validate(c["response"]), bundle).model_dump()
                 for c in calls if c["state"] == "completed" and c["response"]]
    if not draft or draft not in validated or any(c["state"] in {"running", "queued", "blocked"} for c in calls):
        raise RuntimeError("editorial_completion_not_verified")
    if not bundle["candidates"] or bundle["collection_gap"]:
        raise RuntimeError("source_coverage_not_verified")
    recomputed = store.bundle(conn, row["cutoff"])
    fields = ("id", "title", "traffic", "traffic_floor", "peak_snapshot", "latest_snapshot", "first_seen", "new")
    if [[c[k] for k in fields] for c in bundle["candidates"]] != [[c[k] for k in fields] for c in recomputed["candidates"]]:
        raise RuntimeError("frozen_source_changed")
    receipts = conn.execute("SELECT * FROM trend_feed_api_receipts WHERE digest_id=%s", (ID,)).fetchall()
    if not any(r["kind"] == "trend" and r["receipt"].get("ok") for r in receipts):
        raise RuntimeError("naver_trend_not_verified")
    if any(not r["receipt"].get("ok") for r in receipts):
        raise RuntimeError("naver_request_failed")
    for candidate in bundle["candidates"]:
        matches = [trend_summary(result, receipt["request"]) for receipt in receipts if receipt["kind"] == "trend"
                   for result in receipt["receipt"].get("data", {}).get("results", [])
                   if result.get("title") == candidate["id"] and result.get("keywords") == [candidate["title"]]]
        if candidate["naver"].get("state") == "available" and candidate["naver"] not in matches:
            raise RuntimeError("naver_series_mismatch")
        if any(not article_source(article["url"], store.company.settings) for article in candidate["articles"]):
            raise RuntimeError("unapproved_original")
    day_start = row["cutoff"].astimezone(schedule.KST).replace(hour=0, minute=0, second=0, microsecond=0)
    count = conn.execute("SELECT count(*) AS n FROM trend_feed_calls WHERE created_at>=%s AND created_at<%s",
                         (day_start, day_start + timedelta(days=1))).fetchone()["n"]
    usage = conn.execute("SELECT calls FROM trend_feed_api_usage WHERE day=%s", (day_start.date(),)).fetchone()
    if count > 2 or not usage or usage["calls"] > 100:
        raise RuntimeError("daily_budget_exceeded")
    text = immediate_text(bundle, draft)
    return {"valid": True, "content_sha256": fingerprint(text), "text": text,
            "cards": len(re.findall(r"^\*\d+\.", text, re.MULTILINE)), "partial_history": bundle["partial_history"],
            "collection_gap": bundle["collection_gap"], "model_requests_today": count, "naver_requests_today": usage["calls"],
            "calls": [{**{k: c[k] for k in ("id", "state", "error", "created_at", "completed_at")},
                       "model": c["request"]["model"], "reasoning_effort": c["request"]["reasoning_effort"],
                       "provider": c["response"].get("provider") if c["response"] else None} for c in calls],
            "original_articles": [a for c in bundle["candidates"] for a in c["articles"]],
            "backgrounds": [{k: item[k] for k in ("member_ids", "category", "background", "evidence")} for item in draft["items"][:8]],
            "policy_matches": row["policy_digest"] == store.policy()}


async def main(action):
    company = Company(Settings())
    store = TrendFeedStore(company)
    if not store.authorized() or company.settings.model_provider == "fixture":
        raise RuntimeError("real_operational_scope_required")
    if action == "prepare":
        if company.settings.trend_feed_publish_enabled:
            raise RuntimeError("manual_check_requires_preview_mode")
        await TrendFeedCollector(company).tick()
        at = schedule.utcnow()
        with store.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            row = conn.execute("SELECT * FROM trend_feed_digests WHERE id=%s FOR UPDATE", (ID,)).fetchone()
            if not row:
                bundle = store.bundle(conn, at)
                if not bundle["candidates"] or bundle["collection_gap"]:
                    raise RuntimeError("current_source_not_ready")
                row = conn.execute("""INSERT INTO trend_feed_digests
                    (id,day,geo,channel,owner_user,cutoff,send_at,expires_at,policy_digest,bundle,lease_token,lease_until)
                    VALUES(%s,%s,'KR-manual',%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (ID, at.astimezone(schedule.KST).date(), company.settings.trend_feed_channel_id,
                     company.settings.trend_feed_owner_user, at, at + timedelta(minutes=30), at + timedelta(hours=1),
                     store.policy(), Jsonb(bundle), uuid4(), at + timedelta(minutes=10))).fetchone()
                company._event(conn, "trend_immediate_check_authorized", {"approval": APPROVAL, "digest_id": str(ID),
                               "real_cutoff": at.isoformat(), "scope": "manual current briefing; original daily slot unchanged"})
        if not row["enriched"]:
            await TrendFeedCollector(company).enrich(row)
        # Uses the existing persisted reservation, frozen Reporter assignment and authenticated runtime.
        await TrendFeedEditor(company).tick()
    with store.db.transaction() as conn:
        row = conn.execute("SELECT * FROM trend_feed_digests WHERE id=%s FOR UPDATE", (ID,)).fetchone()
        if not row:
            raise RuntimeError("manual_digest_missing")
        result = check(store, conn, row)
        if action in {"prepare", "repair", "queue"}:
            if row["content"] and row["content"] != result["text"]:
                if action != "repair" or conn.execute("SELECT 1 FROM outbox WHERE id=%s", (ID,)).fetchone():
                    raise RuntimeError("committed_manual_body_changed")
                company._event(conn, "trend_immediate_body_repair_before_delivery",
                               {"approval": APPROVAL, "id": str(ID), "before": row["content"], "after": result["text"],
                                "reason": "Remove unverified article associations; preserve frozen inputs and model response."})
            conn.execute("UPDATE trend_feed_digests SET state='preview',content=%s WHERE id=%s", (result["text"], ID))
        if action == "queue":
            if not result["policy_matches"] or schedule.utcnow() >= row["expires_at"]:
                raise RuntimeError("manual_policy_or_freshness_changed")
            project_id = stable(f"trend-feed-project:{row['channel']}:{row['owner_user']}")
            project = conn.execute("SELECT * FROM projects WHERE id=%s FOR UPDATE", (project_id,)).fetchone()
            old = conn.execute("SELECT text,author FROM messages WHERE id=%s", (ID,)).fetchone()
            if old and (old["text"] != result["text"] or old["author"] != "trend_scout"):
                raise RuntimeError("manual_message_identity_changed")
            if not old:
                company._message(conn, project, None, "trend_scout", "status", result["text"], message_id=str(ID))
                company._event(conn, "trend_immediate_brief_delivery_authorized", {"approval": APPROVAL,
                               "message_id": str(ID), "scope": "one manual briefing before regular activation"}, project_id)
        receipt = conn.execute("SELECT id,status,channel,sent_ts,error,attempts FROM outbox WHERE id=%s", (ID,)).fetchone()
        result.update(id=str(ID), checked_at=schedule.utcnow(), real_cutoff=row["cutoff"], daily_slot_untouched=True,
                      receipt=receipt, publish_enabled=company.settings.trend_feed_publish_enabled,
                      model_provider=company.settings.model_provider, automatic_uncertain_replay=False)
    print(json.dumps(as_json(result), ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
