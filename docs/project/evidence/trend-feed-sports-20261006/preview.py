"""Render the persisted real briefing with the sports policy, without new external calls or writes."""

import hashlib
import json
import re
from pathlib import Path

from quant_company.company import Company, as_json
from quant_company.config import Settings
from quant_company.contracts import ProviderResponse
from quant_company.trend_feed import editor, schedule

ID = "012716d1-68d5-52fe-839e-047a2c9b99ef"


if __name__ == "__main__":
    company = Company(Settings())
    with company.db.transaction() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        row = conn.execute("SELECT bundle,draft,content,cutoff FROM trend_feed_digests WHERE id=%s", (ID,)).fetchone()
        calls = conn.execute("SELECT response FROM trend_feed_calls WHERE digest_id=%s AND state='completed'", (ID,)).fetchall()
        receipt = conn.execute("SELECT id,status,channel,sent_ts,attempts,error FROM outbox WHERE id=%s", (ID,)).fetchone()
        message = conn.execute("SELECT text FROM messages WHERE id=%s", (ID,)).fetchone()
    if (not row or not row["draft"] or not receipt or receipt["status"] != "delivered"
            or not message or row["content"] != message["text"]):
        raise RuntimeError("original_briefing_not_verified")
    bundle, draft = row["bundle"], row["draft"]
    validated = [editor.validate_draft(ProviderResponse.model_validate(c["response"]), bundle).model_dump() for c in calls]
    if draft not in validated:
        raise RuntimeError("persisted_classification_not_verified")
    candidates = {c["id"]: c for c in bundle["candidates"]}

    def topics(items):
        return [{"titles": [candidates[key]["title"] for key in item["member_ids"]], "category": item["category"]}
                for item in items]

    selected = editor.publication_items(draft)
    text = editor.render(bundle, draft)
    lines = text.splitlines()
    lines[0] = "*한국 검색 트렌드 · 스포츠 제외 미리보기*"
    text = "\n".join(lines)
    if len(re.findall(r"^\*\d+\.", text, re.MULTILINE)) != len(selected):
        raise RuntimeError("rendered_topic_count_mismatch")
    if any(item["category"] == "스포츠" for item in selected):
        raise RuntimeError("sports_topic_published")
    files = {name: Path(module.__file__) for name, module in (
        ("trend_feed/editor.py", editor),
        ("trend_feed/store.py", __import__("quant_company.trend_feed.store", fromlist=["TrendFeedStore"])))}
    print(json.dumps(as_json({"valid": True, "checked_at": schedule.utcnow(), "source_digest_id": ID,
        "source_cutoff": row["cutoff"], "editorial_policy_version": editor.EDITORIAL_POLICY_VERSION,
        "classified_candidates": len(draft["items"]),
        "excluded": topics([item for item in draft["items"] if item["category"] == "스포츠"]),
        "selected": topics(selected), "sports_selected": 0, "text": text,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "source_sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()},
        "original_receipt": receipt, "original_body_unchanged": True,
        "database_read_only": True, "model_calls_made": 0, "naver_calls_made": 0, "slack_calls_made": 0}), ensure_ascii=False))
