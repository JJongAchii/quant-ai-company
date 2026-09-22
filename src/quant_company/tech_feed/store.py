"""Feed checkpoints and publication receipts, with no company turns or model budgets."""

from datetime import timedelta
from uuid import uuid4

from psycopg.types.json import Jsonb

from ..company import as_json, fingerprint, stable
from . import schedule
from .contracts import TECH_FEED_AGENT, TechFeedItem, load_sources
from .feeds import render

LOCK = 71350241


class TechFeedStore:
    def __init__(self, company):
        self.company = company
        self.db = company.db

    def sources(self):
        return {source.id: source for source in load_sources(self.company.settings.tech_feed_sources_file)}

    def authorized(self):
        settings = self.company.settings
        role = self.company.roles.get(TECH_FEED_AGENT)
        return (settings.tech_feed_enabled and settings.tech_feed_owner_user in settings.slack_allowed_users
                and settings.tech_feed_channel_id in settings.slack_allowed_channels
                and settings.tech_feed_channel_id.startswith(("C", "G")) and role is not None and not role.active
                and not (settings.company_news_enabled and settings.news_channel_id == settings.tech_feed_channel_id))

    def policy(self):
        settings = self.company.settings
        return fingerprint({"version": 1, "enabled": settings.tech_feed_enabled,
                            "publish": settings.tech_feed_publish_enabled,
                            "channel": settings.tech_feed_channel_id, "owner": settings.tech_feed_owner_user,
                            "users": settings.slack_allowed_users, "channels": settings.slack_allowed_channels,
                            "sources": [s.model_dump() for s in self.sources().values()]})

    def claim_sources(self):
        sources = self.sources()
        at = schedule.utcnow()
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            conn.execute("UPDATE tech_feed_sources SET enabled=false WHERE NOT(id=ANY(%s))", (list(sources),))
            for source in sources.values():
                config = source.model_dump()
                digest = fingerprint(config)
                conn.execute("""INSERT INTO tech_feed_sources(id,config,config_digest,enabled,next_at)
                    VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET
                    config=excluded.config,config_digest=excluded.config_digest,enabled=excluded.enabled,
                    etag=CASE WHEN tech_feed_sources.config_digest=excluded.config_digest THEN tech_feed_sources.etag END,
                    modified=CASE WHEN tech_feed_sources.config_digest=excluded.config_digest THEN tech_feed_sources.modified END,
                    initialized_at=CASE WHEN tech_feed_sources.config_digest=excluded.config_digest
                                        THEN tech_feed_sources.initialized_at END,
                    next_at=CASE WHEN tech_feed_sources.config_digest=excluded.config_digest
                                 THEN tech_feed_sources.next_at ELSE excluded.next_at END""",
                             (source.id, Jsonb(config), digest, source.enabled, at))
            rows = conn.execute("""SELECT * FROM tech_feed_sources WHERE enabled AND next_at<=%s
                AND (lease_until IS NULL OR lease_until<=%s) ORDER BY next_at,id
                FOR UPDATE SKIP LOCKED LIMIT 12""", (at, at)).fetchall()
            for row in rows:
                row["lease_token"] = uuid4()
                conn.execute("UPDATE tech_feed_sources SET lease_until=%s,lease_token=%s,last_attempt=%s WHERE id=%s",
                             (at + timedelta(minutes=10), row["lease_token"], at, row["id"]))
            return rows

    def save(self, claimed, receipt):
        at = schedule.utcnow()
        sources = self.sources()
        spec = sources.get(claimed["id"])
        if not self.authorized() or not spec or not spec.enabled or fingerprint(spec.model_dump()) != claimed["config_digest"]:
            return {"state": "stale"}
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            source = conn.execute("SELECT * FROM tech_feed_sources WHERE id=%s FOR UPDATE", (claimed["id"],)).fetchone()
            if not source or source["lease_token"] != claimed["lease_token"] or source["config_digest"] != claimed["config_digest"]:
                return {"state": "stale"}
            if not receipt.get("ok"):
                delay = min(3600, 600 * 2 ** min(source["failures"], 3))
                conn.execute("""UPDATE tech_feed_sources SET failures=failures+1,error=%s,receipt=%s,
                    lease_until=NULL,lease_token=NULL,next_at=%s WHERE id=%s""",
                             (receipt.get("error", "feed_failed"), Jsonb(as_json(receipt)),
                              at + timedelta(seconds=delay), source["id"]))
                return {"state": "source_error"}
            added, queued = 0, 0
            baseline = source["initialized_at"] is None
            for entry in receipt.get("entries", []):
                item = TechFeedItem.model_validate(entry).model_dump()
                if not spec.allows_article(item["url"]):
                    raise ValueError("unregistered_tech_article")
                identity = fingerprint([source["id"], item["guid"]])
                date = item["event_at"]
                fresh = date is not None and at - timedelta(hours=72) <= date <= at + timedelta(minutes=5)
                state = ("baseline" if baseline else "historical_or_undated" if not fresh else
                         "preview" if not self.company.settings.tech_feed_publish_enabled else "ready")
                inserted = conn.execute("""INSERT INTO tech_feed_items
                    (id,source_id,guid,url,title,description,event_at,time_kind,source_digest,collected_at,state)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
                                        (identity, source["id"], item["guid"], item["url"], item["title"],
                                         item["description"], date, item["time_kind"], source["config_digest"], at, state)).fetchone()
                if not inserted:
                    continue
                added += 1
                if state == "ready":
                    queued += self.enqueue(conn, identity, item, spec, at)
            metadata = {k: v for k, v in receipt.items() if k != "entries"}
            conn.execute("""UPDATE tech_feed_sources SET initialized_at=COALESCE(initialized_at,%s),last_success=%s,
                next_at=%s,lease_until=NULL,lease_token=NULL,failures=0,error=NULL,receipt=%s,
                etag=COALESCE(%s,etag),modified=COALESCE(%s,modified) WHERE id=%s""",
                         (at, at, at + timedelta(minutes=10), Jsonb(as_json(metadata)), receipt.get("etag"),
                          receipt.get("modified"), source["id"]))
            return {"state": "baselined" if baseline else "collected", "added": added, "queued": queued}

    def enqueue(self, conn, identity, item, source, at):
        settings = self.company.settings
        if conn.execute("SELECT 1 FROM tech_feed_publications WHERE channel=%s AND url=%s",
                        (settings.tech_feed_channel_id, item["url"])).fetchone():
            conn.execute("UPDATE tech_feed_items SET state='duplicate' WHERE id=%s", (identity,))
            return 0
        project_id = stable(f"tech-feed-project:{settings.tech_feed_channel_id}:{settings.tech_feed_owner_user}")
        conn.execute("""INSERT INTO projects(id,title,instruction,owner_user,channel)
            VALUES(%s,'tech-feed','RSS/Atom link delivery without model execution',%s,%s) ON CONFLICT DO NOTHING""",
                     (project_id, settings.tech_feed_owner_user, settings.tech_feed_channel_id))
        project = conn.execute("SELECT * FROM projects WHERE id=%s", (project_id,)).fetchone()
        message_id = stable(f"tech-feed:{settings.tech_feed_channel_id}:{item['url']}")
        self.company._message(conn, project, None, TECH_FEED_AGENT, "tech_feed", render(item, source),
                              message_id=message_id)
        conn.execute("""INSERT INTO tech_feed_publications(id,item_id,channel,owner_user,url,policy_digest,expires_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s)""", (message_id, identity, settings.tech_feed_channel_id,
                                            settings.tech_feed_owner_user, item["url"], self.policy(),
                                            item["event_at"] + timedelta(hours=72)))
        conn.execute("UPDATE outbox SET next_at=%s WHERE id=%s", (schedule.delivery_time(at), message_id))
        conn.execute("UPDATE tech_feed_items SET state='queued' WHERE id=%s", (identity,))
        return 1

    def gate(self, conn, row, *, claimed=False):
        """Check policy at claim and immediately before HTTP; reserve the spacing only before HTTP."""
        at = schedule.utcnow()
        publication = conn.execute("SELECT * FROM tech_feed_publications WHERE id=%s", (row["id"],)).fetchone()
        settings = self.company.settings
        if not (publication and self.authorized() and settings.tech_feed_publish_enabled
                and publication["policy_digest"] == self.policy() and publication["expires_at"] > at
                and row["channel"] == settings.tech_feed_channel_id):
            conn.execute("UPDATE outbox SET status='stale',error='tech_feed_policy_or_freshness_changed' WHERE id=%s",
                         (row["id"],))
            return False
        conn.execute("INSERT INTO tech_feed_delivery(channel,next_at) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                     (row["channel"], at))
        pacing = conn.execute("SELECT next_at FROM tech_feed_delivery WHERE channel=%s FOR UPDATE",
                              (row["channel"],)).fetchone()["next_at"]
        until = schedule.delivery_time(max(at, pacing))
        if until > at:
            conn.execute("""UPDATE outbox SET status='pending',next_at=%s,started_at=NULL,
                attempts=GREATEST(0,attempts-%s),error='tech_feed_delivery_window_or_spacing' WHERE id=%s""",
                         (until, int(claimed), row["id"]))
            return False
        if claimed:
            conn.execute("UPDATE tech_feed_delivery SET next_at=%s WHERE channel=%s",
                         (at + timedelta(minutes=1), row["channel"]))
        return True

    def status(self):
        with self.db.transaction() as conn:
            return as_json({
                "enabled": self.company.settings.tech_feed_enabled,
                "publish_enabled": self.company.settings.tech_feed_publish_enabled,
                "authorized": self.authorized(), "channel": self.company.settings.tech_feed_channel_id,
                "model_execution": "not_used", "delivery_window": "06:00–24:00 Asia/Seoul",
                "sources": conn.execute("""SELECT id,enabled,initialized_at,last_success,last_attempt,
                    next_at,failures,error,receipt FROM tech_feed_sources ORDER BY id""").fetchall(),
                "items": conn.execute("SELECT state,count(*) AS count FROM tech_feed_items GROUP BY state").fetchall(),
                "recent_items": conn.execute("""SELECT source_id,title,url,description,time_kind,event_at,state
                    FROM tech_feed_items ORDER BY collected_at DESC,event_at DESC NULLS LAST LIMIT 20""").fetchall(),
                "deliveries": conn.execute("""SELECT p.id,p.url,o.status,o.error,o.next_at,o.sent_ts,o.attempts
                    FROM tech_feed_publications p JOIN outbox o ON o.id=p.id ORDER BY o.created_at DESC LIMIT 20""").fetchall(),
            })
