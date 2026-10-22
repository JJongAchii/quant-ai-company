"""PostgreSQL records of the performance analytics: public uploads linked to their jobs, reach reports, fixed-window
snapshots, analysis receipts, owner-decided lessons and the posts in the analytics channel."""

import hashlib
from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb

from ..briefing.contracts import BRIEFER
from ..company import PolicyError, as_json, stable
from .analytics_metrics import (
    HORIZONS,
    MIN_BASELINE,
    PT,
    baseline,
    in_series,
    parse_reach_csv,
    parse_title,
    shorts_key,
    window,
)
from .contracts import digest

BASIC = "channel_reach_basic_a1"
# Totals per video, and the split by traffic source that the diagnosis reads.
REPORT_TYPES = (BASIC, "channel_reach_combined_a1")
# Reach data lags about two days; this many days after a window ends its snapshot is taken without it.
REACH_GRACE_DAYS = 4
BASELINE_DAYS = 28
# Enabling analytics measures the recent past for baselines, not the whole channel history.
MEASURE_DAYS = 60
# Video production owns the single claude-runtime lane: no analysis is requested while an episode is in the works.
PRODUCING = ("queued", "reviewing", "synthesizing", "rendering", "inspecting", "delivering", "uploading", "approved")
# Longer than a full production with remote render retries; state changes refresh updated_at.
PRODUCING_HOURS = 6
LOCK = 71350302


def utcnow():
    return datetime.now(UTC)


