"""Explicitly reconcile certainly-unsent Quant posts after an allowlist-only change.

No model invocation or direct Slack write. Old calls and their frozen bundles are
never rewritten. Plan first, persist its receipt, then apply that exact digest.
"""

import argparse
import hashlib
import json

from quant_company.company import Company, as_json, fingerprint
from quant_company.config import Settings
from quant_company.contracts import ProviderResponse
from quant_company.quant_feed import schedule
from quant_company.quant_feed.editor import render, source_spans, validate
from quant_company.quant_feed.store import LOCK, QuantFeedStore


def reconcile(store, identities, old_channels, old_policy, *, apply_digest=None):
    settings = store.company.settings
    current_channels = settings.slack_allowed_channels
    if (not identities or len(set(identities)) != len(identities) or len(identities) > 16
            or current_channels[:len(old_channels)] != old_channels
            or len(current_channels) <= len(old_channels)
            or settings.quant_feed_channel_id not in old_channels
            or not store.authorized() or not settings.quant_feed_publish_enabled):
        raise ValueError("quant_reconciliation_scope_invalid")
    current_policy = store.policy()
    try:
        settings.slack_allowed_channels = old_channels
        if store.policy() != old_policy or not store.authorized():
            raise ValueError("quant_change_is_not_allowlist_only")
    finally:
        settings.slack_allowed_channels = current_channels
    rows = []
    with store.db.transaction() as conn:
        if apply_digest is None:
            conn.execute("SET TRANSACTION READ ONLY")
        else:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
        for identity in identities:
            publication = conn.execute("SELECT * FROM quant_feed_publications WHERE id=%s", (identity,)).fetchone()
            outbox = conn.execute("SELECT o.*,m.kind AS message_kind FROM outbox o JOIN messages m ON m.id=o.id "
                                  "WHERE o.id=%s" + (" FOR UPDATE OF o" if apply_digest else ""),
                                  (identity,)).fetchone()
            if (not publication or not outbox or publication["policy_digest"] != old_policy
                    or publication["channel"] != settings.quant_feed_channel_id
                    or outbox["channel"] != settings.quant_feed_channel_id or outbox["agent"] != "quant_scout"
                    or outbox["message_kind"] != "quant_feed" or outbox["status"] != "stale"
                    or outbox["error"] != "quant_feed_policy_changed" or outbox["attempts"] != 0
                    or outbox["sent_ts"] is not None or outbox["started_at"] is not None):
                raise ValueError("quant_not_certainly_unsent:" + identity)
            document = conn.execute("SELECT * FROM quant_feed_documents WHERE id=%s",
                                    (publication["document_id"],)).fetchone()
            candidate = conn.execute("SELECT * FROM quant_feed_candidates WHERE id=%s",
                                     (document["candidate_id"],)).fetchone()
            source = store.sources().get(candidate["source_id"])
            if (document["state"] != "queued" or not source or not source.enabled
                    or document["source_digest"] != fingerprint(source.model_dump())
                    or not source.allows(candidate["url"]) or not source.allows(document["metadata"]["url"])):
                raise ValueError("quant_original_requires_requalification:" + identity)
            calls = conn.execute("""SELECT * FROM quant_feed_calls WHERE document_id=%s
                AND state='completed' ORDER BY created_at DESC,id DESC""", (document["id"],)).fetchall()
            critic = next((call for call in calls if call["stage"] == "critique"), None)
            draft = next((call for call in calls if call["stage"] in {"review", "repair", "revision"}), None)
            if (not critic or not draft or critic["id"] == draft["id"]
                    or any(call["policy_digest"] != old_policy for call in (critic, draft))):
                raise ValueError("quant_frozen_reviews_missing:" + identity)
            current_bundle = store.bundle(conn, document)
            for call in (critic, draft):
                if (call["bundle"]["original_sha256"] != current_bundle["original_sha256"]
                        or source_spans(call["bundle"]["pages"]) != source_spans(current_bundle["pages"])
                        or call["response"]["request_id"] != call["id"]):
                    raise ValueError("quant_frozen_original_changed:" + identity)
            brief = validate(ProviderResponse.model_validate(draft["response"]), draft["bundle"], draft["stage"])
            checked = validate(ProviderResponse.model_validate(critic["response"]), critic["bundle"], "critique")
            if (brief.disposition != "publish" or brief.change != "new" or critic["bundle"].get("prior")
                    or checked.disposition != "pass" or checked.issues
                    or fingerprint(brief.model_dump()) != fingerprint(document["brief"])
                    or fingerprint(critic["bundle"]["draft"]) != fingerprint(document["brief"])
                    or fingerprint(checked.model_dump()) != fingerprint(document["critique"])
                    or render(brief, document["metadata"], None) != outbox["text"]
                    or len(outbox["text"]) > 2400):
                raise ValueError("quant_saved_evidence_no_longer_valid:" + identity)
            rows.append({"id": identity, "document_id": document["id"], "title": document["metadata"]["title"],
                         "old_policy": old_policy, "new_policy": current_policy, "old_status": outbox["status"],
                         "old_error": outbox["error"], "attempts": 0, "sent_ts": None,
                         "original_sha256": current_bundle["original_sha256"],
                         "draft_request_id": draft["id"], "critic_request_id": critic["id"],
                         "card_sha256": hashlib.sha256(outbox["text"].encode()).hexdigest(),
                         "card_characters": len(outbox["text"]),
                         "frozen_evidence_revalidated": True})
        plan = {"old_channels": old_channels, "current_channels": current_channels, "publications": rows}
        digest = fingerprint(plan)
        if apply_digest is not None:
            if digest != apply_digest:
                raise ValueError("quant_reconciliation_plan_changed")
            for row in rows:
                conn.execute("UPDATE quant_feed_publications SET policy_digest=%s WHERE id=%s",
                             (current_policy, row["id"]))
                conn.execute("""UPDATE outbox SET status='pending',error=NULL,next_at=%s
                    WHERE id=%s""", (schedule.delivery_time(schedule.utcnow()), row["id"]))
    return as_json({"state": "applied" if apply_digest else "planned", "plan_digest": digest, **plan,
                    "model_calls": 0, "direct_slack_writes": 0,
                    "original_calls_rewritten": 0, "database_writes": len(rows) * 2 if apply_digest else 0})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", action="append", required=True)
    parser.add_argument("--old-channels", type=json.loads, required=True)
    parser.add_argument("--old-policy", required=True)
    parser.add_argument("--apply-digest")
    args = parser.parse_args()
    result = reconcile(QuantFeedStore(Company(Settings())), args.id, args.old_channels, args.old_policy,
                       apply_digest=args.apply_digest)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
