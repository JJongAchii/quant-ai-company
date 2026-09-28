"""Durable Slack Work Object detail requests for the housing map panel."""

import re
from datetime import timedelta

import httpx

from ..company import now
from .contracts import HousingNotice
from .maps import details
from .store import HousingFeedStore


class HousingMapPanel:
    def __init__(self, company, credentials, *, transport=None):
        self.company = company
        self.credentials = credentials
        self.transport = transport
        self.store = HousingFeedStore(company)

    def enabled(self):
        return self.company.settings.housing_map_panel_enabled and self.store.authorized()

    def accept(self, payload, event, credential):
        settings = self.company.settings
        external_ref = event.get("external_ref")
        if (not self.enabled() or event.get("user") != settings.housing_feed_owner_user
                or event.get("channel") != settings.housing_feed_channel_id
                or not isinstance(external_ref, dict)
                or external_ref.get("type") != "housing_notice"
                or not isinstance(external_ref.get("id"), str) or not external_ref["id"]
                or not isinstance(event.get("trigger_id"), str) or not 0 < len(event["trigger_id"]) <= 200
                or not isinstance(payload.get("event_id"), str) or not 0 < len(payload["event_id"]) <= 100
                or not re.fullmatch(r"\d+\.\d+", str(event.get("message_ts", "")))):
            return {"ok": True, "ignored": True}
        with self.company.db.transaction() as conn:
            row = conn.execute("""SELECT n.payload FROM housing_feed_publications p
                JOIN outbox o ON o.id=p.id JOIN housing_feed_notices n ON n.id=p.notice_id
                WHERE p.notice_id=%s AND p.channel=%s AND o.sent_ts=%s AND o.status='delivered'
                AND n.active LIMIT 1""", (event["external_ref"]["id"], event["channel"],
                                       event["message_ts"])).fetchone()
            if not row:
                return {"ok": True, "ignored": True}
            notice = HousingNotice.model_validate(row["payload"])
            if not notice.map_location or event.get("entity_url") != notice.url:
                return {"ok": True, "ignored": True}
            conn.execute("""INSERT INTO housing_map_details
                (trigger_id,event_id,notice_id,channel,message_ts,user_id,expires_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                         (event["trigger_id"], payload["event_id"], notice.id,
                          event["channel"], event["message_ts"], event["user"],
                          now() + timedelta(minutes=2)))
        return {"ok": True, "map_details_queued": True}

    def claim(self):
        if not self.enabled():
            return None
        with self.company.db.transaction() as conn:
            conn.execute("""UPDATE housing_map_details SET status='uncertain',error='detail_sender_lease_expired'
                WHERE status='sending' AND started_at<now()-interval '30 seconds'""")
            conn.execute("""UPDATE housing_map_details SET status='stale',error='trigger_expired'
                WHERE status='pending' AND expires_at<=now()""")
            row = conn.execute("""SELECT d.*,n.payload,n.active FROM housing_map_details d
                JOIN housing_feed_notices n ON n.id=d.notice_id
                WHERE d.status='pending' AND d.expires_at>now()
                ORDER BY d.created_at FOR UPDATE OF d SKIP LOCKED LIMIT 1""").fetchone()
            if not row:
                return None
            if (not row["active"] or row["channel"] != self.company.settings.housing_feed_channel_id
                    or row["user_id"] != self.company.settings.housing_feed_owner_user):
                conn.execute("UPDATE housing_map_details SET status='stale',error='map_scope_changed' WHERE trigger_id=%s",
                             (row["trigger_id"],))
                return None
            notice = HousingNotice.model_validate(row["payload"])
            if not notice.map_location:
                conn.execute("UPDATE housing_map_details SET status='stale',error='map_location_unavailable' WHERE trigger_id=%s",
                             (row["trigger_id"],))
                return None
            conn.execute("""UPDATE housing_map_details SET status='sending',started_at=now(),attempts=attempts+1
                WHERE trigger_id=%s""", (row["trigger_id"],))
            return row, notice

    def settle(self, trigger_id, status, error=None):
        with self.company.db.transaction() as conn:
            conn.execute("""UPDATE housing_map_details SET status=%s,error=%s
                WHERE trigger_id=%s AND status='sending'""", (status, error, trigger_id))

    async def send_one(self):
        import asyncio

        claimed = await asyncio.to_thread(self.claim)
        if not claimed:
            return False
        row, notice = claimed
        token = self.credentials["reporter"]["bot_token"]
        body = {"trigger_id": row["trigger_id"], "metadata": details(notice)}
        try:
            async with httpx.AsyncClient(timeout=10, transport=self.transport) as client:
                response = await client.post("https://slack.com/api/entity.presentDetails", json=body,
                                             headers={"Authorization": "Bearer " + token})
            if response.status_code >= 500:
                await asyncio.to_thread(self.settle, row["trigger_id"], "uncertain", "slack_server_error")
            elif response.status_code == 429:
                await asyncio.to_thread(self.settle, row["trigger_id"], "blocked", "rate_limited")
            else:
                result = response.json()
                await asyncio.to_thread(self.settle, row["trigger_id"],
                                        "delivered" if result.get("ok") else "blocked",
                                        None if result.get("ok") else str(result.get("error", "slack_rejected"))[:100])
        except (httpx.HTTPError, ValueError):
            await asyncio.to_thread(self.settle, row["trigger_id"], "uncertain", "delivery_outcome_unknown")
        return True
