"""Read-only PG/Temporal and Slack auth/permalink checks; no messages or models."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from temporalio.client import Client

from quant_company.company import Company, as_json
from quant_company.config import Settings
from quant_company.tech_feed.contracts import TECH_FEED_AGENT
from quant_company.tech_feed.store import TechFeedStore


async def main():
    settings = Settings()
    store = TechFeedStore(Company(settings))
    result = {"checked_at": datetime.now(UTC), "enabled": settings.tech_feed_enabled,
              "publish_enabled": settings.tech_feed_publish_enabled, "authorized": store.authorized(),
              "policy": store.policy(), "model_calls_by_this_check": 0, "slack_writes_by_this_check": 0}
    with store.db.transaction() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        result["sources"] = conn.execute("""SELECT id,last_success,failures,error
            FROM tech_feed_sources ORDER BY id""").fetchall()
        result["counts"] = conn.execute("""SELECT o.status,count(*) AS count FROM outbox o
            JOIN tech_feed_publications p ON p.id=o.id GROUP BY o.status ORDER BY o.status""").fetchall()
        result["deliveries"] = conn.execute("""SELECT o.id,o.agent,o.channel,o.status,o.sent_ts,o.error,o.attempts,
            i.title,p.policy_digest FROM tech_feed_publications p JOIN outbox o ON o.id=p.id
            JOIN tech_feed_items i ON i.id=p.item_id WHERE o.status='delivered'
            AND o.sent_ts::numeric>1791419400 ORDER BY o.sent_ts::numeric DESC LIMIT 5""").fetchall()
        result["new_policy_items"] = conn.execute("""SELECT o.id,o.status,o.error,o.attempts,o.sent_ts,o.created_at
            FROM tech_feed_publications p JOIN outbox o ON o.id=p.id
            WHERE p.policy_digest=%s ORDER BY o.created_at DESC LIMIT 5""", (store.policy(),)).fetchall()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace,
                                  api_key=settings.temporal_api_key.get_secret_value() or None,
                                  tls=settings.temporal_tls)
    handle = client.get_workflow_handle("company-tech-feed-collection-v1")
    description = await handle.describe()
    result["workflow"] = {"run_id": description.run_id, "status": description.status.name,
                          "task_queue": description.task_queue,
                          "pending_activities": len(description.raw_description.pending_activities)}
    history = await handle.fetch_history()
    completed = [event.activity_task_completed_event_attributes for event in history.events
                 if event.HasField("activity_task_completed_event_attributes")]
    result["workflow"]["completed_activities"] = len(completed)
    if completed:
        result["workflow"]["latest_activity"] = await client.data_converter.decode(completed[-1].result.payloads)
    if settings.slack_credentials_file:
        credential = json.loads(settings.slack_credentials_file.read_text())[TECH_FEED_AGENT]
        headers = {"Authorization": "Bearer " + credential["bot_token"]}
        async with httpx.AsyncClient(timeout=15) as http:
            response = await http.post("https://slack.com/api/auth.test", headers=headers)
            body = response.json()
            result["slack_identity"] = {key: body.get(key) for key in ("ok", "error", "user", "user_id", "team_id")}
            result["slack_identity"]["matches_configured_bot"] = body.get("user_id") == credential["bot_user_id"]
            for delivery in result["deliveries"]:
                response = await http.post("https://slack.com/api/chat.getPermalink", headers=headers,
                                           data={"channel": delivery["channel"], "message_ts": delivery["sent_ts"]})
                body = response.json()
                delivery["slack_permalink"] = {key: body.get(key) for key in ("ok", "error", "permalink")}
    result["patch_sha256"] = {name: hashlib.sha256((Path(__import__("quant_company.tech_feed.store", fromlist=["__file__"])
                                                               .__file__).parent / name).read_bytes()).hexdigest()
                             for name in ("store.py", "recovery.py", "worker.py")}
    print(json.dumps(as_json(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
