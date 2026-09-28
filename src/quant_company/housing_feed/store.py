from datetime import datetime, time, timedelta
from uuid import uuid4

from psycopg.types.json import Jsonb

from ..company import as_json, fingerprint, stable
from . import schedule
from .contracts import SOURCES, HousingNotice
from .render import render

LOCK = 71350249


class HousingFeedStore:
    def __init__(self, company):
        self.company, self.db = company, company.db

    def authorized(self):
        s = self.company.settings
        return (s.housing_feed_enabled and s.housing_feed_owner_user in s.slack_allowed_users
                and s.housing_feed_channel_id.startswith(("C", "G"))
                and s.housing_feed_channel_id in s.housing_feed_allowed_channels
                and s.housing_feed_channel_id not in {s.news_channel_id, s.tech_feed_channel_id,
                    s.quant_feed_channel_id, s.data_watch_channel_id, s.model_accounts_channel_id,
                    s.improvements_channel_id}
                and "reporter" in self.company.roles)

    def policy(self):
        s = self.company.settings
        return fingerprint({"version": 1, "enabled": s.housing_feed_enabled,
                            "publish": s.housing_feed_publish_enabled, "channel": s.housing_feed_channel_id,
                            "owner": s.housing_feed_owner_user, "users": s.slack_allowed_users,
                            "channels": s.housing_feed_allowed_channels, "sources": list(SOURCES)})

    def claim_sources(self):
        if not self.authorized():
            return []
        at = schedule.utcnow()
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            for source in SOURCES:
                conn.execute("INSERT INTO housing_feed_sources(id,next_at) VALUES(%s,%s) ON CONFLICT DO NOTHING", (source, at))
            rows = conn.execute("""SELECT * FROM housing_feed_sources WHERE id=ANY(%s) AND next_at<=%s
                AND (lease_until IS NULL OR lease_until<=%s) ORDER BY id FOR UPDATE SKIP LOCKED""",
                                (list(SOURCES), at, at)).fetchall()
            for row in rows:
                row["lease_token"] = uuid4()
                row["policy"] = self.policy()
                conn.execute("""UPDATE housing_feed_sources SET lease_token=%s,lease_until=%s,last_attempt=%s
                    WHERE id=%s""", (row["lease_token"], at + timedelta(minutes=15), at, row["id"]))
            return rows

    def save(self, claimed, receipt):
        at = schedule.utcnow()
        if not self.authorized() or claimed["policy"] != self.policy():
            return {"state": "stale"}
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            row = conn.execute("SELECT * FROM housing_feed_sources WHERE id=%s FOR UPDATE", (claimed["id"],)).fetchone()
            if not row or row["lease_token"] != claimed["lease_token"]:
                return {"state": "stale"}
            metadata = {k: v for k, v in receipt.items() if k != "entries"}
            if not receipt.get("ok"):
                conn.execute("""UPDATE housing_feed_sources SET error=%s,receipt=%s,next_at=%s,
                    lease_until=NULL,lease_token=NULL WHERE id=%s""",
                             (receipt.get("error", "collection_failed"), Jsonb(as_json(metadata)),
                              at + timedelta(minutes=30), row["id"]))
                return {"state": "source_error"}
            notices = [HousingNotice.model_validate(n) for n in receipt["entries"]]
            if any(n.source != row["id"] for n in notices) or len({n.id for n in notices}) != len(notices):
                raise ValueError("housing_snapshot_identity_mismatch")
            conn.execute("UPDATE housing_feed_notices SET active=false WHERE source_id=%s AND NOT(id=ANY(%s))",
                         (row["id"], [n.id for n in notices]))
            queued = 0
            for notice in notices:
                payload = notice.model_dump(mode="json")
                digest = fingerprint(payload)
                previous = conn.execute("SELECT digest FROM housing_feed_notices WHERE id=%s", (notice.id,)).fetchone()
                conn.execute("""INSERT INTO housing_feed_notices(id,source_id,payload,digest,active,checked_at)
                    VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,
                    digest=excluded.digest,active=excluded.active,checked_at=excluded.checked_at""",
                             (notice.id, notice.source, Jsonb(payload), digest,
                              notice.active(at.astimezone(schedule.KST).date()), at))
                if self.company.settings.housing_feed_publish_enabled and notice.active(at.astimezone(schedule.KST).date()):
                    queued += self.enqueue(conn, notice, digest, at, changed=bool(previous and previous["digest"] != digest))
            conn.execute("""UPDATE housing_feed_sources SET last_success=%s,error=NULL,receipt=%s,next_at=%s,
                lease_until=NULL,lease_token=NULL WHERE id=%s""",
                         (at, Jsonb(as_json(metadata)), at + timedelta(hours=1), row["id"]))
            return {"state": "collected", "notices": len(notices), "queued": queued}

    def enqueue(self, conn, notice, digest, at, *, changed=False, reminders=()):
        s = self.company.settings
        event_key = f"{notice.id}:{digest}:" + (f"reminder:{at.astimezone(schedule.KST).date()}" if reminders else "notice")
        if conn.execute("SELECT 1 FROM housing_feed_publications WHERE channel=%s AND event_key=%s",
                        (s.housing_feed_channel_id, event_key)).fetchone():
            return 0
        project_id = stable(f"housing-feed-project:{s.housing_feed_channel_id}:{s.housing_feed_owner_user}")
        conn.execute("""INSERT INTO projects(id,title,instruction,owner_user,channel)
            VALUES(%s,'housing-feed','서울·경기 공식 주택분양 공고와 접수 일정 알림',%s,%s) ON CONFLICT DO NOTHING""",
                     (project_id, s.housing_feed_owner_user, s.housing_feed_channel_id))
        project = conn.execute("SELECT * FROM projects WHERE id=%s", (project_id,)).fetchone()
        identity = stable(f"housing-feed:{s.housing_feed_channel_id}:{event_key}")
        self.company._message(conn, project, None, "reporter", "housing_feed",
                              render(notice, at, changed=changed, reminders=reminders), message_id=identity)
        expires = at + timedelta(days=1)
        if reminders:
            expires = datetime.combine(at.astimezone(schedule.KST).date(), time(15), schedule.KST)
        conn.execute("""INSERT INTO housing_feed_publications
            (id,notice_id,notice_digest,channel,event_key,policy_digest,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                     (identity, notice.id, digest, s.housing_feed_channel_id, event_key, self.policy(), expires))
        conn.execute("UPDATE outbox SET next_at=%s WHERE id=%s", (schedule.delivery_time(at), identity))
        return 1

    def reminders(self):
        if not self.authorized() or not self.company.settings.housing_feed_publish_enabled:
            return 0
        at, count = schedule.utcnow(), 0
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            rows = conn.execute("""SELECT n.* FROM housing_feed_notices n JOIN housing_feed_sources s ON s.id=n.source_id
                WHERE n.active AND s.error IS NULL AND n.checked_at>=%s AND n.first_seen<%s""",
                                (at - timedelta(hours=3), datetime.combine(
                                    at.astimezone(schedule.KST).date(), time(9), schedule.KST))).fetchall()
            for row in rows:
                notice = HousingNotice.model_validate(row["payload"])
                if notice.active(at.astimezone(schedule.KST).date()) and (items := schedule.reminder(notice, at)):
                    count += self.enqueue(conn, notice, row["digest"], at, reminders=items)
        return count

    def gate(self, conn, row, *, claimed=False):
        at = schedule.utcnow()
        p = conn.execute("""SELECT p.*,n.digest,n.active,n.payload,n.checked_at,s.error FROM housing_feed_publications p
            JOIN housing_feed_notices n ON n.id=p.notice_id JOIN housing_feed_sources s ON s.id=n.source_id
            WHERE p.id=%s""", (row["id"],)).fetchone()
        s = self.company.settings
        if not (p and self.authorized() and s.housing_feed_publish_enabled and p["active"]
                and p["policy_digest"] == self.policy() and p["notice_digest"] == p["digest"]
                and p["expires_at"] > at and row["channel"] == s.housing_feed_channel_id
                and HousingNotice.model_validate(p["payload"]).active(at.astimezone(schedule.KST).date())):
            conn.execute("UPDATE outbox SET status='stale',error='housing_policy_or_notice_changed' WHERE id=%s", (row["id"],))
            return False
        if p["error"] or p["checked_at"] < at - timedelta(hours=3):
            conn.execute("""UPDATE outbox SET status='pending',next_at=%s,started_at=NULL,
                attempts=GREATEST(0,attempts-%s),error='housing_source_needs_refresh' WHERE id=%s""",
                         (at + timedelta(minutes=10), int(claimed), row["id"]))
            return False
        conn.execute("INSERT INTO housing_feed_delivery(channel,next_at) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                     (row["channel"], at))
        pacing = conn.execute("SELECT next_at FROM housing_feed_delivery WHERE channel=%s FOR UPDATE",
                              (row["channel"],)).fetchone()["next_at"]
        due = schedule.delivery_time(max(pacing, at))
        if due > at:
            conn.execute("""UPDATE outbox SET status='pending',next_at=%s,started_at=NULL,
                attempts=GREATEST(0,attempts-%s),error='housing_delivery_window_or_spacing' WHERE id=%s""",
                         (due, int(claimed), row["id"]))
            return False
        if claimed:
            conn.execute("UPDATE housing_feed_delivery SET next_at=%s WHERE channel=%s",
                         (at + timedelta(minutes=1), row["channel"]))
        return True

    def status(self):
        with self.db.transaction() as conn:
            return as_json({"enabled": self.company.settings.housing_feed_enabled,
                            "publish_enabled": self.company.settings.housing_feed_publish_enabled,
                            "authorized": self.authorized(), "channel": self.company.settings.housing_feed_channel_id,
                            "sources": conn.execute("SELECT * FROM housing_feed_sources ORDER BY id").fetchall(),
                            "notices": conn.execute("SELECT id,payload,active,checked_at FROM housing_feed_notices ORDER BY id").fetchall(),
                            "deliveries": conn.execute("""SELECT p.event_key,o.status,o.error,o.sent_ts,o.attempts FROM
                                housing_feed_publications p JOIN outbox o ON o.id=p.id ORDER BY o.created_at DESC LIMIT 50""").fetchall()})
