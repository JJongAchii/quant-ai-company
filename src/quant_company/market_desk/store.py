from datetime import datetime, timedelta
from hashlib import sha256

from psycopg.types.json import Jsonb

from ..briefing import schedule
from ..briefing.schedule import KST
from . import editor, lead, note

DAILY_CALLS = 2
# The news page of Insight Invest reads a week. The home list below it keeps its two days and fifteen places.
NEWS_DAYS = 7
NEWS_LIMIT = 80


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

    def publications(self, conn, at, days, limit):
        """Reporter publications that reached the owner: delivered to the news channel (alone or in a morning
        digest), or published to this board without a Slack post."""
        return conn.execute("""SELECT n.event_id,n.article_ids,n.item,e.headline,m.text,m.created_at,
                COALESCE(o.sent_ts,delivery.sent_ts) AS sent_ts FROM news_publications n
            JOIN news_events e ON e.id=n.event_id JOIN messages m ON n.id=m.id
            JOIN projects p ON p.id=e.project_id LEFT JOIN outbox o ON o.id=m.id
            LEFT JOIN outbox delivery ON delivery.id=n.digest_id
            WHERE p.channel=%s AND p.owner_user=%s AND (n.venue='insight'
                OR ((o.status='delivered' OR delivery.status='delivered')
                    AND COALESCE(o.sent_ts,delivery.sent_ts) IS NOT NULL))
              AND m.created_at BETWEEN %s AND %s ORDER BY m.created_at DESC LIMIT %s""",
                            (self.settings.news_channel_id, self.settings.news_owner_user,
                             at - timedelta(days=days), at, limit)).fetchall()

    @staticmethod
    def report(row, docs):
        """One Reporter event as its latest publication, in the form the home has always read."""
        published = slack_time(row["sent_ts"]) if row["sent_ts"] else row["created_at"].astimezone(KST).isoformat()
        return {"id": "source-" + str(row["event_id"]), "kind": "news", "origin": "reporter", "status": "reported",
                "headline": row["headline"], "fact": row["text"], "interpretation": "", "watch": "", "observation": "",
                "published_at": published, "observed_at": None,
                "sources": [editor.public_source(d) for d in docs if d["id"] in row["article_ids"]][:8]}

    def news(self, conn, at):
        hot = self.publications(conn, at, 2, 12)
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
            reports.setdefault("source-" + str(r["event_id"]), self.report(r, docs))
        return docs, reports

    def week(self, conn, at, docs):
        """A week of Reporter events as articles: the fields recorded at publication instead of the chat text.
        An edition published before fields were recorded keeps its text."""
        reports = {}
        for r in self.publications(conn, at, NEWS_DAYS, 4 * NEWS_LIMIT):
            report = self.report(r, docs)
            if report["id"] in reports:
                continue
            if r["item"]:
                item = r["item"]
                report.update(fact=item["facts"], sources=item["sources"], category=item["category"],
                              priority=item["priority"], update=item["update"], attribution=item["attribution"],
                              significance=item["significance"], change=item["change"])
            reports[report["id"]] = report
        return reports

    def merged(self, conn, at, reports, days, moves=True):
        """Reporter events with the desk's own articles, newest first. A desk analysis replaces the report it
        explains. An index or sector move that cites a report takes its place only where moves are shown."""
        rows = conn.execute("""SELECT DISTINCT ON (issue_id) article,aliases,followups FROM market_desk_revisions
            WHERE created_at>=%s AND packet->>'kind' NOT IN ('session','note') AND (%s OR packet->>'kind'<>'move')
            ORDER BY issue_id,(article->>'status'='analysis') DESC,created_at DESC,
            (packet->>'kind'='move') DESC, revision DESC""",
                            (at - timedelta(days=days), moves)).fetchall()
        for r in rows:
            article = {**r["article"], "followups": r["followups"]}
            for alias in r["aliases"]:
                if alias != article["id"] and article["status"] == "analysis":
                    reports.pop(alias, None)
            if article["id"] not in reports or article["status"] == "analysis":
                reports[article["id"]] = article
        return sorted(reports.values(), key=lambda x: datetime.fromisoformat(x["published_at"]), reverse=True)

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
            FROM market_desk_revisions WHERE day=%s AND request IS NOT NULL AND packet->>'kind'<>'note'""", (day,)).fetchone()
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
            FROM market_desk_revisions WHERE day=%s AND request IS NOT NULL AND packet->>'kind'<>'note'""", (key, day)).fetchone()
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
            if self.settings.insight_notes_enabled:
                self.register_note(conn, at, market, filings)
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
            issues = self.merged(conn, at, reports, 2)
            # Keep room for the day desk even on a busy Reporter morning.
            desk = [i for i in issues if i.get("origin") != "reporter"][:7]
            news = [i for i in issues if i.get("origin") == "reporter"][:15-len(desk)]
            # Index and sector moves belong to the market explanation, not to the news page.
            week = self.merged(conn, at, self.week(conn, at, docs), NEWS_DAYS, moves=False)
            if self.settings.insight_notes_enabled:
                listed = {source["url"] for item in week for source in item.get("sources") or []}
                week = sorted(week + self.quoted(conn, at, listed), reverse=True,
                              key=lambda x: datetime.fromisoformat(x["published_at"]))
            reading = conn.execute("""SELECT article FROM market_desk_revisions WHERE day=%s AND state='written'
                AND packet->>'kind'='session' AND article ? 'headline' ORDER BY created_at DESC LIMIT 1""",
                                   (local.date(),)).fetchone()
            board = {"version": 1, "checked_at": at.isoformat(), "monitoring": is_session,
                     **{k: v.isoformat() if v else None for k, v in times.items()},
                     "issues": desk + news, "news": week[:NEWS_LIMIT], "briefings": self.briefs(conn, at),
                     "market_reports": self.session_reports(conn, times["market_at"]),
                     "lead": reading["article"] if reading else None}
            if self.settings.insight_notes_enabled:
                board["notes"] = [r["article"] for r in conn.execute("""SELECT article FROM market_desk_revisions
                    WHERE day=%s AND state='written' AND packet->>'kind'='note' AND article ? 'headline'
                    ORDER BY created_at""", (local.date(),)).fetchall()]
                bounds = note.session_bounds(local.date(), schedule.overrides(self.settings))
                board["alerts"] = note.market_actions(self.action_headlines(conn, at), bounds) if bounds else []
            write(board)

    def quoted(self, conn, at, listed):
        """Market and stock reports the week's notes quoted, as desk news under 시장: the original's title and link,
        and when a note relied on it. A report the news already lists is not repeated; filings keep their heading."""
        rows = conn.execute("""SELECT article FROM market_desk_revisions WHERE created_at>=%s AND state='written'
            AND packet->>'kind'='note' AND article ? 'headline' ORDER BY created_at""",
                            (at - timedelta(days=NEWS_DAYS),)).fetchall()
        found = {}
        for r in rows:
            noted = datetime.fromisoformat(r["article"]["published_at"]).astimezone(KST)
            for source in r["article"].get("sources") or []:
                if source.get("role") not in ("시장 원문", "종목 원문") or source["url"] in listed:
                    continue
                item = found.setdefault(source["url"], {
                    "id": "market-" + sha256(source["url"].encode()).hexdigest()[:16], "kind": "news", "origin": "desk",
                    "status": "reported", "headline": source["title"], "category": "시장", "priority": "major",
                    "significance": "", "interpretation": "", "watch": "", "observation": "",
                    "published_at": source["published_at"], "observed_at": None,
                    "sources": [{k: v for k, v in source.items() if k != "role"}], "noted": []})
                day = datetime.fromisoformat(source["published_at"]).astimezone(KST).date()
                stamp = noted.strftime("%H:%M") if noted.date() == day else f"{noted.month}월 {noted.day}일 {noted:%H:%M}"
                if stamp not in item["noted"]:
                    item["noted"].append(stamp)
        for item in found.values():
            item["fact"] = f"{'·'.join(item.pop('noted'))} 장중 노트가 근거로 인용한 원문입니다."
        return list(found.values())

    def action_headlines(self, conn, at):
        """Today's reports of a sidecar or circuit breaker from any enabled source, as published."""
        start = at.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
        return conn.execute("""SELECT a.title,a.url,a.published_at,s.config->>'publisher' AS publisher,
                s.config->>'license_url' AS license_url,s.config->>'license_name' AS license_name
            FROM news_articles a JOIN news_sources s ON s.id=a.source_id
            WHERE s.enabled AND a.source_digest=s.config_digest AND a.published_at BETWEEN %s AND %s
              AND a.collected_at<=%s AND (a.title LIKE '%%사이드카%%' OR a.title LIKE '%%서킷%%')
            ORDER BY a.published_at""", (start, at, at)).fetchall()

    def note_docs(self, conn, at, open_at, market_map, filings):
        """Originals a note may quote: market reports since the previous evening, reports naming a stock on the map,
        and that stock's filings. Each excerpt is the text a quote is checked against."""
        names = sorted(note.stock_index(market_map), key=len, reverse=True)
        rows = conn.execute("""WITH recent AS MATERIALIZED (
                SELECT id,source_id,source_digest,title,url,published_at FROM news_articles
                WHERE content IS NOT NULL AND published_at BETWEEN %s AND %s AND collected_at<=%s
                ORDER BY published_at DESC LIMIT 400)
            SELECT r.id,r.title,r.url,r.published_at,left(a.content,2000) AS content,
                s.config->>'publisher' AS publisher,s.config->>'license_url' AS license_url,
                s.config->>'license_name' AS license_name
            FROM recent r JOIN news_articles a ON a.id=r.id JOIN news_sources s ON s.id=r.source_id
            WHERE s.enabled AND s.config->>'use_for_summary'='true' AND s.config_digest=r.source_digest
            ORDER BY r.published_at DESC""", (open_at - timedelta(hours=16), at, at)).fetchall()
        docs = []
        for row in rows:
            role = ("시장 원문" if note.MARKET_WORDS.search(row["title"])
                    else "종목 원문" if any(name in row["title"] for name in names) else None)
            if role and len(docs) < 8:
                docs.append({**row, "id": str(row["id"]), "published_at": row["published_at"].isoformat(), "role": role})
        for filing in filings:
            if filing.get("company") in names and len(docs) < 10:
                docs.append({**filing, "id": str(filing["id"]), "role": "공시", "content": filing["content"][:2000]})
        return docs

    def register_note(self, conn, at, market, filings):
        """Freeze the scheduled or urgent note due now. A note needs today's map no older than 25 minutes, and the
        closing note a map collected after the close reached it; a slot without one is recorded as skipped and never
        written late."""
        map_ = (market or {}).get("map")
        day = at.astimezone(KST).date()
        bounds = note.session_bounds(day, schedule.overrides(self.settings))
        if not map_ or not bounds or map_["trade_date"] != day.isoformat():
            return
        age = at - datetime.fromisoformat(map_["observed_at"])
        rows = conn.execute("""SELECT packet->>'slot' AS slot,packet->>'note_kind' AS kind,packet->'trigger' AS trigger,
                packet->'indices' AS indices,created_at,state,article FROM market_desk_revisions
            WHERE day=%s AND packet->>'kind'='note' ORDER BY created_at""", (day,)).fetchall()
        written = [{"slot": r["slot"], "kind": r["kind"], "trigger": r["trigger"], "created_at": r["created_at"],
                    "state": r["state"]} for r in rows]
        last = next((r for r in reversed(rows) if r["state"] == "written" and r["article"].get("headline")), None)
        due = note.due_slot(at, bounds, written)
        trigger = None
        if due:
            slot, action = due
            kind = "scheduled"
            settled = datetime.fromisoformat(map_["observed_at"]) >= bounds[1] + note.QUOTE_DELAY
            if (not settled) if slot == "close" else age > timedelta(minutes=25):
                action = "skip"
        else:
            if age > timedelta(minutes=25):
                return
            trigger = note.urgent_trigger(at, bounds, market, self.action_headlines(conn, at), written,
                                          last["indices"] if last else None)
            if not trigger:
                return
            slot, action, kind = None, "write", "urgent"
        previous = {"headline": last["article"]["headline"], "indices": last["indices"]} if last else None
        docs = self.note_docs(conn, at, bounds[0], map_, filings) if action == "write" else []
        packet = note.packet_for(kind=kind, slot=slot, trigger=trigger, market_map=map_, docs=docs,
                                 previous=previous, at=at)
        brief = next((b for b in self.briefs(conn, at) if b["kind"] == "am"
                      and datetime.fromisoformat(b["published_at"]).date() == day), None)
        if brief and action == "write":
            # Background only: the morning brief has no link to cite, so its figures cannot pass the check.
            packet["context"] = {"아침 브리핑": brief["body"][:3000]}
        revision = editor.digest(packet)
        req = note.request_for(revision, packet) if action == "write" else None
        conn.execute("""INSERT INTO market_desk_revisions
            (revision,issue_id,aliases,day,created_at,packet,article,request,state,next_at)
            VALUES(%s,%s,%s,%s,%s,%s,'{}',%s,%s,%s) ON CONFLICT (revision) DO NOTHING""",
                     (revision, packet["key"], [packet["key"]], day, at, Jsonb(packet),
                      Jsonb(req.model_dump(mode="json")) if req else None, "queued" if req else "skipped", at))

    def claim(self, at, only_close=False):
        with self.db.transaction() as conn:
            return conn.execute("""UPDATE market_desk_revisions SET state='running',next_at=%s
                WHERE revision=(SELECT revision FROM market_desk_revisions
                WHERE state IN ('queued','running') AND next_at<=%s AND created_at>=%s AND day=%s
                AND (NOT %s OR packet->>'phase'='close')
                ORDER BY (packet->>'kind' IN ('note','session')) DESC,created_at LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *""",
                                (at + timedelta(minutes=8), at, at - timedelta(minutes=50),
                                 at.astimezone(KST).date(), only_close)).fetchone()

    def finish(self, row, state, at, article=None):
        with self.db.transaction() as conn:
            conn.execute("""UPDATE market_desk_revisions SET state=%s,next_at=%s,article=COALESCE(%s,article)
                WHERE revision=%s AND state='running' AND next_at=%s""",
                         (state, at, Jsonb(article) if article else None, row["revision"], row["next_at"]))


def slack_time(ts):
    return datetime.fromtimestamp(float(ts), KST).isoformat()
