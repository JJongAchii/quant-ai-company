"""Operator-reviewed migration of certainly-unsent v1 publications; never sends HTTP."""

import hashlib
from datetime import timedelta

from ..company import as_json, fingerprint, stable
from . import schedule
from .contracts import TECH_FEED_AGENT, TechFeedItem
from .feeds import render
from .store import LOCK


def legacy_policy(store, channels, users):
    settings = store.company.settings
    return fingerprint({"version": 1, "enabled": settings.tech_feed_enabled,
                        "publish": settings.tech_feed_publish_enabled,
                        "channel": settings.tech_feed_channel_id, "owner": settings.tech_feed_owner_user,
                        "users": users, "channels": channels,
                        "sources": [s.model_dump() for s in store.sources().values()]})


def candidates(store, old_policy):
    with store.db.transaction() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        return [str(row["id"]) for row in conn.execute("""SELECT p.id FROM tech_feed_publications p
            JOIN outbox o ON o.id=p.id WHERE p.policy_digest=%s AND p.expires_at>%s
            AND o.status='stale' AND o.error='tech_feed_policy_or_freshness_changed'
            AND o.attempts=0 AND o.sent_ts IS NULL AND o.started_at IS NULL
            ORDER BY o.created_at,o.id""", (old_policy, schedule.utcnow())).fetchall()]


def reconcile(store, identities, old_channels, old_users, old_policy, *, apply_digest=None):
    settings = store.company.settings
    if (not identities or len(set(identities)) != len(identities) or len(identities) > 1000
            or settings.tech_feed_channel_id not in old_channels
            or settings.tech_feed_owner_user not in old_users
            or not store.authorized() or not settings.tech_feed_publish_enabled
            or legacy_policy(store, old_channels, old_users) != old_policy):
        raise ValueError("tech_reconciliation_scope_invalid")
    at, current_policy, sources = schedule.utcnow(), store.policy(), store.sources()
    rows = []
    with store.db.transaction() as conn:
        if apply_digest is None:
            conn.execute("SET TRANSACTION READ ONLY")
        else:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
        for identity in identities:
            publication = conn.execute("SELECT * FROM tech_feed_publications WHERE id=%s",
                                       (identity,)).fetchone()
            outbox = conn.execute("""SELECT o.*,m.kind AS message_kind,m.author,
                m.text AS message_text,p.owner_user AS project_owner,p.channel AS project_channel,
                p.status AS project_status,p.revision AS project_revision
                FROM outbox o JOIN messages m ON m.id=o.id JOIN projects p ON p.id=o.project_id
                WHERE o.id=%s""" + (" FOR UPDATE OF o" if apply_digest else ""), (identity,)).fetchone()
            if (not publication or not outbox or publication["policy_digest"] != old_policy
                    or publication["channel"] != settings.tech_feed_channel_id
                    or publication["owner_user"] != settings.tech_feed_owner_user
                    or outbox["channel"] != settings.tech_feed_channel_id
                    or outbox["project_channel"] != settings.tech_feed_channel_id
                    or outbox["project_owner"] != settings.tech_feed_owner_user
                    or outbox["project_status"] != "active"
                    or outbox["revision"] != outbox["project_revision"]
                    or outbox["agent"] != TECH_FEED_AGENT or outbox["author"] != TECH_FEED_AGENT
                    or outbox["message_kind"] != "tech_feed" or outbox["status"] != "stale"
                    or outbox["error"] != "tech_feed_policy_or_freshness_changed"
                    or outbox["attempts"] != 0 or outbox["sent_ts"] is not None
                    or outbox["started_at"] is not None or publication["expires_at"] <= at):
                raise ValueError("tech_not_certainly_unsent:" + identity)
            item = conn.execute("SELECT * FROM tech_feed_items WHERE id=%s",
                                (publication["item_id"],)).fetchone()
            source = sources.get(item["source_id"]) if item else None
            if (not item or not source or not source.enabled or item["state"] != "queued"
                    or item["source_digest"] != fingerprint(source.model_dump())
                    or not source.allows_article(item["url"]) or item["url"] != publication["url"]
                    or stable(f"tech-feed:{settings.tech_feed_channel_id}:{item['url']}") != identity
                    or item["event_at"] is None or item["event_at"] > at + timedelta(minutes=5)
                    or item["event_at"] + timedelta(hours=72) != publication["expires_at"]):
                raise ValueError("tech_original_changed:" + identity)
            text = render(TechFeedItem.model_validate({k: item[k] for k in TechFeedItem.model_fields}).model_dump(), source)
            if text != outbox["text"] or text != outbox["message_text"]:
                raise ValueError("tech_saved_card_changed:" + identity)
            rows.append({"id": identity, "title": item["title"], "url": item["url"],
                         "expires_at": publication["expires_at"],
                         "card_sha256": hashlib.sha256(text.encode()).hexdigest()})
        plan = as_json({"old_policy": old_policy, "new_policy": current_policy,
                        "old_channels": old_channels, "old_users": old_users, "publications": rows})
        digest = fingerprint(plan)
        if apply_digest is not None:
            if digest != apply_digest:
                raise ValueError("tech_reconciliation_plan_changed")
            for row in rows:
                conn.execute("UPDATE tech_feed_publications SET policy_digest=%s WHERE id=%s",
                             (current_policy, row["id"]))
                conn.execute("""UPDATE outbox SET status='pending',error=NULL,next_at=%s WHERE id=%s""",
                             (schedule.delivery_time(at), row["id"]))
    return {"state": "applied" if apply_digest else "planned", "plan_digest": digest, **plan,
            "database_writes": len(rows) * 2 if apply_digest else 0,
            "model_calls": 0, "direct_slack_writes": 0}
