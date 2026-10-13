"""Read-only verification of real Quant deliveries; credentials stay in API service."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx

from quant_company.company import Company, as_json
from quant_company.config import Settings
from quant_company.quant_feed.store import QuantFeedStore

IDENTITIES = (
    "f81016bb-f7ec-5d4f-b53c-f3c9a1219882", "94e0dac9-d677-5a11-aafb-f971c7990711",
    "bcb61b6f-1bf5-5308-8859-1aa70dc21f4e", "5770b8a4-050e-5753-9d23-d62cecefef25",
    "d4a8fb1e-b071-537f-9dc5-2e0c6a82b6df", "9e85fbec-71ae-5db4-b18b-d4d0f2d7941d",
    "5d5e81ce-b0c0-5013-b42e-902a73f47858",
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slack", action="store_true")
    args = parser.parse_args()
    company = Company(Settings())
    store = QuantFeedStore(company)
    with company.db.transaction() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        rows = conn.execute("""SELECT p.id,p.document_id,p.channel,p.policy_digest,o.status,o.sent_ts,
            o.error,o.attempts,o.agent,o.text,c.title,d.brief->>'published_on' AS published_on,d.critique,
            d.receipt->>'original_sha256' AS original_sha256
            FROM quant_feed_publications p JOIN outbox o ON o.id=p.id
            JOIN quant_feed_documents d ON d.id=p.document_id
            JOIN quant_feed_candidates c ON c.id=d.candidate_id WHERE p.id=ANY(%s::uuid[])
            ORDER BY o.created_at""", (list(IDENTITIES),)).fetchall()
        calls = conn.execute("""SELECT id,stage,state,error,policy_digest,created_at,completed_at
            FROM quant_feed_calls ORDER BY created_at DESC LIMIT 6""").fetchall()
        sources = conn.execute("""SELECT id,last_success,failures,error FROM quant_feed_sources
            WHERE enabled ORDER BY id""").fetchall()
        historic = conn.execute("""SELECT status,sent_ts FROM outbox
            WHERE id='ee661513-7996-5045-a465-82b9e2c60eb4'""").fetchone()
    policy = store.policy()
    for row in rows:
        card = row.pop("text")
        critic = row.pop("critique")
        row.update(card_sha256=hashlib.sha256(card.encode()).hexdigest(), card_characters=len(card),
                   critic_passed=critic.get("disposition") == "pass" and not critic.get("issues"),
                   current_policy=row["policy_digest"] == policy)
        if row["sent_ts"]:
            row["sent_kst"] = datetime.fromtimestamp(float(row["sent_ts"]), UTC).astimezone(
                ZoneInfo("Asia/Seoul")).isoformat()
    if args.slack:
        credential = json.loads(company.settings.slack_credentials_file.read_text())["quant_scout"]
        with httpx.Client(timeout=15, trust_env=False, follow_redirects=False,
                          headers={"Authorization": "Bearer " + credential["bot_token"]}) as client:
            identity = client.post("https://slack.com/api/auth.test").json()
            identity_ok = (identity.get("ok") is True and identity.get("user_id") == credential["bot_user_id"]
                           and identity.get("team_id") == company.settings.slack_team_id)
            for row in rows:
                if row["status"] == "delivered" and row["sent_ts"]:
                    permalink = client.get("https://slack.com/api/chat.getPermalink", params={
                        "channel": row["channel"], "message_ts": row["sent_ts"],
                    }).json()
                    row.update(slack_permalink_confirmed=permalink.get("ok") is True,
                               permalink=permalink.get("permalink"), bot_identity_confirmed=identity_ok)
    delivered = sum(row["status"] == "delivered" for row in rows)
    checks = {"authorized": store.authorized(), "publication_enabled": company.settings.quant_feed_publish_enabled,
              "seven_rows": len(rows) == 7, "all_delivered": delivered == 7,
              "all_current_policy": all(row["current_policy"] for row in rows),
              "all_independent_critics_passed": all(row["critic_passed"] for row in rows),
              "one_send_each": all(row["attempts"] == 1 for row in rows),
              "card_budgets": all(row["card_characters"] <= 2400 for row in rows),
              "historic_bad_post_unchanged": bool(historic and historic["status"] == "delivered"
                                                  and historic["sent_ts"] == "1790146734.295959")}
    if args.slack:
        checks["all_slack_links_confirmed"] = all(row.get("slack_permalink_confirmed") for row in rows)
        checks["correct_quant_bot"] = all(row.get("bot_identity_confirmed") and row["agent"] == "quant_scout"
                                         for row in rows)
    print(json.dumps(as_json({"checked_at": datetime.now(UTC), "policy": policy, "checks": checks,
                              "delivered_count": delivered, "publications": rows, "recent_calls": calls,
                              "sources": sources, "database_writes": 0, "model_calls": 0, "slack_writes": 0,
                              "slack_history_readback": "not_permitted_by_chat_write_only_bot"}), ensure_ascii=False))


if __name__ == "__main__":
    main()
