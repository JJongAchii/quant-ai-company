from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from ..briefing.contracts import BriefReview
from ..company import PolicyError, as_json, stable
from .contracts import POLICY_VERSION, VideoAction, VideoReview, digest

KST = ZoneInfo("Asia/Seoul")
LOCK = 71350301
ACTIVE = ("queued", "reviewing", "synthesizing", "rendering", "uploading", "approved")


def utcnow():
    return datetime.now(UTC)


class UncertainEffect(Exception):
    pass


class VideoStore:
    def __init__(self, company):
        self.company, self.db, self.settings = company, company.db, company.settings

    def enabled(self):
        from ..briefing.store import BriefStore

        return self.settings.video_enabled and self.settings.briefing_publish_enabled and BriefStore(self.company).authorized()

    def enqueue(self, conn, edition, at):
        if not self.enabled() or not edition["publish"] or edition["kind"] != "am" or edition["state"] != "ready":
            return None
        quality = edition["quality"] or {}
        review = edition.get("review") or quality.get("editorial_review")
        if (not edition["proposal"] or not review or quality.get("reduced") is not False
                or quality.get('substantive') is not True or quality.get("rejected")
                or quality.get("unreviewed_draft_preserved")):
            return None
        try:
            checked = BriefReview.model_validate(review)
        except ValueError:
            return None
        if checked.verdict != "publish" or checked.rejected_ids or not all(checked.checks.values()):
            return None
        source = as_json({k: edition[k] for k in ("id", "day", "cutoff", "proposal", "bundle", "rendered",
                                                 "project_id", "owner_user", "channel", "policy_digest")})
        identity = stable("video:" + str(edition["id"]) + ":1")
        deadline = datetime.combine(edition["day"], time(9), KST)
        if at >= deadline:
            return None
        policy = {"version": POLICY_VERSION, "voice": self.settings.video_voice,
                  "speech_model": "eleven_multilingual_v2", "model": self.settings.video_model,
                  "workspace_id": self.settings.video_runway_workspace_id,
                  "youtube_channel": self.settings.video_youtube_channel_id}
        conn.execute("""INSERT INTO video_jobs(id,edition_id,version,source_digest,source,policy,publish_deadline)
            VALUES(%s,%s,1,%s,%s,%s,%s) ON CONFLICT(edition_id,version) DO NOTHING""",
                     (identity, edition["id"], digest(source), Jsonb(source), Jsonb(policy), deadline))
        return str(identity)

    def get(self, identity):
        with self.db.transaction() as conn:
            return conn.execute("SELECT * FROM video_jobs WHERE id=%s", (identity,)).fetchone()

    def claim(self):
        if not self.enabled():
            return None
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            conn.execute("""UPDATE video_jobs j SET state=CASE WHEN EXISTS
                (SELECT 1 FROM video_effects e WHERE e.job_id=j.id AND e.state='running')
                THEN 'uncertain' ELSE 'expired' END,lease_until=NULL,updated_at=now()
                WHERE state=ANY(%s) AND publish_deadline<=%s
                AND (lease_until IS NULL OR lease_until<now())""", (list(ACTIVE)+['awaiting_approval'], utcnow()))
            row = conn.execute("""SELECT * FROM video_jobs WHERE state=ANY(%s)
                AND (lease_until IS NULL OR lease_until<now())
                AND (state<>'uploading' OR %s) AND (state<>'approved' OR %s)
                ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED""",
                               (list(ACTIVE), self.settings.video_upload_enabled,
                                self.settings.video_publish_enabled)).fetchone()
            if row:
                conn.execute("UPDATE video_jobs SET lease_until=now()+interval '20 minutes' WHERE id=%s", (row["id"],))
            return row

    def allowed(self, job):
        configured = (self.enabled() and job['source']['owner_user'] == self.settings.briefing_owner_user
                and job['source']['channel'] == self.settings.briefing_channel_id
                and job['policy']['youtube_channel'] == self.settings.video_youtube_channel_id
                and job['policy']['workspace_id'] == self.settings.video_runway_workspace_id)
        if not configured:
            return False
        with self.db.transaction() as conn:
            project = conn.execute('SELECT status,revision FROM projects WHERE id=%s',
                                   (job['source']['project_id'],)).fetchone()
        return bool(project and project['status'] == 'active' and project['revision'] == 1)

    def gate(self, conn, row):
        job = conn.execute('SELECT * FROM video_jobs WHERE review_message_id=%s', (row['id'],)).fetchone()
        if (job and self.allowed(job) and job['state'] == 'awaiting_approval'
                and utcnow() < job['publish_deadline']):
            return True
        conn.execute("UPDATE outbox SET status='stale',error='video_review_expired_or_disabled' WHERE id=%s",
                     (row['id'],))
        return False

    def has_uncertain_effect(self, job):
        with self.db.transaction() as conn:
            return bool(conn.execute("SELECT 1 FROM video_effects WHERE job_id=%s AND state='running' LIMIT 1",
                                     (job['id'],)).fetchone())

    def budget_available(self, job, cost):
        with self.db.transaction() as conn:
            spent = conn.execute("""SELECT COALESCE(sum(credits),0) AS n FROM video_effects
                WHERE created_at >= date_trunc('month',now() AT TIME ZONE 'Asia/Seoul') AT TIME ZONE 'Asia/Seoul'
                AND state<>'rejected'""").fetchone()['n']
            episode = conn.execute("""SELECT COALESCE(sum(e.credits),0) AS n FROM video_effects e
                JOIN video_jobs j ON j.id=e.job_id WHERE j.edition_id=%s AND e.state<>'rejected'""",
                                   (job['edition_id'],)).fetchone()['n']
            return (spent+cost <= self.settings.video_monthly_credit_limit
                    and episode+cost <= self.settings.video_episode_credit_limit)

    def save(self, job, state, **values):
        allowed = {"plan", "review", "artifacts", "artifact_digest", "youtube_id", "upload_session", "error"}
        if not values.keys() <= allowed:
            raise ValueError("Unknown video update")
        assignments = ["state=%s", "lease_until=NULL", "updated_at=now()"]
        parameters = [state]
        for key, value in values.items():
            assignments.append(key + "=%s")
            parameters.append(Jsonb(value) if key in {"plan", "review", "artifacts"} else value)
        with self.db.transaction() as conn:
            result = conn.execute("UPDATE video_jobs SET " + ",".join(assignments) + " WHERE id=%s AND state=%s",
                                  (*parameters, job["id"], job["state"]))
            if not result.rowcount:
                raise PolicyError("Video changed while processing")

    def checkpoint(self, job, **values):
        if not values.keys() <= {"youtube_id", "upload_session"}:
            raise ValueError("Invalid checkpoint")
        with self.db.transaction() as conn:
            for key, value in values.items():
                conn.execute("UPDATE video_jobs SET " + key + "=%s WHERE id=%s", (value, job["id"]))

    def begin_effect(self, job, key, request, kind, cost=0):
        identity = str(job["id"]) + ":" + key
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            old = conn.execute("SELECT * FROM video_effects WHERE id=%s FOR UPDATE", (identity,)).fetchone()
            if old:
                if old["request_digest"] != digest(request):
                    raise PolicyError("Effect input changed")
                if old["state"] == "completed":
                    return old["receipt"]
                if old['state'] != 'rejected':
                    raise UncertainEffect(identity)
            if utcnow() >= job['publish_deadline']:
                raise PolicyError('Video effect deadline expired')
            if cost:
                spent = conn.execute("""SELECT COALESCE(sum(credits),0) AS n FROM video_effects
                    WHERE created_at >= date_trunc('month',now() AT TIME ZONE 'Asia/Seoul') AT TIME ZONE 'Asia/Seoul'
                    AND state<>'rejected'""").fetchone()["n"]
                episode = conn.execute("""SELECT COALESCE(sum(e.credits),0) AS n FROM video_effects e
                    JOIN video_jobs j ON j.id=e.job_id WHERE j.edition_id=%s AND e.state<>'rejected'""",
                                       (job["edition_id"],)).fetchone()["n"]
                if spent + cost > self.settings.video_monthly_credit_limit or episode + cost > self.settings.video_episode_credit_limit:
                    raise PolicyError("Video credit budget exhausted")
            if kind == "model":
                from ..owner_controls import effective_limits

                conn.execute("INSERT INTO daily_usage(day,reserved) VALUES(CURRENT_DATE,0) ON CONFLICT DO NOTHING")
                used = conn.execute("SELECT reserved FROM daily_usage WHERE day=CURRENT_DATE FOR UPDATE").fetchone()["reserved"]
                limit = effective_limits(conn, self.company)["company"]
                if limit is not None and used >= limit:
                    raise PolicyError("Company model allowance exhausted")
                conn.execute("UPDATE daily_usage SET reserved=reserved+1 WHERE day=CURRENT_DATE")
            conn.execute("""INSERT INTO video_effects(id,job_id,request_digest,kind,state,credits)
                VALUES(%s,%s,%s,%s,'running',%s) ON CONFLICT(id) DO UPDATE SET state='running',
                receipt=NULL,credits=EXCLUDED.credits,created_at=now(),completed_at=NULL""",
                         (identity, job["id"], digest(request), kind, cost))
        return None

    def reject_busy_model(self, job, key):
        # Official runtime's busy receipt is emitted before model execution, unlike a timeout.
        with self.db.transaction() as conn:
            row = conn.execute("""UPDATE video_effects SET state='rejected',receipt=%s WHERE id=%s
                AND kind='model' AND state='running' RETURNING created_at""",
                (Jsonb({'code': 'busy', 'executed': False}), str(job['id'])+':'+key)).fetchone()
            if row:
                conn.execute('UPDATE daily_usage SET reserved=GREATEST(0,reserved-1) WHERE day=%s::date',
                             (row['created_at'],))

    def defer(self, job, seconds):
        self.save(job, job['state'], error='model_busy')
        with self.db.transaction() as conn:
            conn.execute('UPDATE video_jobs SET lease_until=%s WHERE id=%s AND state=%s',
                         (datetime.now(UTC)+timedelta(seconds=max(5,min(seconds or 30,60))), job['id'], job['state']))

    def finish_effect(self, job, key, receipt):
        with self.db.transaction() as conn:
            conn.execute("UPDATE video_effects SET state='completed',receipt=%s,completed_at=now() WHERE id=%s AND state='running'",
                         (Jsonb(receipt), str(job["id"]) + ":" + key))

    def notice(self, job):
        with self.db.transaction() as conn:
            current = conn.execute("SELECT * FROM video_jobs WHERE id=%s FOR UPDATE", (job["id"],)).fetchone()
            if (current["review_message_id"] or current['state'] != 'awaiting_approval'
                    or utcnow() >= current['publish_deadline'] or not self.allowed(current)):
                return
            project = self.company._project(conn, current["source"]["project_id"])
            # The existing briefing parent must have a confirmed Slack receipt before threading the review.
            if not project["thread_ts"]:
                return
            mid = stable("video-review:" + str(current["id"]))
            text = (f"영상 검토 · v{current['version']}\n{current['plan']['title']}\n"
                    f"https://www.youtube.com/watch?v={current['youtube_id']}\n"
                    f"자료 기준: {current['source']['cutoff']}\n"
                    f"길이: {round(current['artifacts']['duration'])}초 · 자동 기술 검사 통과\n"
                    "비공개 업로드 완료. 전체 시청 후 공개·수정·보류를 선택하세요. 공개 승인은 09:00 KST에 만료됩니다.")
            self.company._message(conn, project, None, "market_brief", "video_review", text, message_id=mid)
            conn.execute("UPDATE video_jobs SET review_message_id=%s WHERE id=%s", (mid, current["id"]))

    def pending_notices(self):
        with self.db.transaction() as conn:
            return conn.execute("""SELECT * FROM video_jobs WHERE state='awaiting_approval'
                AND review_message_id IS NULL AND publish_deadline>%s""", (utcnow(),)).fetchall()

    def late_notices(self):
        """One durable 08:30 status notice per edition; no new model call."""
        with self.db.transaction() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK,))
            jobs = conn.execute("""SELECT * FROM video_jobs WHERE publish_deadline-interval '30 minutes'<=%s
                AND publish_deadline>%s AND state NOT IN ('published','awaiting_approval','held','superseded','expired')""",
                                (utcnow(), utcnow())).fetchall()
            for job in jobs:
                mid = stable('video-late:'+str(job['edition_id']))
                if not self.allowed(job) or conn.execute('SELECT 1 FROM messages WHERE id=%s', (mid,)).fetchone():
                    continue
                project = self.company._project(conn, job['source']['project_id'])
                if project['thread_ts'] and project['status'] == 'active':
                    self.company._message(conn, project, None, 'market_brief', 'video_status',
                        f"영상 제작 현황 · 08:30 KST\n검토본이 아직 준비되지 않았습니다. 상태: {job['state']}\n"
                        '원문 브리핑은 그대로 확인할 수 있습니다. 준비되면 비공개 영상 링크를 전달합니다. '
                        '09:00 KST 이후에는 일반 공개 승인이 만료됩니다.', message_id=mid)

    def action(self, identity, action: VideoAction, owner, channel, event_key, *, message_ts=None, at=None):
        at = at or utcnow()
        if not self.enabled():
            raise PolicyError("Video production is disabled")
        with self.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            job = conn.execute("SELECT * FROM video_jobs WHERE id=%s FOR UPDATE", (str(UUID(identity)),)).fetchone()
            if (not job or not self.allowed(job) or owner != job["source"]["owner_user"] or channel != job["source"]["channel"]
                    or owner not in self.settings.slack_allowed_users or channel not in self.settings.slack_allowed_channels
                    or action.version != job["version"] or action.artifact_digest != job["artifact_digest"]):
                raise PolicyError("Invalid video approval identity or version")
            if message_ts is not None:
                sent = conn.execute("SELECT sent_ts,status FROM outbox WHERE id=%s", (job["review_message_id"],)).fetchone()
                if not sent or sent["status"] != "delivered" or sent["sent_ts"] != message_ts:
                    raise PolicyError("Video review message receipt mismatch")
            old = conn.execute("SELECT * FROM video_actions WHERE event_key=%s", (event_key,)).fetchone()
            if old:
                if old["job_id"] != job["id"] or old["action"] != action.action or old["owner_user"] != owner:
                    raise PolicyError("Approval event collision")
                return {"state": job["state"], "duplicate": True}
            if job["state"] != "awaiting_approval":
                raise PolicyError("Video is no longer awaiting approval")
            if action.action == "approve":
                if not self.settings.video_publish_enabled or at >= job["publish_deadline"]:
                    raise PolicyError("Public release disabled or approval expired; keep private")
                if (not job["review"] or not VideoReview.model_validate(job["review"]).passed()
                        or not job["artifacts"].get("qa_passed")
                        or job['artifact_digest'] != job['artifacts'].get('digest')):
                    raise PolicyError("Video QA incomplete")
                state = "approved"
            else:
                state = "held" if action.action == "hold" else "superseded"
            conn.execute("""INSERT INTO video_actions(event_key,job_id,owner_user,action,artifact_digest)
                VALUES(%s,%s,%s,%s,%s)""", (event_key, job["id"], owner, action.action, action.artifact_digest))
            conn.execute("UPDATE video_jobs SET state=%s,approved_at=%s,approved_by=%s,lease_until=NULL WHERE id=%s",
                         (state, at if state == "approved" else None, owner if state == "approved" else None, job["id"]))
            if action.action == "revise":
                if at >= job["publish_deadline"]:
                    raise PolicyError("Revision requires a current morning edition")
                version = conn.execute("SELECT max(version)+1 AS n FROM video_jobs WHERE edition_id=%s",
                                       (job["edition_id"],)).fetchone()["n"]
                identity = stable("video:" + str(job["edition_id"]) + ":" + str(version))
                conn.execute("""INSERT INTO video_jobs(id,edition_id,version,source_digest,source,policy,feedback,publish_deadline)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""", (identity, job["edition_id"], version, job["source_digest"],
                        Jsonb(job["source"]), Jsonb(job["policy"]), action.note or "Make the text clearer and less repetitive.",
                        job["publish_deadline"]))
            return {"state": state}

    def status(self):
        with self.db.transaction() as conn:
            rows = conn.execute("""SELECT id,edition_id,version,state,youtube_id,error,created_at,publish_deadline
                FROM video_jobs ORDER BY created_at DESC LIMIT 30""").fetchall()
            usage = conn.execute("SELECT state,COALESCE(sum(credits),0) AS credits FROM video_effects GROUP BY state").fetchall()
        return as_json({"enabled": self.enabled(), "upload_enabled": self.settings.video_upload_enabled,
                        "publish_enabled": self.settings.video_publish_enabled, "jobs": rows, "effects": usage})
