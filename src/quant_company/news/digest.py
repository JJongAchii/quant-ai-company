"""Group verified overnight publications without losing event or delivery receipts."""

from psycopg.types.json import Jsonb

from ..company import stable
from . import schedule
from .store import NewsStore


class NewsDigestStore(NewsStore):
    def flush(self):
        settings = self.company.settings
        if not settings.news_delivery_window_enabled or not self.authorized() or not settings.news_publish_enabled:
            return {"state": "paused"}
        at = schedule.utcnow()
        day = schedule.opening(at).date()
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(71350227)")
            if schedule.quiet(settings, at):
                # Carry pending automatic posts across midnight even if dispatch is temporarily stopped.
                conn.execute("""UPDATE news_publications p SET morning_day=%s FROM outbox o
                    WHERE p.id=o.id AND o.status='pending' AND p.digest_id IS NULL""", (day,))
                return {"state": "quiet"}
            existing = conn.execute("SELECT id FROM news_digests WHERE day=%s AND channel=%s AND owner_user=%s AND part=0",
                                    (day, settings.news_channel_id, settings.news_owner_user)).fetchone()
            if existing:
                # Late originals are reviewed normally; never mutate/replay an already frozen digest.
                conn.execute("""UPDATE news_publications p SET morning_day=NULL FROM outbox o
                    WHERE p.id=o.id AND o.status='pending' AND p.digest_id IS NULL AND p.morning_day<=%s""", (day,))
                return {"state": "already_prepared"}
            conn.execute("""UPDATE news_publications p SET morning_day=%s FROM outbox o
                WHERE p.id=o.id AND o.status='pending' AND o.created_at<%s
                AND p.digest_id IS NULL AND p.morning_day IS NULL""", (day, schedule.opening(at)))
            pending = conn.execute("""SELECT 1 FROM news_articles a JOIN news_sources s ON s.id=a.source_id
                WHERE a.state IN ('ready','selected') AND a.collected_at>=%s AND a.collected_at<%s
                AND s.enabled AND s.config->>'use_for_summary'='true' AND a.source_digest=s.config_digest
                AND a.published_at>=now()-make_interval(hours=>%s) LIMIT 1""",
                                   (schedule.overnight_start(at), schedule.opening(at), settings.news_max_age_hours)).fetchone()
            if pending or conn.execute("SELECT 1 FROM news_reviews WHERE state='running' AND bundle->>'morning_day'=%s", (str(day),)).fetchone():
                return {"state": "awaiting_review"}
            rows = conn.execute("""SELECT o.*,p.expires_at,p.policy_digest,p.article_ids FROM outbox o
                JOIN news_publications p ON p.id=o.id WHERE o.status='pending' AND p.morning_day<=%s
                AND p.digest_id IS NULL ORDER BY o.created_at,o.id FOR UPDATE OF o""", (day,)).fetchall()
            valid = []
            for row in rows:
                if self.delivery_allowed(conn, row):
                    valid.append(row)
                else:
                    conn.execute("UPDATE outbox SET status='stale',error='news_policy_or_freshness_changed' WHERE id=%s", (row["id"],))
            if not valid:
                return {"state": "idle"}
            root_id = stable(f"news-digest:{day}:{settings.news_channel_id}:{settings.news_owner_user}:0")
            project = conn.execute("""INSERT INTO projects(id,title,instruction,owner_user,channel)
                VALUES(%s,%s,%s,%s,%s) RETURNING *""",
                                   (stable("news-digest-project:"+root_id), f"{day} 밤사이 주요 뉴스",
                                    "Reporter의 야간 뉴스 모음. 각 사건의 원문을 확인해 후속 질문에 답한다.",
                                    settings.news_owner_user, settings.news_channel_id)).fetchone()
            # A normal night is one message. Large catch-ups use replies under one root, not a channel flood.
            groups = []
            for row in valid:
                if not groups or sum(len(r["text"]) for r in groups[-1])+len(row["text"]) > 28000:
                    groups.append([])
                groups[-1].append(row)
            for part, members in enumerate(groups):
                identity = stable(f"news-digest:{day}:{settings.news_channel_id}:{settings.news_owner_user}:{part}")
                header = f"*☀️ {day:%m/%d} 밤사이 주요 뉴스*" if part == 0 else "*밤사이 주요 뉴스 · 이어서*"
                text = header + "\n야간 수집분과 발송 대기분 중 원문 검토를 마친 주요 소식입니다.\n\n" + "\n\n──────────\n\n".join(r["text"] for r in members)
                self.company._message(conn, project, None, "reporter", "news_digest", text, message_id=identity)
                conn.execute("""INSERT INTO news_digests(id,project_id,day,part,root_id,channel,owner_user,member_ids,policy_digest,expires_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                             (identity, project["id"], day, part, root_id, settings.news_channel_id, settings.news_owner_user,
                              Jsonb([str(r["id"]) for r in members]), self.policy(), min(r["expires_at"] for r in members)))
                if part:
                    conn.execute("UPDATE outbox SET status='awaiting_digest_root' WHERE id=%s", (identity,))
                for row in members:
                    conn.execute("UPDATE news_publications SET digest_id=%s WHERE id=%s", (identity, row["id"]))
                    conn.execute("UPDATE outbox SET status='bundled' WHERE id=%s", (row["id"],))
                    for article in conn.execute("SELECT * FROM news_articles WHERE id=ANY(%s)", (row["article_ids"],)).fetchall():
                        conn.execute("""INSERT INTO sources(id,title,uri,content,available_at,approved,project_id,synthetic)
                            VALUES(%s,%s,%s,%s,%s,true,%s,false) ON CONFLICT DO NOTHING""",
                                     (f"news-digest:{root_id}:{article['id'][:16]}", article["title"], article["url"],
                                      article["content"], at, project["id"]))
            return {"state": "queued", "members": len(valid), "parts": len(groups), "id": root_id}

    def allowed(self, conn, row):
        digest = conn.execute("SELECT * FROM news_digests WHERE id=%s", (row["id"],)).fetchone()
        if not (digest and self.authorized() and self.company.settings.news_publish_enabled
                and digest["policy_digest"] == self.policy() and digest["expires_at"] > schedule.utcnow()
                and row["channel"] == self.company.settings.news_channel_id):
            return False
        members = conn.execute("SELECT * FROM outbox WHERE id::text=ANY(%s)", (digest["member_ids"],)).fetchall()
        return len(members) == len(digest["member_ids"]) and all(self.delivery_allowed(conn, m) for m in members)

    @staticmethod
    def settle_members(conn, row, status, sent_ts=None, error=None):
        digest = conn.execute("SELECT * FROM news_digests WHERE id=%s", (row["id"],)).fetchone()
        if not digest or status == "pending":
            return
        conn.execute("""UPDATE outbox SET status=%s,sent_ts=%s,error=%s WHERE id::text=ANY(%s) AND status='bundled'""",
                     (status, sent_ts, error, digest["member_ids"]))
        if digest["part"] == 0:
            if status == "delivered":
                conn.execute("""UPDATE outbox SET status='pending',thread_ts=%s WHERE status='awaiting_digest_root'
                    AND id IN (SELECT id FROM news_digests WHERE root_id=%s AND part>0)""", (sent_ts, digest["root_id"]))
            else:
                parts = conn.execute("""UPDATE outbox SET status='blocked',error='digest_root_not_delivered'
                    WHERE status='awaiting_digest_root' AND id IN
                    (SELECT id FROM news_digests WHERE root_id=%s AND part>0) RETURNING id""", (digest["root_id"],)).fetchall()
                for part in parts:
                    NewsDigestStore.settle_members(conn, part, "blocked", error="digest_root_not_delivered")
