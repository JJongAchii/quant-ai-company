"""Read-only installed-service and Slack permalink verification; no model calls.

Run through /app/entrypoint.py in the API container. Credentials stay inside
the service. Original pages and model responses are never emitted. --card emits
only the already-public card for the operator's readability inspection.
"""

import hashlib
import json
import sys
from datetime import UTC, datetime

import httpx

from quant_company.company import Company, as_json
from quant_company.config import Settings
from quant_company.quant_feed.store import QuantFeedStore

CHANNEL = "C0C3K8ZB9PB"
POLICY = "567a9c8c20501bc1783b66179ebed0e35fc25727ca00bce1d252e60735cfb7eb"


def main():
    company = Company(Settings())
    store = QuantFeedStore(company)
    with company.db.transaction() as conn:
        publication = conn.execute("""SELECT p.id,p.document_id,p.channel,p.policy_digest,o.status,
            o.sent_ts,o.error,o.attempts,o.created_at,o.next_at,o.agent,o.text,c.title,c.url,
            d.brief,d.critique,d.metadata,d.receipt->>'original_sha256' AS original_sha256
            FROM quant_feed_publications p JOIN outbox o ON o.id=p.id
            JOIN quant_feed_documents d ON d.id=p.document_id
            JOIN quant_feed_candidates c ON c.id=d.candidate_id
            WHERE p.channel=%s AND p.policy_digest=%s ORDER BY o.created_at DESC LIMIT 1""",
                                   (CHANNEL, POLICY)).fetchone()
        if publication is None:
            raise ValueError("quant_current_policy_publication_missing")
        calls = conn.execute("""SELECT id,stage,state,error,policy_digest,created_at,completed_at,receipt
            FROM quant_feed_calls WHERE document_id=%s ORDER BY created_at""",
                             (publication["document_id"],)).fetchall()
        states = conn.execute("SELECT state,count(*) AS count FROM quant_feed_documents GROUP BY state").fetchall()
        historic = conn.execute("""SELECT p.id,o.status,o.sent_ts FROM quant_feed_publications p
            JOIN outbox o ON o.id=p.id WHERE p.id='ee661513-7996-5045-a465-82b9e2c60eb4'""").fetchone()
    card = publication.pop("text")
    draft = publication.pop("brief")
    critique = publication.pop("critique")
    publication.pop("metadata")
    credential = json.loads(company.settings.slack_credentials_file.read_text())["quant_scout"]
    with httpx.Client(timeout=15, trust_env=False, follow_redirects=False,
                      headers={"Authorization": "Bearer " + credential["bot_token"]}) as client:
        identity = client.post("https://slack.com/api/auth.test").json()
        permalink = client.get("https://slack.com/api/chat.getPermalink", params={
            "channel": publication["channel"], "message_ts": publication["sent_ts"],
        }).json()
    identity_ok = (identity.get("ok") is True and identity.get("user_id") == credential["bot_user_id"]
                   and identity.get("team_id") == company.settings.slack_team_id)
    checks = {
        "publication_enabled": company.settings.quant_feed_publish_enabled,
        "collection_enabled": company.settings.quant_feed_enabled,
        "authorized": store.authorized(), "policy_current": store.policy() == POLICY,
        "delivered_with_timestamp": publication["status"] == "delivered" and bool(publication["sent_ts"]),
        "one_send_attempt": publication["attempts"] == 1,
        "quant_bot_author": publication["agent"] == "quant_scout" and identity_ok,
        "slack_permalink_confirmed": permalink.get("ok") is True,
        "fresh_current_policy_calls": bool(calls) and all(c["policy_digest"] == POLICY for c in calls),
        "independent_critic_passed": critique.get("disposition") == "pass" and not critique.get("issues"),
        "central_quant_scope": critique.get("direct_quant_scope") is True,
        "substantive_research": critique.get("substantive_research") is True,
        "claims_supported": critique.get("claims_supported") is True,
        "card_budget": len(card) <= 2400,
        "grouped_card": all("\n\n" + heading + "\n" in card for heading in (
            "*핵심*", "*왜 읽나*", "*연구 설계·결과*", "*주의점*", "*적용 전*")),
        "historic_post_preserved": bool(historic and historic["status"] == "delivered"
                                       and historic["sent_ts"] == "1790146734.295959"),
    }
    result = {"checked_at": datetime.now(UTC).isoformat(), "checks": checks,
              "publication": publication, "permalink": permalink.get("permalink"),
              "card_sha256": hashlib.sha256(card.encode()).hexdigest(), "card_characters": len(card),
              "card_lines": len(card.splitlines()), "stage_receipts": calls,
              "published_on": draft.get("published_on"), "maturity": draft.get("maturity"),
              "critic": critique, "document_states": states,
              "slack_history_readback": "not_permitted_by_chat_write_only_bot",
              "slack_writes_by_verifier": 0, "model_calls_by_verifier": 0,
              "database_writes_by_verifier": 0}
    if "--card" in sys.argv:
        result["card"] = card
    print(json.dumps(as_json(result), ensure_ascii=False, indent=2))
    if not all(checks.values()):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
