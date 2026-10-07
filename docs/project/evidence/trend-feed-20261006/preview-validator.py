"""Run inside news-worker; read real preview records without invoking a model or API."""

import datetime
import hashlib
import json
import sys

from quant_company.company import Company, as_json
from quant_company.config import Settings
from quant_company.contracts import ProviderResponse
from quant_company.trend_feed.editor import render, validate_draft
from quant_company.trend_feed.schedule import KST
from quant_company.trend_feed.sources import article_source, trend_summary
from quant_company.trend_feed.store import TrendFeedStore


def validate_day(store, conn, row):
    reasons = []
    bundle = row["bundle"]
    candidates = bundle["candidates"]
    # Freeze serializes its scheduled cutoff in KST; PostgreSQL may return UTC.
    current = store.bundle(conn, row["cutoff"].astimezone(KST))
    calls = conn.execute("SELECT * FROM trend_feed_calls WHERE digest_id=%s ORDER BY stage", (row["id"],)).fetchall()
    api = conn.execute("SELECT * FROM trend_feed_api_receipts WHERE digest_id=%s", (row["id"],)).fetchall()
    usage = conn.execute("SELECT calls FROM trend_feed_api_usage WHERE day=%s", (row["day"],)).fetchone()
    outbox = conn.execute("SELECT status FROM outbox WHERE id=%s", (row["id"],)).fetchone()
    lo = datetime.datetime.combine(row["day"], datetime.time(), KST)
    model_count = conn.execute("SELECT count(*) AS n FROM trend_feed_calls WHERE created_at>=%s AND created_at<%s",
                               (lo, lo + datetime.timedelta(days=1))).fetchone()["n"]
    if row["state"] != "preview" or outbox:
        reasons.append("preview_state_or_outbox")
    if row["cutoff"].astimezone(KST).strftime("%H:%M") != "07:30" or row["send_at"].astimezone(KST).strftime("%H:%M") != "08:00":
        reasons.append("schedule_mismatch")
    if not row["enriched"] or not candidates or bundle["collection_gap"]:
        reasons.append("enrichment_or_collection_gap")
    if any(current[k] != bundle[k] for k in ("cutoff", "partial_history", "collection_gap", "last_success", "snapshot_ids")):
        reasons.append("frozen_coverage_mismatch")
    fields = ("id", "title", "traffic", "traffic_floor", "peak_snapshot", "latest_snapshot", "first_seen", "last_seen", "new")
    if [[c[k] for k in fields] for c in candidates] != [[c[k] for k in fields] for c in current["candidates"]]:
        reasons.append("frozen_candidate_or_peak_mismatch")
    if model_count > 2 or not usage or usage["calls"] > 100:
        reasons.append("daily_request_budget")
    if any(c["state"] in {"blocked", "running", "queued"} for c in calls):
        reasons.append("unresolved_model_request")
    if not row["draft"] or not any(c["state"] == "completed" for c in calls):
        reasons.append("editorial_preview_not_completed")
    validated = []
    for call in calls:
        if call["state"] == "completed":
            try:
                validated.append(validate_draft(ProviderResponse.model_validate(call["response"]), bundle).model_dump())
            except (ValueError, TypeError, KeyError):
                reasons.append("model_response_validation")
    if row["draft"] and row["draft"] not in validated:
        reasons.append("draft_receipt_mismatch")
    if render(bundle, row["draft"]) != row["content"]:
        reasons.append("final_body_changed")
    for candidate in candidates:
        for article in candidate["articles"]:
            if not article_source(article["url"], store.company.settings):
                reasons.append("original_publisher_not_allowed")
        matches = []
        for receipt in api:
            if receipt["kind"] != "trend" or not receipt["receipt"].get("ok"):
                continue
            for result in receipt["receipt"].get("data", {}).get("results", []):
                if result.get("title") == candidate["id"] and result.get("keywords") == [candidate["title"]]:
                    matches.append(trend_summary(result, receipt["request"]))
        if candidate["naver"].get("state") == "available" and candidate["naver"] not in matches:
            reasons.append("naver_comparison_receipt_mismatch")
    if not any(r["kind"] == "trend" and r["receipt"].get("ok") for r in api):
        reasons.append("naver_trend_not_verified")
    if any(not r["receipt"].get("ok") for r in api):
        reasons.append("naver_request_error")
    fingerprint = hashlib.sha256(json.dumps(as_json({k: row[k] for k in
        ("id", "day", "cutoff", "send_at", "policy_digest", "bundle", "draft", "content")}),
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"id": str(row["id"]), "day": str(row["day"]), "state": row["state"],
            "valid": not reasons, "reasons": sorted(set(reasons)), "fingerprint": fingerprint,
            "candidate_count": len(candidates), "card_limit": 8, "partial_history": bundle["partial_history"],
            "collection_gap": bundle["collection_gap"], "model_requests": model_count,
            "naver_requests": usage["calls"] if usage else 0,
            "original_articles": sum(len(c["articles"]) for c in candidates), "preview_outbox_absent": not outbox}


def main(start_day):
    company = Company(Settings())
    store = TrendFeedStore(company)
    with company.db.transaction() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        rows = conn.execute("SELECT * FROM trend_feed_digests WHERE day>=%s AND state='preview' ORDER BY day",
                            (datetime.date.fromisoformat(start_day),)).fetchall()
        days = [validate_day(store, conn, row) for row in rows]
    print(json.dumps({"checked_at": datetime.datetime.now(datetime.UTC).isoformat(), "days": days,
                      "mode": "read_only_real_records", "model_calls_made": 0, "api_calls_made": 0,
                      "authorized": bool(store.authorized()),
                      "publish_enabled": company.settings.trend_feed_publish_enabled}))


if __name__ == "__main__":
    main(sys.argv[1])
