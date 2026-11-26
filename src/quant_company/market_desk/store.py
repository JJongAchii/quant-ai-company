from datetime import datetime, timedelta

from psycopg.types.json import Jsonb

from ..briefing import schedule
from ..briefing.schedule import KST
from . import editor, lead

DAILY_CALLS = 2


class DeskStore:
    def __init__(self, company):
        self.db = company.db
        self.settings = company.settings

    def briefs(self, conn, at):
        rows = conn.execute("""SELECT DISTINCT ON (e.kind) e.id, e.kind, e.cutoff, m.text, o.sent_ts
            FROM brief_editions e JOIN brief_messages b ON b.edition_id=e.id AND b.part=0
            JOIN messages m ON m.id=b.id JOIN outbox o ON o.id=m.id
            WHERE e.publish AND o.status='delivered' AND o.sent_ts IS NOT NULL AND e.channel=%s AND e.owner_user=%s
            AND e.due_at BETWEEN %s AND %s ORDER BY e.kind,e.due_at DESC""",
                            (self.settings.briefing_channel_id, self.settings.briefing_owner_user,
                             at - timedelta(days=12), at)).fetchall()
        return sorted(({"id": str(r["id"]), "kind": r["kind"], "body": r["text"],
                        "as_of": r["cutoff"].isoformat(), "published_at": slack_time(r["sent_ts"])}
                       for r in rows), key=lambda x: x["published_at"], reverse=True)

    def news(self, conn, at):
        # Only messages actually delivered to the owner's configured news channel, including a morning digest.
        hot = conn.execute("""SELECT n.event_id, n.article_ids, e.headline, m.text,
                COALESCE(o.sent_ts, delivery.sent_ts) AS sent_ts FROM news_publications n
            JOIN news_events e ON e.id=n.event_id JOIN messages m ON n.id=m.id
            JOIN projects p ON p.id=e.project_id LEFT JOIN outbox o ON o.id=m.id
            LEFT JOIN outbox delivery ON delivery.id=n.digest_id
            WHERE p.channel=%s AND p.owner_user=%s AND (o.status='delivered' OR delivery.status='delivered')
              AND COALESCE(o.sent_ts,delivery.sent_ts) IS NOT NULL
              AND m.created_at BETWEEN %s AND %s ORDER BY m.created_at DESC LIMIT 12""",
                           (self.settings.news_channel_id, self.settings.news_owner_user,
                            at - timedelta(days=2), at)).fetchall()
        hot_ids = {a: str(r["event_id"]) for r in hot for a in r["article_ids"]}
        rows = conn.execute("""WITH recent AS MATERIALIZED (SELECT id FROM news_articles
                WHERE content IS NOT NULL AND published_at BETWEEN %s AND %s AND collected_at<=%s
                ORDER BY published_at DESC LIMIT 256)
            SELECT a.id,a.url,a.title,a.published_at,left(a.content,4500) AS content,
                s.config->>'publisher' AS publisher,s.config->>'license_url' AS license_url,
                s.config->>'license_name' AS license_name FROM news_articles a JOIN news_sources s ON s.id=a.source_id
            JOIN recent ON recent.id=a.id
            WHERE s.enabled AND s.config->>'use_for_summary'='true' AND a.source_digest=s.config_digest
            ORDER BY a.published_at DESC LIMIT 64""", (at - timedelta(hours=3), at, at)).fetchall()
        docs = [{**r, "published_at": r["published_at"].isoformat(), "kind": "news", "event_id": hot_ids.get(r["id"])}
                for r in rows]
        reports = {}
        for r in hot:
            key = "source-" + str(r["event_id"])
            if key not in reports:
                reports[key] = {"id": key, "kind": "news", "origin": "reporter", "status": "reported", "headline": r["headline"],
                                "fact": r["text"], "interpretation": "", "watch": "", "observation": "",
                                "published_at": slack_time(r["sent_ts"]), "observed_at": None,
                                "sources": [editor.public_source(d) for d in docs if d["id"] in r["article_ids"]][:8]}
        return docs, reports

    def register(self, conn, packet, at, limit=DAILY_CALLS):
        day = at.astimezone(KST).date()
        move, docs = packet["move"], packet["docs"]
        aliases = [packet["key"], *(editor.source_key(d) for d in docs)]
        aliases += ["headline-" + editor.digest("".join(d["title"].casefold().split()))
                    for d in docs if d["kind"] == "news"]
        # Freeze the first packet for each material change; polling, syndication and retries keep the same request.
        prior = conn.execute("""SELECT issue_id FROM market_desk_revisions WHERE day=%s AND aliases && %s::text[]
            ORDER BY (issue_id=%s) DESC,created_at LIMIT 1""", (day, aliases, packet["key"])).fetchone()
        key = (prior["issue_id"] if prior else editor.source_key(docs[0]) if docs else packet["key"])
        material = sorted({editor.digest(" ".join(d["content"].split())) for d in docs})
        revision = editor.digest([str(day), key, move["id"] if move else None, material])
        if conn.execute("SELECT 1 FROM market_desk_revisions WHERE revision=%s", (revision,)).fetchone():
            return
        available = any(len(d["content"]) >= 120 for d in docs)
        used = conn.execute("""SELECT count(*) AS total,count(*) FILTER (WHERE packet->>'kind'<>'move') AS source_only
            FROM market_desk_revisions WHERE day=%s AND request IS NOT NULL""", (day,)).fetchone()
        # Leave one of the two daily places for a price-led story.
        within_budget = used["total"] < limit and (move is not None or used["source_only"] == 0)
        req = editor.request_for(revision, packet) if available and within_budget else None
        article = editor.initial(packet, key, at)
        conn.execute("""INSERT INTO market_desk_revisions
            (revision, issue_id, aliases, day, created_at, packet, article, request, state, next_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                     (revision, key, aliases, day, at, Jsonb(packet), Jsonb(article),
                      Jsonb(req.model_dump(mode="json")) if req else None, "queued" if req else "observed", at))

    def session_reports(self, conn, observed_at):
        if not observed_at:
            return []
        local = observed_at.astimezone(KST)
        start = local.replace(hour=9, minute=0, second=0, microsecond=0)
        cutoff = local - timedelta(minutes=20)
        if cutoff < start:
            return []
        rows = conn.execute("""WITH recent AS MATERIALIZED (
                SELECT source_id,source_digest,title,url,published_at FROM news_articles
                WHERE published_at BETWEEN %s AND %s AND collected_at<=%s ORDER BY published_at DESC LIMIT 4000)
            SELECT a.title,a.url,a.published_at,s.config->>'publisher' AS publisher,
                s.config->>'license_url' AS license_url,s.config->>'license_name' AS license_name
            FROM recent a JOIN news_sources s ON s.id=a.source_id
            WHERE s.enabled AND s.config->>'use_for_summary'='true' AND a.source_digest=s.config_digest
                AND (a.title LIKE '%%코스피%%' OR a.title LIKE '%%코스닥%%')
            ORDER BY a.published_at DESC LIMIT 100""", (start, cutoff, observed_at)).fetchall()
        return editor.market_reports(rows)

    def lead_sources(self, conn, at, previous_close):
        # Include both observation windows, even after weekends, without traversing every general-news title.
        return conn.execute("""WITH candidates AS MATERIALIZED (
                SELECT id,source_id,source_digest,title,url,published_at,collected_at FROM news_articles
                WHERE content IS NOT NULL AND published_at BETWEEN %s AND %s AND collected_at<=%s
                AND (title LIKE '%%코스피%%' OR title LIKE '%%코스닥%%' OR title LIKE '%%기준금리%%'
                     OR title LIKE '%%통화정책방향%%' OR title LIKE '%%원/달러%%' OR title LIKE '%%원·달러%%'
                     OR title LIKE '%%국고채 금리%%' OR title LIKE '%%뉴욕증시%%')
                ORDER BY published_at DESC LIMIT 220)
            SELECT c.id,c.title,c.url,c.published_at,c.collected_at,left(a.content,4600) AS content,
                s.config->>'publisher' AS publisher,s.config->>'license_url' AS license_url,
                s.config->>'license_name' AS license_name
            FROM candidates c JOIN news_articles a ON a.id=c.id JOIN news_sources s ON s.id=c.source_id
            WHERE s.enabled AND s.config->>'use_for_summary'='true' AND s.config_digest=c.source_digest
            ORDER BY c.published_at DESC""", (previous_close, at, at)).fetchall()

    def register_lead(self, conn, at, observation):
        day = at.astimezone(KST).date()
        changes = schedule.overrides(self.settings)
        close = schedule.close("KR", day, changes)
        phase = lead.phase_at(at, close)
        if not phase or (phase == "intraday" and not observation):
            return
        key = f"session/{day}/{phase}"
        used = conn.execute("""SELECT count(*) AS total, count(*) FILTER (WHERE issue_id=%s) AS this_edition
            FROM market_desk_revisions WHERE day=%s AND request IS NOT NULL""", (key, day)).fetchone()
        if used["this_edition"] or used["total"] >= (DAILY_CALLS if phase == "close" else DAILY_CALLS - 1):
            return
        previous_close = schedule.close("KR", schedule.previous("KR", day, changes), changes)
        sources = self.lead_sources(conn, lead.source_cutoff(at, close), previous_close)
        packet = lead.packet_for(sources, at, close, previous_close, observation)
        if not packet:
            return
        revision = editor.digest(packet)
        req = lead.request_for(revision, packet)
        conn.execute("""INSERT INTO market_desk_revisions
            (revision,issue_id,aliases,day,created_at,packet,article,request,state,next_at)
            VALUES(%s,%s,%s,%s,%s,%s,'{}',%s,'queued',%s)""",
                     (revision, key, [key], day, at, Jsonb(packet), Jsonb(req.model_dump(mode="json")), at))

    def publish(self, at, market, filings, filings_at, is_session, write):
        with self.db.transaction() as conn:
            # Serialize the daily allowance, revisions and the single S3 board even during a rolling restart.
            conn.execute("SELECT pg_advisory_xact_lock(74308022)")
            times = conn.execute("""INSERT INTO market_desk_publication AS s(checked_at,market_at,filings_at)
                VALUES(%s,%s,%s) ON CONFLICT(singleton) DO UPDATE SET checked_at=EXCLUDED.checked_at,
                market_at=GREATEST(s.market_at,EXCLUDED.market_at), filings_at=GREATEST(s.filings_at,EXCLUDED.filings_at)
                WHERE s.checked_at<=EXCLUDED.checked_at RETURNING market_at,filings_at""",
                                 (at, (market or {}).get("observed_at"), filings_at)).fetchone()
            if times is None:
                return
            docs, reports = self.news(conn, at)
            local = at.astimezone(KST)
            observation = (market or {}).get("session") if (market or {}).get("trade_date") == local.date().isoformat() else None
            self.register_lead(conn, at, observation)
            if is_session:
                # With aligned quotes, prefer the day's interpretation and reserve one of the same two calls for the close.
                limit = DAILY_CALLS if not observation else (1 if local.hour >= 13 else 0)
                for packet in editor.candidates(market, filings + docs, at):
                    followups = packet.pop("followups", [])
                    self.register(conn, packet, at, limit)
                    if followups:
                        conn.execute("""UPDATE market_desk_revisions SET followups=%s
                            WHERE day=%s AND %s=ANY(aliases) AND followups IS DISTINCT FROM %s""",
                                     (Jsonb(followups), at.astimezone(KST).date(), packet["key"], Jsonb(followups)))
            rows = conn.execute("""SELECT DISTINCT ON (issue_id) article,aliases,followups FROM market_desk_revisions
                WHERE created_at>=%s AND packet->>'kind'<>'session' ORDER BY issue_id,(article->>'status'='analysis') DESC,created_at DESC,
                (packet->>'kind'='move') DESC, revision DESC""",
                                (at - timedelta(days=2),)).fetchall()
            for r in rows:
                article = {**r["article"], "followups": r["followups"]}
                for alias in r["aliases"]:
                    if alias != article["id"] and article["status"] == "analysis":
                        reports.pop(alias, None)
                if article["id"] not in reports or article["status"] == "analysis":
                    reports[article["id"]] = article
            issues = sorted(reports.values(), key=lambda x: datetime.fromisoformat(x["published_at"]), reverse=True)
            # Keep room for the day desk even on a busy Reporter morning.
            desk = [i for i in issues if i.get("origin") != "reporter"][:7]
            news = [i for i in issues if i.get("origin") == "reporter"][:15-len(desk)]
            reading = conn.execute("""SELECT article FROM market_desk_revisions WHERE day=%s AND state='written'
                AND packet->>'kind'='session' AND article ? 'headline' ORDER BY created_at DESC LIMIT 1""",
                                   (local.date(),)).fetchone()
            board = {"version": 1, "checked_at": at.isoformat(), "monitoring": is_session,
                     **{k: v.isoformat() if v else None for k, v in times.items()},
                     "issues": desk + news, "briefings": self.briefs(conn, at),
                     "market_reports": self.session_reports(conn, times["market_at"]),
                     "lead": reading["article"] if reading else None}
            write(board)

    def claim(self, at, only_close=False):
        with self.db.transaction() as conn:
            return conn.execute("""UPDATE market_desk_revisions SET state='running',next_at=%s
                WHERE revision=(SELECT revision FROM market_desk_revisions
                WHERE state IN ('queued','running') AND next_at<=%s AND created_at>=%s AND day=%s
                AND (NOT %s OR packet->>'phase'='close')
                ORDER BY (packet->>'kind'='session') DESC,created_at LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *""",
                                (at + timedelta(minutes=8), at, at - timedelta(minutes=50),
                                 at.astimezone(KST).date(), only_close)).fetchone()

    def finish(self, row, state, at, article=None):
        with self.db.transaction() as conn:
            conn.execute("""UPDATE market_desk_revisions SET state=%s,next_at=%s,article=COALESCE(%s,article)
                WHERE revision=%s AND state='running' AND next_at=%s""",
                         (state, at, Jsonb(article) if article else None, row["revision"], row["next_at"]))


def slack_time(ts):
    return datetime.fromtimestamp(float(ts), KST).isoformat()