class AnalyticsStore:
    def __init__(self, company):
        self.company, self.db, self.settings = company, company.db, company.settings

    def enabled(self):
        return bool(self.settings.video_analytics_enabled)

    # ---- Uploads ---------------------------------------------------------------------------------------------

    def upsert_publications(self, items):
        """Public uploads of the channel. A new or still unlinked upload is linked to the newest produced job of the
        edition its title suffix names, else to a job with exactly the same title, else (no suffix) to the newest
        Shorts cut with its title made before it and not linked to another upload; a manual link is kept."""
        with self.db.transaction() as conn:
            for item in items:
                parsed = parse_title(item["title"], item["published_at"])
                conn.execute("""INSERT INTO video_publications(youtube_id,title,published_at,duration_s,thumbnail_url,
                    edition_kind) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(youtube_id) DO UPDATE SET title=EXCLUDED.title,
                    published_at=EXCLUDED.published_at,duration_s=EXCLUDED.duration_s,
                    thumbnail_url=EXCLUDED.thumbnail_url,edition_kind=CASE WHEN video_publications.job_id IS NULL
                    THEN EXCLUDED.edition_kind ELSE video_publications.edition_kind END,updated_at=now()""",
                             (item["youtube_id"], item["title"], item["published_at"], item["duration_s"],
                              item.get("thumbnail_url"), parsed[1] if parsed else None))
                row = conn.execute("SELECT * FROM video_publications WHERE youtube_id=%s", (item["youtube_id"],)).fetchone()
                if row["job_id"] is None:
                    self._match(conn, row, parsed)
        return len(items)

    @staticmethod
    def _match(conn, row, parsed):
        job, how = None, None
        if parsed:
            job = conn.execute("""SELECT j.* FROM video_jobs j JOIN brief_editions e ON e.id=j.edition_id
                WHERE e.day=%s AND e.kind=%s AND j.artifacts IS NOT NULL ORDER BY j.version DESC LIMIT 1""",
                               (parsed[0], "pm" if parsed[1] == "close" else "am")).fetchone()
            how = "title_suffix"
        if not job:
            job = conn.execute("""SELECT * FROM video_jobs WHERE artifacts->>'title'=%s
                ORDER BY created_at DESC LIMIT 1""", (row["title"],)).fetchone()
            how = "exact_title"
        if not job and not parsed:
            # A Shorts title carries no series suffix (ShortsPlan): linked to its cut, measured as its own kind. The
            # morning and close cuts of a day may share a title, and a backfill sees cuts made after an old upload.
            key = shorts_key(row["title"])
            cuts = conn.execute("""SELECT s.job_id,s.artifacts->>'title' AS title,j.edition_id FROM video_shorts s
                JOIN video_jobs j ON j.id=s.job_id WHERE s.artifacts ? 'title' AND s.created_at<=%s
                AND NOT EXISTS (SELECT 1 FROM video_publications q WHERE q.job_id=s.job_id AND q.edition_kind='shorts'
                AND q.youtube_id<>%s) ORDER BY s.created_at DESC""", (row["published_at"], row["youtube_id"])).fetchall()
            cut = next((c for c in cuts if key and shorts_key(c["title"]) == key), None)
            if cut:
                conn.execute("""UPDATE video_publications SET job_id=%s,edition_id=%s,edition_kind='shorts',
                    proposed_title=%s,matched_by='shorts_title',updated_at=now() WHERE youtube_id=%s""",
                             (cut["job_id"], cut["edition_id"], cut["title"], row["youtube_id"]))
            return
        if job:
            conn.execute("""UPDATE video_publications SET job_id=%s,edition_id=%s,edition_kind=%s,proposed_title=%s,
                matched_by=%s,updated_at=now() WHERE youtube_id=%s""",
                         (job["id"], job["edition_id"], job["source"].get("edition_kind"),
                          job["artifacts"].get("title"), how, row["youtube_id"]))

    def link(self, youtube_id, job_id):
        """Operator link for an upload the title rules could not place (video-analytics link)."""
        with self.db.transaction() as conn:
            job = conn.execute("SELECT * FROM video_jobs WHERE id=%s", (job_id,)).fetchone()
            row = conn.execute("SELECT 1 FROM video_publications WHERE youtube_id=%s", (youtube_id,)).fetchone()
            if not job or not row or not job["artifacts"]:
                raise PolicyError("Unknown upload or unproduced video job")
            conn.execute("""UPDATE video_publications SET job_id=%s,edition_id=%s,edition_kind=%s,proposed_title=%s,
                matched_by='manual',updated_at=now() WHERE youtube_id=%s""",
                         (job["id"], job["edition_id"], job["source"].get("edition_kind"),
                          job["artifacts"].get("title"), youtube_id))

    def publications(self):
        with self.db.transaction() as conn:
            return conn.execute("SELECT * FROM video_publications ORDER BY published_at").fetchall()

    # ---- Reach reports ---------------------------------------------------------------------------------------

    def report_job(self, report_type):
        with self.db.transaction() as conn:
            row = conn.execute("SELECT job_id FROM video_report_jobs WHERE report_type=%s", (report_type,)).fetchone()
        return row["job_id"] if row else None

    def save_report_job(self, report_type, job_id, receipt):
        with self.db.transaction() as conn:
            conn.execute("""INSERT INTO video_report_jobs(report_type,job_id,receipt) VALUES(%s,%s,%s)
                ON CONFLICT(report_type) DO NOTHING""", (report_type, job_id, Jsonb(as_json(receipt))))

    def known_reports(self):
        with self.db.transaction() as conn:
            return {r["report_id"] for r in conn.execute("SELECT report_id FROM video_report_downloads").fetchall()}

    def record_report(self, report_type, report_id, day, created, text):
        """Applies one daily reach report. A newer report for the same type and day replaces that day's rows; an
        older or already recorded one changes nothing. Returns whether the rows were applied."""
        rows = parse_reach_csv(text, report_type)
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            if conn.execute("SELECT 1 FROM video_report_downloads WHERE report_id=%s", (report_id,)).fetchone():
                return False
            latest = conn.execute("""SELECT max(report_created) AS at FROM video_report_downloads
                WHERE report_type=%s AND day=%s AND applied""", (report_type, day)).fetchone()["at"]
            apply = latest is None or created > latest
            conn.execute("""INSERT INTO video_report_downloads(report_id,report_type,day,report_created,sha256,row_count,
                applied) VALUES(%s,%s,%s,%s,%s,%s,%s)""", (report_id, report_type, day, created,
                hashlib.sha256(text.encode()).hexdigest(), len(rows), apply))
            if apply:
                basic = report_type == BASIC
                conn.execute("DELETE FROM video_reach_daily WHERE day=%s AND (source='ALL')=%s", (day, basic))
                for row in rows:
                    conn.execute("""INSERT INTO video_reach_daily(youtube_id,day,source,impressions,clicks,report_id)
                        VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(youtube_id,day,source) DO UPDATE SET
                        impressions=EXCLUDED.impressions,clicks=EXCLUDED.clicks,report_id=EXCLUDED.report_id""",
                                 (row["video_id"], row["day"], row["source"], row["impressions"], row["clicks"],
                                  report_id))
        return apply

    def reach(self, youtube_id, start, end):
        """Impressions, clicks and impression-weighted CTR over the days, plus the split by traffic source."""
        with self.db.transaction() as conn:
            total = conn.execute("""SELECT COALESCE(sum(impressions),0) AS impressions,COALESCE(sum(clicks),0) AS clicks,
                count(DISTINCT day) AS days FROM video_reach_daily WHERE youtube_id=%s AND source='ALL'
                AND day BETWEEN %s AND %s""", (youtube_id, start, end)).fetchone()
            sources = conn.execute("""SELECT source,sum(impressions) AS impressions,sum(clicks) AS clicks
                FROM video_reach_daily WHERE youtube_id=%s AND source<>'ALL' AND day BETWEEN %s AND %s
                GROUP BY source ORDER BY sum(impressions) DESC,source""", (youtube_id, start, end)).fetchall()
        impressions, clicks = int(total["impressions"]), float(total["clicks"])
        return {"impressions": impressions, "clicks": clicks, "ctr": clicks / impressions if impressions else None,
                "days": total["days"],
                "sources": [{"source": s["source"], "impressions": int(s["impressions"]),
                             "ctr": float(s["clicks"]) / int(s["impressions"]) if s["impressions"] else None}
                            for s in sources]}

    # ---- Snapshots -------------------------------------------------------------------------------------------

    def due_snapshots(self, at=None):
        """(publication, horizon) whose fixed window is over and has both reach reports for every day, or whose
        window ended more than REACH_GRACE_DAYS ago without them. A new reporting job backfills the past newest day
        first, and the two report types arrive apart, so the latest reported day says nothing about the others.
        Uploads of other series are skipped unless linked."""
        at = at or utcnow()
        today = at.astimezone(PT).date()
        with self.db.transaction() as conn:
            reported = {r["day"] for r in conn.execute("""SELECT day FROM video_report_downloads
                WHERE report_type=ANY(%s) AND applied GROUP BY day HAVING count(DISTINCT report_type)=%s""",
                                                       (list(REPORT_TYPES), len(REPORT_TYPES))).fetchall()}
            taken = {(r["youtube_id"], r["horizon"]) for r in
                     conn.execute("SELECT youtube_id,horizon FROM video_snapshots").fetchall()}
            rows = conn.execute("SELECT * FROM video_publications WHERE published_at>=%s ORDER BY published_at",
                                (at - timedelta(days=MEASURE_DAYS),)).fetchall()
        due = []
        for row in rows:
            if row["job_id"] is None and not in_series(row["title"]):
                continue
            for horizon in HORIZONS:
                start, end = window(row["published_at"], horizon)
                if (row["youtube_id"], horizon) in taken or end >= today:
                    continue
                days = {start + timedelta(days=n) for n in range((end - start).days + 1)}
                if days <= reported or today > end + timedelta(days=REACH_GRACE_DAYS):
                    due.append((row, horizon))
        return due

    def save_snapshot(self, youtube_id, horizon, start, end, metrics, taken_at=None):
        with self.db.transaction() as conn:
            conn.execute("""INSERT INTO video_snapshots(youtube_id,horizon,window_start,window_end,metrics,taken_at)
                VALUES(%s,%s,%s,%s,%s,COALESCE(%s,now())) ON CONFLICT DO NOTHING""",
                         (youtube_id, horizon, start, end, Jsonb(as_json(metrics)), taken_at))

    def snapshot(self, youtube_id, horizon):
        with self.db.transaction() as conn:
            return conn.execute("SELECT * FROM video_snapshots WHERE youtube_id=%s AND horizon=%s",
                                (youtube_id, horizon)).fetchone()

    def snapshots(self, horizon, since):
        with self.db.transaction() as conn:
            return conn.execute("""SELECT s.*,p.title,p.proposed_title,p.edition_kind,p.published_at,p.job_id,
                p.duration_s,p.thumbnail_url FROM video_snapshots s JOIN video_publications p USING(youtube_id)
                WHERE s.horizon=%s AND p.published_at>=%s ORDER BY p.published_at""", (horizon, since)).fetchall()

    def unposted(self, horizon):
        """Reportable snapshots of `horizon` whose Slack post does not exist yet, with their publication."""
        # messages.id reads back as a UUID and stable() gives its text: compare text.
        with self.db.transaction() as conn:
            rows = conn.execute("""SELECT s.youtube_id,s.horizon,s.window_start,s.window_end,s.metrics,p.title,
                p.proposed_title,p.edition_kind,p.published_at,p.job_id,p.duration_s,p.thumbnail_url
                FROM video_snapshots s JOIN video_publications p USING(youtube_id)
                WHERE s.horizon=%s AND (s.metrics->>'report')::boolean ORDER BY p.published_at""", (horizon,)).fetchall()
            posted = {str(r["id"]) for r in conn.execute("SELECT id FROM messages WHERE id=ANY(%s)", (
                [stable(f"video-analytics-message:{r['youtube_id']}:{horizon}") for r in rows],)).fetchall()}
        return [r for r in rows if stable(f"video-analytics-message:{r['youtube_id']}:{horizon}") not in posted]

    def undetailed(self):
        """Cards with a diagnosis whose thread reply (the full diagnosis) is not posted yet, with what it shows.
        Cards from before the Studio edits carried the full diagnosis themselves and get no reply."""
        with self.db.transaction() as conn:
            rows = conn.execute("""SELECT a.youtube_id,a.output,a.request->'extra'->'baselines' AS baselines,
                s.window_start,s.window_end,s.metrics,p.title,p.edition_kind,p.published_at
                FROM video_analyses a JOIN video_snapshots s ON s.youtube_id=a.youtube_id AND s.horizon='d3'
                JOIN video_publications p ON p.youtube_id=a.youtube_id
                WHERE a.kind='video' AND a.state='completed' AND a.message_id IS NOT NULL AND a.output ? 'changes'
                ORDER BY a.created_at""").fetchall()
            posted = {str(r["id"]) for r in conn.execute("SELECT id FROM messages WHERE id=ANY(%s)", (
                [stable(f"video-analytics-message:{r['youtube_id']}:d3:detail") for r in rows],)).fetchall()}
        return [r for r in rows if stable(f"video-analytics-message:{r['youtube_id']}:d3:detail") not in posted]

    def episodes(self, since=None):
        """Publications since `since` (all when None) with their latest snapshot (d28, else d7, else d3), the d3 one
        and the craft version and lessons frozen in their job's policy. A Shorts row carries the cut's own plan and
        manifest and only the shorts lessons; an episode row only the others (runner.write_plan, shorts_step)."""
        with self.db.transaction() as conn:
            rows = conn.execute("""SELECT p.*,j.policy->'lessons' AS lessons,j.policy->>'craft' AS craft,
                CASE WHEN p.edition_kind='shorts' THEN sh.plan ELSE j.plan END AS plan,
                CASE WHEN p.edition_kind='shorts' THEN sh.artifacts ELSE j.artifacts END AS artifacts
                FROM video_publications p LEFT JOIN video_jobs j ON j.id=p.job_id
                LEFT JOIN video_shorts sh ON sh.job_id=p.job_id
                WHERE %s::timestamptz IS NULL OR p.published_at>=%s ORDER BY p.published_at""",
                                (since, since)).fetchall()
            snaps = conn.execute("""SELECT youtube_id,horizon,window_end,metrics,taken_at FROM video_snapshots
                WHERE youtube_id=ANY(%s)""", ([r["youtube_id"] for r in rows],)).fetchall()
        best, d3 = {}, {}
        for snap in snaps:
            rank = ("d3", "d7", "d28").index(snap["horizon"])
            if snap["youtube_id"] not in best or rank > best[snap["youtube_id"]][0]:
                best[snap["youtube_id"]] = (rank, snap)
            if snap["horizon"] == "d3":
                d3[snap["youtube_id"]] = snap
        for row in rows:
            snap, first = best.get(row["youtube_id"], (None, None))[1], d3.get(row["youtube_id"])
            row["horizon"], row["metrics"] = (snap["horizon"], snap["metrics"]) if snap else (None, None)
            row["window_end"] = snap["window_end"] if snap else None
            row["d3"], row["d3_taken"] = (first["metrics"], first["taken_at"]) if first else (None, None)
            row["craft"] = int(row["craft"]) if row["craft"] is not None else None
            shorts = row["edition_kind"] == "shorts"
            row["lessons"] = [x["id"] for x in row["lessons"] or [] if (x["scope"] == "shorts") == shorts]
        return rows

    def job(self, job_id):
        with self.db.transaction() as conn:
            return conn.execute("SELECT * FROM video_jobs WHERE id=%s", (job_id,)).fetchone() if job_id else None

    def shorts(self, job_id):
        with self.db.transaction() as conn:
            return conn.execute("SELECT plan,artifacts FROM video_shorts WHERE job_id=%s", (job_id,)).fetchone()

    def baselines(self, publication, horizon, keys, minimum=MIN_BASELINE):
        """Median of each metric over episodes of the same edition kind published in the 28 days before this one;
        None for a metric with fewer than `minimum` such episodes."""
        with self.db.transaction() as conn:
            rows = conn.execute("""SELECT s.metrics FROM video_snapshots s JOIN video_publications p USING(youtube_id)
                WHERE s.horizon=%s AND p.youtube_id<>%s AND p.edition_kind IS NOT DISTINCT FROM %s
                AND p.published_at<%s AND p.published_at>=%s""",
                                (horizon, publication["youtube_id"], publication["edition_kind"],
                                 publication["published_at"],
                                 publication["published_at"] - timedelta(days=BASELINE_DAYS))).fetchall()
        return {**{key: baseline([r["metrics"].get(key) for r in rows], minimum) for key in keys}, "n": len(rows)}

    def producing(self):
        """An episode is being made now. A job left in a production state for hours (production switched off, an
        operator hold) does not keep the analysis waiting."""
        if not self.settings.video_enabled:
            return False
        with self.db.transaction() as conn:
            return bool(conn.execute(f"""SELECT 1 FROM video_jobs WHERE state=ANY(%s)
                AND updated_at>now()-interval '{PRODUCING_HOURS} hours' LIMIT 1""", (list(PRODUCING),)).fetchone())

    # ---- Analysis receipts (same rules as video_effects model receipts) --------------------------------------

    def begin_analysis(self, request_id, kind, request, youtube_id=None, week=None):
        """None: ask claude-runtime (new, or asked again by the same ID while it runs). Otherwise the receipt of a
        completed or failed analysis. A changed request under the same ID is refused. The digest is taken of the
        stored JSON form, so a request read back from the row compares equal."""
        request = as_json(request)
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            old = conn.execute("SELECT * FROM video_analyses WHERE request_id=%s FOR UPDATE", (request_id,)).fetchone()
            if old:
                if old["request_digest"] != digest(request):
                    raise PolicyError("Analysis input changed")
                if old["state"] in {"completed", "failed"}:
                    return old["receipt"]
                if old["state"] == "running":
                    return None
            from ..owner_controls import effective_limits

            conn.execute("INSERT INTO daily_usage(day,reserved) VALUES(CURRENT_DATE,0) ON CONFLICT DO NOTHING")
            used = conn.execute("SELECT reserved FROM daily_usage WHERE day=CURRENT_DATE FOR UPDATE").fetchone()["reserved"]
            limit = effective_limits(conn, self.company)["company"]
            if limit is not None and used >= limit:
                raise PolicyError("Company model allowance exhausted")
            conn.execute("UPDATE daily_usage SET reserved=reserved+1 WHERE day=CURRENT_DATE")
            if old:
                conn.execute("""UPDATE video_analyses SET state='running',receipt=NULL,created_at=now(),completed_at=NULL
                    WHERE request_id=%s""", (request_id,))
            else:
                conn.execute("""INSERT INTO video_analyses(id,kind,request_id,youtube_id,week,request_digest,request,
                    state) VALUES(%s,%s,%s,%s,%s,%s,%s,'running')""", (stable("video-analysis:" + request_id), kind,
                    request_id, youtube_id, week, digest(request), Jsonb(request)))
        return None

    def finish_analysis(self, request_id, receipt):
        with self.db.transaction() as conn:
            conn.execute("""UPDATE video_analyses SET state='completed',receipt=%s,output=%s,completed_at=now()
                WHERE request_id=%s AND state='running'""",
                         (Jsonb(as_json(receipt)), Jsonb(as_json(receipt["output"])), request_id))

    def fail_analysis(self, request_id, receipt):
        """An executed turn without a usable output; the post goes out with the numbers alone."""
        with self.db.transaction() as conn:
            conn.execute("""UPDATE video_analyses SET state='failed',receipt=%s,completed_at=now()
                WHERE request_id=%s AND state='running'""", (Jsonb(as_json(receipt)), request_id))

    def reject_analysis(self, request_id, code):
        """busy/quota are reported before any inference: refund the allowance; the same request runs later."""
        with self.db.transaction() as conn:
            row = conn.execute("""UPDATE video_analyses SET state='rejected',receipt=%s WHERE request_id=%s
                AND state='running' RETURNING created_at""", (Jsonb({"code": code, "executed": False}),
                                                              request_id)).fetchone()
            if row:
                conn.execute("UPDATE daily_usage SET reserved=GREATEST(0,reserved-1) WHERE day=%s::date",
                             (row["created_at"],))

    def analysis(self, request_id):
        with self.db.transaction() as conn:
            return conn.execute("SELECT * FROM video_analyses WHERE request_id=%s", (request_id,)).fetchone()

    def analyses(self, kind, since):
        with self.db.transaction() as conn:
            return conn.execute("""SELECT * FROM video_analyses WHERE kind=%s AND state='completed' AND created_at>=%s
                ORDER BY created_at""", (kind, since)).fetchall()

    # ---- Lessons ---------------------------------------------------------------------------------------------

    def active_lessons(self, conn=None):
        """[{id, scope, text}] in adoption order; VideoStore.enqueue freezes this list into a new job's policy."""
        query = """SELECT id,scope,text FROM video_lessons WHERE state='active' ORDER BY decided_at,created_at,id"""
        if conn is not None:
            rows = conn.execute(query).fetchall()
        else:
            with self.db.transaction() as own:
                rows = own.execute(query).fetchall()
        return [{"id": str(r["id"]), "scope": r["scope"], "text": r["text"]} for r in rows]

    def lessons(self, states):
        with self.db.transaction() as conn:
            return conn.execute("""SELECT * FROM video_lessons WHERE state=ANY(%s)
                ORDER BY decided_at NULLS LAST,created_at,id""", (list(states),)).fetchall()

    def propose(self, conn, analysis_id, proposals):
        ids = []
        for n, item in enumerate(proposals):
            identity = stable(f"video-lesson:{analysis_id}:{n}")
            conn.execute("""INSERT INTO video_lessons(id,analysis_id,text,scope,evidence) VALUES(%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING""", (identity, analysis_id, item["text"], item["scope"],
                Jsonb({k: item[k] for k in ("evidence", "videos", "expected", "confidence")})))
            ids.append(str(identity))
        return ids

    def bound(self, lesson_id, message_id, ts, channel, action):
        """The thread ('weekly:<monday>' or a video ID) of the delivered message a button was rendered in, or None.
        adopt/reject: the lesson was proposed by that review or answer; retire: any active lesson, from a review
        (each review lists them)."""
        with self.db.transaction() as conn:
            source = conn.execute("""SELECT a.id,a.kind,a.youtube_id,a.week FROM video_analyses a
                JOIN outbox o ON o.id=a.message_id WHERE a.message_id=%s AND a.kind IN ('weekly','answer')
                AND o.status='delivered' AND o.sent_ts=%s AND o.channel=%s""", (message_id, ts, channel)).fetchone()
            lesson = conn.execute("SELECT * FROM video_lessons WHERE id=%s", (lesson_id,)).fetchone()
        if not source or not lesson or (action == "retire" and source["kind"] != "weekly") or (
                action != "retire" and lesson["analysis_id"] != source["id"]):
            return None
        return source["youtube_id"] or f"weekly:{source['week']}"

    def lesson(self, lesson_id):
        with self.db.transaction() as conn:
            return conn.execute("SELECT * FROM video_lessons WHERE id=%s", (lesson_id,)).fetchone()

    def decide(self, lesson_id, action, owner, event_key):
        """The owner's Slack decision: adopt or reject a proposal, retire an active lesson. A repeated event returns
        its first outcome; adoption stops at VIDEO_ANALYTICS_MAX_LESSONS ('full')."""
        if owner != self.settings.briefing_owner_user or action not in {"adopt", "reject", "retire"}:
            raise PolicyError("Only the brief owner decides lessons")
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            old = conn.execute("SELECT outcome FROM video_lesson_actions WHERE event_key=%s", (event_key,)).fetchone()
            if old:
                return old["outcome"]
            lesson = conn.execute("SELECT * FROM video_lessons WHERE id=%s FOR UPDATE", (lesson_id,)).fetchone()
            if not lesson:
                raise PolicyError("Unknown lesson")
            outcome = "unchanged"
            if action == "adopt" and lesson["state"] == "proposed":
                active = conn.execute("SELECT count(*) AS n FROM video_lessons WHERE state='active'").fetchone()["n"]
                outcome = "full" if active >= self.settings.video_analytics_max_lessons else "active"
            elif action == "reject" and lesson["state"] == "proposed":
                outcome = "rejected"
            elif action == "retire" and lesson["state"] == "active":
                outcome = "retired"
            if outcome in {"active", "rejected", "retired"}:
                conn.execute("UPDATE video_lessons SET state=%s,decided_by=%s,decided_at=now() WHERE id=%s",
                             (outcome, owner, lesson_id))
            conn.execute("""INSERT INTO video_lesson_actions(event_key,lesson_id,owner_user,action,outcome)
                VALUES(%s,%s,%s,%s,%s)""", (event_key, lesson_id, owner, action, outcome))
            return outcome

    def status(self):
        """Operator summary (video-analytics status); no tokens or report URLs are stored to show."""
        with self.db.transaction() as conn:
            rows = conn.execute("SELECT youtube_id,title,job_id FROM video_publications ORDER BY published_at DESC").fetchall()
            day = conn.execute("""SELECT max(day) AS day FROM video_report_downloads WHERE report_type=%s AND applied""",
                               (BASIC,)).fetchone()["day"]
            snaps = conn.execute("SELECT horizon,count(*) AS n FROM video_snapshots GROUP BY horizon").fetchall()
            analyses = conn.execute("SELECT kind,state,count(*) AS n FROM video_analyses GROUP BY kind,state").fetchall()
            lessons = conn.execute("SELECT state,count(*) AS n FROM video_lessons GROUP BY state").fetchall()
            jobs = conn.execute("SELECT report_type FROM video_report_jobs ORDER BY report_type").fetchall()
        unlinked = [r for r in rows if r["job_id"] is None]
        return {"enabled": self.enabled(),
                "publications": {"total": len(rows), "linked": len(rows) - len(unlinked),
                                 "other_series": sum(not in_series(r["title"]) for r in unlinked),
                                 "unlinked": [{"youtube_id": r["youtube_id"], "title": r["title"]}
                                              for r in unlinked if in_series(r["title"])][:10]},
                "report_jobs": [r["report_type"] for r in jobs],
                "latest_reach_day": day.isoformat() if day else None,
                "snapshots": {r["horizon"]: r["n"] for r in snaps},
                "analyses": {f"{r['kind']}:{r['state']}": r["n"] for r in analyses},
                "lessons": {r["state"]: r["n"] for r in lessons}}

    # ---- Slack posts in the analytics channel ----------------------------------------------------------------

    def _project(self, conn, key, title):
        identity = stable("video-analytics-project:" + key)
        conn.execute("""INSERT INTO projects(id,title,instruction,owner_user,channel)
            VALUES(%s,%s,'Read-only video performance report',%s,%s) ON CONFLICT DO NOTHING""",
                     (identity, title[:300], self.settings.briefing_owner_user, self.settings.video_analytics_channel_id))
        return conn.execute("SELECT * FROM projects WHERE id=%s", (identity,)).fetchone()

    def post(self, thread, key, title, text, *, reply_to=None, analysis_request=None):
        """One message per key (stable ID) in the project of `thread` (a video ID or 'weekly:<monday>'). reply_to: the
        key of the thread's root post; the reply waits until that root has a Slack receipt. Returns the message ID,
        or None while waiting."""
        identity = stable("video-analytics-message:" + key)
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM messages WHERE id=%s", (identity,)).fetchone():
                return str(identity)
            project = self._project(conn, thread, title)
            if reply_to is not None:
                root = conn.execute("SELECT status,sent_ts FROM outbox WHERE id=%s",
                                    (stable("video-analytics-message:" + reply_to),)).fetchone()
                if not root or root["status"] != "delivered" or not root["sent_ts"]:
                    return None
                project["thread_ts"] = root["sent_ts"]
            self.company._message(conn, project, None, BRIEFER, "video_analytics", text[:2900], message_id=identity)
            if analysis_request:
                conn.execute("UPDATE video_analyses SET message_id=%s WHERE request_id=%s", (identity, analysis_request))
        return str(identity)

    # ---- The owner's questions in analytics threads ----------------------------------------------------------

    def ask(self, *, event_key, channel, thread_ts, message_ts, user, text):
        """The owner's reply under a delivered card or weekly review becomes one question; a Slack retry of the same
        event is the same question. Other messages in the channel are not questions."""
        text = text.strip()[:2000]
        if (not self.enabled() or user != self.settings.briefing_owner_user or not text
                or channel != self.settings.video_analytics_channel_id):
            return {"ignored": True, "reason": "video_question_scope"}
        with self.db.transaction() as conn:
            root = conn.execute("""SELECT a.kind,a.youtube_id,a.week,o.id AS message_id FROM outbox o
                JOIN video_analyses a ON a.message_id=o.id WHERE o.channel=%s AND o.sent_ts=%s AND o.status='delivered'
                AND o.thread_ts IS NULL AND a.kind IN ('video','weekly')""", (channel, thread_ts)).fetchone()
            if not root:
                return {"ignored": True, "reason": "not_an_analytics_thread"}
            video = root["youtube_id"] if root["kind"] == "video" else None
            identity = stable("video-question:" + event_key)
            inserted = conn.execute("""INSERT INTO video_questions(id,event_key,thread_key,youtube_id,week,
                root_message_id,owner_user,channel,thread_ts,message_ts,text) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(event_key) DO NOTHING RETURNING id""",
                                    (identity, event_key, video or f"weekly:{root['week']}", video,
                                     None if video else root["week"], root["message_id"], user, channel, thread_ts,
                                     message_ts, text)).fetchone()
        return {"video_question": str(identity), "duplicate": inserted is None}

    def question(self, question_id):
        with self.db.transaction() as conn:
            return conn.execute("SELECT * FROM video_questions WHERE id=%s", (question_id,)).fetchone()

    def unstarted_questions(self):
        """Open questions whose answering workflow dispatch has not started yet, oldest first."""
        with self.db.transaction() as conn:
            return [str(r["id"]) for r in conn.execute("""SELECT id FROM video_questions WHERE state='pending'
                AND NOT workflow_started ORDER BY created_at LIMIT 10""").fetchall()]

    def question_started(self, question_id):
        with self.db.transaction() as conn:
            conn.execute("UPDATE video_questions SET workflow_started=true WHERE id=%s", (question_id,))

    def notice_waiting(self, question_id):
        """True the first time only: the 'answer after production' notice is said once per question."""
        with self.db.transaction() as conn:
            return bool(conn.execute("""UPDATE video_questions SET waiting_noticed=true WHERE id=%s
                AND NOT waiting_noticed RETURNING id""", (question_id,)).fetchone())

    def close_question(self, question_id, state):
        with self.db.transaction() as conn:
            conn.execute("""UPDATE video_questions SET state=%s,closed_at=now() WHERE id=%s AND state='pending'""",
                         (state, question_id))

    def thread_history(self, question):
        """The thread's earlier questions with their answers, oldest first (at most five)."""
        with self.db.transaction() as conn:
            rows = conn.execute("""SELECT q.text AS question,a.output->>'answer' AS answer FROM video_questions q
                LEFT JOIN video_analyses a ON a.request_id='video-analytics-answer-'||q.id::text
                WHERE q.thread_key=%s AND q.created_at<%s AND q.id<>%s ORDER BY q.created_at DESC LIMIT 5""",
                                (question["thread_key"], question["created_at"], question["id"])).fetchall()
        return rows[::-1]

    def publication(self, youtube_id):
        with self.db.transaction() as conn:
            return conn.execute("SELECT * FROM video_publications WHERE youtube_id=%s", (youtube_id,)).fetchone()

    def posted(self, key):
        """Outbox row of a post (status, sent_ts) or None."""
        with self.db.transaction() as conn:
            return conn.execute("SELECT o.* FROM outbox o WHERE o.id=%s",
                                (stable("video-analytics-message:" + key),)).fetchone()

    def gate(self, conn, row):
        project = conn.execute("SELECT * FROM projects WHERE id=%s", (row["project_id"],)).fetchone()
        permitted = (self.enabled() and row["agent"] == BRIEFER
                     and row["channel"] == self.settings.video_analytics_channel_id
                     and project["owner_user"] == self.settings.briefing_owner_user
                     and project["channel"] == row["channel"] and len(row["text"]) <= 3000)
        if not permitted:
            conn.execute("UPDATE outbox SET status='stale',error='video_analytics_disabled_or_changed' WHERE id=%s",
                         (row["id"],))
        return permitted
