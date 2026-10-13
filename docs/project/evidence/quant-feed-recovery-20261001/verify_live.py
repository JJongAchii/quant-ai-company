"""Read-only Quant-worker verification through /app/entrypoint.py (Temporal credentials required)."""

import asyncio
import json
from datetime import UTC, datetime

from temporalio.api.enums.v1 import EventType

from quant_company.company import Company, as_json
from quant_company.config import Settings
from quant_company.quant_feed.store import QuantFeedStore
from quant_company.runtime import connect

STALLED = "c3f27c35c3ff8f16ef7bbfbc60593649bd2b3ca4cd4d5367d83487123f7b9b60"


async def main():
    company = Company(Settings())
    store = QuantFeedStore(company)
    with company.db.transaction() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        document = conn.execute("""SELECT id,state,stage,error,reviewed_at,
            receipt->>'original_sha256' AS original_sha256 FROM quant_feed_documents WHERE id=%s""", (STALLED,)).fetchone()
        calls = conn.execute("""SELECT id,document_id,stage,state,error,created_at,completed_at,
            length(request->>'prompt') AS prompt_characters,request->>'output_contract' AS contract,receipt
            FROM quant_feed_calls WHERE policy_digest=%s ORDER BY created_at DESC LIMIT 6""", (store.policy(),)).fetchall()
        states = conn.execute("SELECT state,count(*) AS n FROM quant_feed_documents GROUP BY state ORDER BY state").fetchall()
        sources = conn.execute("""SELECT id,failures,error,last_success FROM quant_feed_sources
            WHERE enabled ORDER BY id""").fetchall()
        latest = conn.execute("""SELECT p.id,p.document_id,p.policy_digest,o.status,o.sent_ts,o.created_at
            FROM quant_feed_publications p JOIN outbox o ON o.id=p.id ORDER BY o.created_at DESC LIMIT 3""").fetchall()
        historical = conn.execute("""SELECT o.status,o.sent_ts FROM quant_feed_publications p JOIN outbox o ON o.id=p.id
            WHERE p.id='ee661513-7996-5045-a465-82b9e2c60eb4'""").fetchone()
    for call in calls:
        if call.get("receipt") and "source_corrections" in call["receipt"]:
            call["source_correction_count"] = len(call["receipt"].pop("source_corrections"))
    client = await connect(company.settings)
    workflows = []
    for identity in ("company-quant-feed-collection-v1", "company-quant-feed-editorial-v1"):
        handle = client.get_workflow_handle(identity)
        described = await handle.describe()
        history = await handle.fetch_history()
        events = []
        for event in history.events[-8:]:
            row = {"id": event.event_id, "type": EventType.Name(event.event_type), "time": str(event.event_time.ToDatetime())}
            if event.HasField("activity_task_failed_event_attributes"):
                failure = event.activity_task_failed_event_attributes.failure
                row["failure_message"] = failure.cause.message or failure.message
            if event.HasField("timer_started_event_attributes"):
                row["timer_seconds"] = event.timer_started_event_attributes.start_to_fire_timeout.ToTimedelta().total_seconds()
            events.append(row)
        workflows.append({"id": identity, "status": described.status.name, "task_queue": described.task_queue, "events": events})
    print(json.dumps(as_json({"checked_at": datetime.now(UTC), "publication_enabled": company.settings.quant_feed_publish_enabled,
        "authorized": store.authorized(), "policy": store.policy(), "stalled_document": document,
        "fresh_policy_calls": calls, "document_states": states, "sources": sources, "latest_publications": latest,
        "historic_post": historical, "workflows": workflows, "model_calls_by_verifier": 0,
        "database_writes_by_verifier": 0, "slack_writes_by_verifier": 0}), ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
