"""Read active PostgreSQL state and actual Temporal timers; do not invoke APIs/models/Slack."""

import asyncio
import datetime
import hashlib
import json
from pathlib import Path

from quant_company.company import Company, as_json
from quant_company.config import Settings
from quant_company.runtime import connect
from quant_company.trend_feed import schedule
from quant_company.trend_feed.store import TrendFeedStore


async def main():
    company = Company(Settings())
    store = TrendFeedStore(company)
    if not store.authorized() or not company.settings.trend_feed_publish_enabled:
        raise RuntimeError("regular_publication_not_active")
    client = await connect(company.settings)
    workflows = []
    for identity in ("company-trend-feed-collection-v1", "company-trend-feed-digest-v1", "company-trend-feed-editorial-v1"):
        handle = client.get_workflow_handle(identity)
        description = await handle.describe()
        timers = {}
        for event in (await handle.fetch_history()).events:
            if event.HasField("timer_started_event_attributes"):
                timers[event.event_id] = (event.event_time.ToDatetime(datetime.UTC)
                                         + event.timer_started_event_attributes.start_to_fire_timeout.ToTimedelta()).isoformat()
            elif event.HasField("timer_fired_event_attributes"):
                timers.pop(event.timer_fired_event_attributes.started_event_id, None)
            elif event.HasField("timer_canceled_event_attributes"):
                timers.pop(event.timer_canceled_event_attributes.started_event_id, None)
        workflows.append({"id": identity, "status": description.status.name, "pending_timer_fire_at": list(timers.values())})
    renderer = Path(__import__("quant_company.trend_feed.editor", fromlist=["render"]).__file__)
    with company.db.transaction() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        receipt = conn.execute("SELECT id,status,channel,sent_ts,error,attempts FROM outbox WHERE id=%s",
                               ("012716d1-68d5-52fe-839e-047a2c9b99ef",)).fetchone()
        pause = conn.execute("SELECT paused_until,reason FROM runtime_control WHERE id=1").fetchone()
    print(json.dumps(as_json({"checked_at": schedule.utcnow(), "publish_enabled": True,
                              "next_publication": schedule.next_publication(schedule.utcnow()),
                              "renderer_sha256": hashlib.sha256(renderer.read_bytes()).hexdigest(),
                              "workflows": workflows, "status": store.status(), "manual_receipt": receipt,
                              "runtime_pause": pause, "model_calls_made": 0, "naver_calls_made": 0,
                              "slack_calls_made": 0}), ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
