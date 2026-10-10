import shutil
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from ..company import PolicyError, as_json, stable
from .contracts import POLICY_VERSION, VideoAction, VideoReview, digest

KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")
# Brief kind -> video edition. Both follow the briefing schedule, so market holidays skip both the same way.
EDITIONS = {"am": "am", "pm": "close"}


def _clock(value, default):
    hour, minute = map(int, (value or default).split(":"))
    return time(hour, minute)


def publish_time(edition, settings=None):
    """Owner's YouTube publish time for the edition (KST). Morning follows New York daylight time: 07:00 KST while
    the US close is 05:00 KST, 07:50 KST in winter (from 2026-11-01). Close edition: 18:00 KST."""
    day = edition["day"]
    if edition["kind"] == "pm":
        return datetime.combine(day, _clock(getattr(settings, "video_pm_publish", None), "18:00"), KST)
    summer = bool(datetime.combine(day, time(6), NY).dst())
    value = getattr(settings, "video_am_publish_dst" if summer else "video_am_publish_std", None)
    return datetime.combine(day, _clock(value, "07:00" if summer else "07:50"), KST)


def edition_times(edition, settings=None):
    """Files-ready target and generation deadline. Work starts as soon as the brief body is delivered (no waiting for
    due or a fixed time); the files should be in the brief thread VIDEO_PUBLISH_LEAD_MINUTES before the publish time
    (AM 06:50 / winter 07:40, PM 17:50). A late episode is still delivered with a one-line delay note. New generation
    stops at 09:00 KST for the morning edition and 2h30m after the target for the close edition."""
    lead = getattr(settings, "video_publish_lead_minutes", None)
    target = publish_time(edition, settings) - timedelta(minutes=10 if lead is None else lead)
    if edition["kind"] == "pm":
        return target, target + timedelta(minutes=150)
    return target, datetime.combine(edition["day"], time(9), KST)
EDITION_NAMES = {"am": "아침 브리핑", "close": "마감 브리핑"}
REASONS = {
    "runway_unavailable": "Runway 음성 연결 실패(로그인 만료 또는 서비스 응답 없음). 유료 API로 전환하지 않았습니다. "
                          "서버에서 quant-company video-auth runway로 다시 로그인한 뒤 video retry로 재개합니다.",
    "runway_account": "Runway 계정·workspace가 설정과 다릅니다. 음성 생성 요청을 보내지 않았습니다.",
    "runway_balance": "Runway 잔액이 이 회차 예상 크레딧보다 적습니다. 충전·결제는 자동으로 하지 않습니다.",
    "credit_cap": "월 또는 회차 크레딧 한도에 걸려 음성 생성을 시작하지 않았습니다.",
    "plan_check_failed": "대본이 브리핑 본문 숫자·표기 검사를 통과하지 못했습니다.",
    "adaptation_review_failed": "대본 검토에서 불합격했습니다.",
    "external_effect_requires_reconciliation": "외부 요청 결과를 확인하지 못했습니다. 같은 요청을 자동으로 다시 보내지 않습니다.",
    "video_stage_failed:rendering": "렌더링 또는 기술 검사에 실패했습니다.",
}


def reason(code):
    code = code or ""
    if code.startswith("model_"):
        return "대본 작성 실행기 오류(" + code + ")."
    return REASONS.get(code, "제작 단계 실패(" + code + ").")


def delivery_text(job, artifacts, at=None):
    minutes, seconds = divmod(round(artifacts.get("duration", 0)), 60)
    publish = job["policy"].get("publish_at")
    target = job["policy"].get("review_target")
    timing = ""
    if publish:
        timing = f"공개 목표 {datetime.fromisoformat(publish).astimezone(KST):%H:%M} KST"
        if target and at and at > datetime.fromisoformat(target):
            late = round((at - datetime.fromisoformat(target)).total_seconds() / 60)
            timing += f" · 파일 준비 목표보다 {late}분 늦게 완성됐습니다"
        timing += "\n"
    return (f"영상 준비 완료 · {EDITION_NAMES.get(job['source'].get('edition_kind'), '브리핑')}\n"
            f"{artifacts.get('title', '')}\n길이 {minutes}분 {seconds:02}초 · 자동 기술 검사 통과\n" + timing +
            "첨부: 영상(MP4) · 썸네일(PNG) · 자막(SRT) · 업로드 문안(upload.txt: 제목·설명·챕터·태그·고정 댓글)\n"
            "전체 시청 후 직접 업로드해 주세요.")


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

    @staticmethod
    def eligible(edition):
        """Every delivered body makes a video: a full or reduced ('일부 확인 중') body, and a fact-list body that kept
        the collected original facts even when the analysis fell back (briefing 'always body', PR #132). Only a
        notice-only edition (fallback and nothing collected) is skipped. Returns the skip reason or None."""
        quality = edition["quality"] or {}
        if not edition["rendered"] or quality.get("unreviewed_draft_preserved"):
            return "no_delivered_body"
        if quality.get("original_facts") or ("fallback" in quality and not quality["fallback"]):
            return None
        return str(quality.get("fallback") or "no_delivered_body")[:60]

    @staticmethod
    def episode_format(quality):
        """Fact-list edition without issue analysis gets the short episode (store → episode.check_structure)."""
        return "facts" if quality.get("original_facts") and not quality.get("issue_count") else "full"

    def enqueue(self, conn, edition, at):
        if not self.enabled() or not edition["publish"] or edition["kind"] not in EDITIONS:
            return None
        skipped = self.eligible(edition)
        if skipped:
            # The alert is posted in the brief's thread once its root message is delivered (skip_notices).
            conn.execute("INSERT INTO video_skips(edition_id,reason) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                         (edition["id"], skipped))
            return None
        quality = edition["quality"]
        # The delivered body is the only source. Proposal, bundle and raw articles are deliberately not frozen here.
        source = as_json({k: edition[k] for k in ("id", "day", "cutoff", "project_id", "owner_user", "channel",
                                                 "policy_digest")})
        source["body"] = list(edition["rendered"])
        source["quality"] = {"reduced": bool(quality.get("reduced")), "fallback": quality.get("fallback"),
                             "issue_count": quality.get("issue_count"), "original_facts": bool(quality.get("original_facts"))}
        source["format"] = self.episode_format(quality)
        source["edition_kind"] = EDITIONS[edition["kind"]]
        # Edition IDs differ per kind, so the morning and closing videos of one day never share a job.
        identity = stable("video:" + str(edition["id"]) + ":1")
        target, deadline = edition_times(edition, self.settings)
        if at >= deadline:
            return None
        policy = {"version": POLICY_VERSION, "voice": self.settings.video_voice,
                  "speech_model": "eleven_multilingual_v2", "model": self.settings.video_model,
                  "workspace_id": self.settings.video_runway_workspace_id,
                  "youtube_channel": self.settings.video_youtube_channel_id, "template": self.settings.video_template,
                  "edition": source["edition_kind"], "review_target": target.isoformat(),
                  "publish_at": publish_time(edition, self.settings).isoformat(),
                  # YouTube private upload + Slack approval only when explicitly enabled; otherwise the owner gets the
                  # files in the brief thread and uploads them himself.
                  "delivery": "youtube" if self.settings.video_upload_enabled else "slack"}
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
            # A new episode starts only after the brief itself reached Slack (root message receipt).
            row = conn.execute("""SELECT * FROM video_jobs j WHERE state=ANY(%s)
                AND (lease_until IS NULL OR lease_until<now())
                AND (state<>'queued' OR EXISTS (SELECT 1 FROM brief_messages m JOIN outbox o ON o.id=m.id
                     WHERE m.edition_id=j.edition_id AND m.part=0 AND o.status='delivered' AND o.sent_ts IS NOT NULL))
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

    def reject_busy_model(self, job, key, code='busy'):
        # Official runtime's busy/quota receipt is emitted before model execution, unlike a timeout.
        with self.db.transaction() as conn:
            row = conn.execute("""UPDATE video_effects SET state='rejected',receipt=%s WHERE id=%s
                AND kind='model' AND state='running' RETURNING created_at""",
                (Jsonb({'code': code, 'executed': False}), str(job['id'])+':'+key)).fetchone()
            if row:
                conn.execute('UPDATE daily_usage SET reserved=GREATEST(0,reserved-1) WHERE day=%s::date',
                             (row['created_at'],))

    def defer(self, job, seconds, code='busy'):
        # A subscription window may reopen later; the publish deadline still expires the job.
        ceiling = 900 if code == 'quota' else 60
        self.save(job, job['state'], error='model_'+code)
        with self.db.transaction() as conn:
            conn.execute('UPDATE video_jobs SET lease_until=%s WHERE id=%s AND state=%s',
                         (datetime.now(UTC)+timedelta(seconds=max(5,min(seconds or 30,ceiling))), job['id'], job['state']))

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
            deadline = current['publish_deadline'].astimezone(KST)
            text = (f"영상 검토 · v{current['version']}\n{current['plan']['title']}\n"
                    f"https://www.youtube.com/watch?v={current['youtube_id']}\n"
                    f"자료 기준: {current['source']['cutoff']}\n"
                    f"길이: {round(current['artifacts']['duration'])}초 · 자동 기술 검사 통과\n"
                    f"비공개 업로드 완료. 전체 시청 후 공개·수정·보류를 선택하세요. 공개 승인은 {deadline:%H:%M} KST에 만료됩니다.")
            self.company._message(conn, project, None, "market_brief", "video_review", text, message_id=mid)
            conn.execute("UPDATE video_jobs SET review_message_id=%s WHERE id=%s", (mid, current["id"]))

    def pending_notices(self):
        with self.db.transaction() as conn:
            return conn.execute("""SELECT * FROM video_jobs WHERE state='awaiting_approval'
                AND review_message_id IS NULL AND publish_deadline>%s""", (utcnow(),)).fetchall()

    def prune(self):
        """Delete artifacts of jobs that ended more than the retention period ago. YouTube keeps the published copy;
        the DB keeps receipts, manifest digests and the plan."""
        root = Path(self.settings.video_artifact_dir)
        with self.db.transaction() as conn:
            rows = conn.execute("""SELECT id FROM video_jobs WHERE state IN ('published','expired','held','superseded','blocked',
                'delivered','delivery_failed','delivery_uncertain')
                AND updated_at < now() - make_interval(days => %s)""", (self.settings.video_retention_days,)).fetchall()
        removed = 0
        for row in rows:
            target = root / str(row['id'])
            if target.is_dir() and not target.is_symlink() and target.resolve().is_relative_to(root.resolve()):
                shutil.rmtree(target)
                removed += 1
        return removed

    def hold_for_disk(self, free_gb):
        """Low disk: queued episodes do not start; one status message per job, nothing is charged."""
        with self.db.transaction() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK,))
            for job in conn.execute("SELECT * FROM video_jobs WHERE state='queued' FOR UPDATE").fetchall():
                conn.execute("UPDATE video_jobs SET state='blocked',error='insufficient_disk',updated_at=now() WHERE id=%s", (job['id'],))
                mid = stable('video-disk:' + str(job['id']))
                project = self.company._project(conn, job['source']['project_id'])
                if project['thread_ts'] and project['status'] == 'active' and not conn.execute(
                        'SELECT 1 FROM messages WHERE id=%s', (mid,)).fetchone():
                    self.company._message(conn, project, None, 'market_brief', 'video_status',
                        f"영상 제작 보류 · 디스크 여유 {free_gb:.1f}GB\n최소 {self.settings.video_min_free_gb:g}GB가 필요해 이 회차는 시작하지 않았습니다. "
                        '원문 브리핑은 그대로 확인할 수 있습니다. 공간을 확보한 뒤 운영자가 다시 시도합니다.', message_id=mid)

    def late_notices(self):
        """One durable status notice at the files-ready target (publish time - lead); no new model call."""
        with self.db.transaction() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK,))
            jobs = conn.execute("""SELECT * FROM video_jobs
                WHERE COALESCE((policy->>'review_target')::timestamptz, publish_deadline-interval '30 minutes')<=%s
                AND publish_deadline>%s AND state NOT IN ('published','awaiting_approval','held','superseded','expired',
                'delivering','delivered','delivery_failed','delivery_uncertain')""",
                                (utcnow(), utcnow())).fetchall()
            for job in jobs:
                mid = stable('video-late:'+str(job['edition_id']))
                if not self.allowed(job) or conn.execute('SELECT 1 FROM messages WHERE id=%s', (mid,)).fetchone():
                    continue
                project = self.company._project(conn, job['source']['project_id'])
                if project['thread_ts'] and project['status'] == 'active':
                    target = datetime.fromisoformat(job['policy'].get('review_target') or
                                                    (job['publish_deadline'] - timedelta(minutes=30)).isoformat()).astimezone(KST)
                    ending = (f"{job['publish_deadline'].astimezone(KST):%H:%M} KST까지 완성되지 않으면 이 회차 제작을 멈춥니다."
                              if job['policy'].get('delivery', 'youtube') == 'slack' else
                              '준비되면 비공개 영상 링크를 전달합니다. '
                              f"{job['publish_deadline'].astimezone(KST):%H:%M} KST 이후에는 일반 공개 승인이 만료됩니다.")
                    ready = '영상 파일' if job['policy'].get('delivery', 'youtube') == 'slack' else '검토본'
                    self.company._message(conn, project, None, 'market_brief', 'video_status',
                        f"영상 제작 현황 · {target:%H:%M} KST\n{ready}이 아직 준비되지 않았습니다. 상태: {job['state']}\n"
                        '원문 브리핑은 그대로 확인할 수 있습니다. ' + ending, message_id=mid)

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

    # ---- Owner file delivery (VIDEO_UPLOAD_ENABLED=false) -------------------------------------------------------

    def deliver(self, job, artifacts):
        """Render finished: hand the files to the Slack dispatcher, which alone holds the bot token."""
        with self.db.transaction() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK,))
            current = conn.execute('SELECT * FROM video_jobs WHERE id=%s FOR UPDATE', (job['id'],)).fetchone()
            if current['state'] not in {'rendering', 'delivery_failed'}:
                raise PolicyError('Video changed while processing')
            project = self.company._project(conn, current['source']['project_id'])
            if not project['thread_ts'] or project['status'] != 'active':
                raise PolicyError('Brief thread receipt missing')
            attempt = conn.execute('SELECT COALESCE(max(attempt),0)+1 AS n FROM video_deliveries WHERE job_id=%s',
                                   (current['id'],)).fetchone()['n']
            mid = stable(f"video-files:{current['id']}:{attempt}")
            self.company._message(conn, project, None, 'market_brief', 'video_files', delivery_text(current, artifacts, utcnow()),
                                  message_id=mid)
            conn.execute('INSERT INTO video_deliveries(id,job_id,attempt) VALUES(%s,%s,%s)', (mid, current['id'], attempt))
            conn.execute("""UPDATE video_jobs SET state='delivering',artifacts=%s,artifact_digest=%s,delivery_message_id=%s,
                error=NULL,lease_until=NULL,updated_at=now() WHERE id=%s""",
                         (Jsonb(artifacts), artifacts['digest'], mid, current['id']))
            return str(mid)

    def delivery_gate(self, conn, row):
        job = conn.execute("SELECT * FROM video_jobs WHERE delivery_message_id=%s", (row['id'],)).fetchone()
        if job and job['state'] == 'delivering' and row['thread_ts'] and self.allowed(job):
            return True
        conn.execute("UPDATE outbox SET status='stale',error='video_delivery_disabled_or_changed' WHERE id=%s", (row['id'],))
        conn.execute("UPDATE video_deliveries SET state='stale',updated_at=now() WHERE id=%s AND state='pending'", (row['id'],))
        return False

    def sync_deliveries(self):
        """A dispatcher that died mid-upload leaves the outbox row uncertain (sender lease expiry) or blocked; reflect it
        on the job once, with the thread notice, instead of leaving it 'delivering'."""
        with self.db.transaction() as conn:
            rows = conn.execute("""SELECT j.delivery_message_id AS id,o.status,o.error FROM video_jobs j
                JOIN outbox o ON o.id=j.delivery_message_id WHERE j.state='delivering'
                AND o.status IN ('uncertain','blocked','stale')""").fetchall()
        for row in rows:
            state = 'uncertain' if row['status'] == 'uncertain' else 'failed'
            with self.db.transaction() as conn:
                receipt = conn.execute('SELECT receipt FROM video_deliveries WHERE id=%s', (row['id'],)).fetchone()
            self.delivery_result(row['id'], state, receipt['receipt'] if receipt else {}, row['error'] or 'outbox_' + row['status'])
        return len(rows)

    def delivery_job(self, message_id):
        with self.db.transaction() as conn:
            return conn.execute('SELECT * FROM video_jobs WHERE delivery_message_id=%s', (message_id,)).fetchone()

    def delivery_receipt(self, message_id, receipt):
        """Persist each Slack file ID before its bytes are sent, so an interrupted upload is traceable."""
        with self.db.transaction() as conn:
            conn.execute('UPDATE video_deliveries SET receipt=%s,updated_at=now() WHERE id=%s', (Jsonb(receipt), message_id))

    def delivery_result(self, message_id, state, receipt, error=None):
        """delivered | failed (Slack definitively rejected before sharing) | uncertain (completion outcome unknown)."""
        job_state = {'delivered': 'delivered', 'failed': 'delivery_failed', 'uncertain': 'delivery_uncertain'}[state]
        with self.db.transaction() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK,))
            conn.execute('UPDATE video_deliveries SET state=%s,receipt=%s,error=%s,updated_at=now() WHERE id=%s',
                         (state, Jsonb(receipt), error, message_id))
            job = conn.execute("""UPDATE video_jobs SET state=%s,error=%s,updated_at=now()
                WHERE delivery_message_id=%s AND state='delivering' RETURNING *""", (job_state, error, message_id)).fetchone()
            if not job or state == 'delivered':
                return
            project = self.company._project(conn, job['source']['project_id'])
            if project['thread_ts'] and project['status'] == 'active':
                folder = job['artifacts']['directory']
                head = ('영상 파일 전달 실패' if state == 'failed' else '영상 파일 전달 결과 확인 필요')
                detail = ('Slack이 업로드를 거절해 파일이 공유되지 않았습니다.' if state == 'failed' else
                          'Slack 응답을 받지 못해 파일이 공유됐는지 알 수 없습니다. 같은 파일을 자동으로 다시 올리지 않습니다.')
                self.company._message(conn, project, None, 'market_brief', 'video_status',
                    f"{head} · {job['artifacts'].get('title', '')}\n{detail} 사유: {error}\n"
                    f"서버 보관 경로: {folder}\n(video.mp4 · thumbnail.png · subtitles.srt · upload.txt, "
                    f"{self.settings.video_retention_days}일 보관)", message_id=stable('video-delivery-notice:'+str(message_id)))

    # ---- Alerts ------------------------------------------------------------------------------------------------

    def alert(self, job):
        """One thread notice when an episode stops (blocked/uncertain). No spend is retried automatically."""
        with self.db.transaction() as conn:
            current = conn.execute('SELECT * FROM video_jobs WHERE id=%s', (job['id'],)).fetchone()
            if current['state'] not in {'blocked', 'uncertain'} or not self.allowed(current):
                return None
            mid = stable(f"video-alert:{current['id']}:{current['state']}:{current['error']}")
            if conn.execute('SELECT 1 FROM messages WHERE id=%s', (mid,)).fetchone():
                return None
            project = self.company._project(conn, current['source']['project_id'])
            if not project['thread_ts'] or project['status'] != 'active':
                return None
            spent = conn.execute("""SELECT COALESCE(sum(e.credits),0) AS n FROM video_effects e JOIN video_jobs j ON j.id=e.job_id
                WHERE j.edition_id=%s AND e.state<>'rejected'""", (current['edition_id'],)).fetchone()['n']
            month = conn.execute("""SELECT COALESCE(sum(credits),0) AS n FROM video_effects
                WHERE created_at >= date_trunc('month',now() AT TIME ZONE 'Asia/Seoul') AT TIME ZONE 'Asia/Seoul'
                AND state<>'rejected'""").fetchone()['n']
            self.company._message(conn, project, None, 'market_brief', 'video_status',
                f"영상 제작 중단 · {EDITION_NAMES.get(current['source'].get('edition_kind'), '브리핑')}\n"
                f"사유: {reason(current['error'])}\n"
                f"크레딧: 이 회차 {spent} · 이번 달 {month}/{self.settings.video_monthly_credit_limit} "
                f"(회차 한도 {self.settings.video_episode_credit_limit})\n원문 브리핑은 그대로 확인할 수 있습니다.", message_id=mid)
            return str(mid)

    def skip_notices(self):
        """Fallback editions: no video, one alert in the brief's thread after the brief itself is delivered."""
        with self.db.transaction() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK,))
            rows = conn.execute("""SELECT s.*,e.day,e.kind,e.project_id FROM video_skips s JOIN brief_editions e ON e.id=s.edition_id
                WHERE s.alert_message_id IS NULL AND s.created_at>now()-interval '12 hours' FOR UPDATE OF s""").fetchall()
            sent = 0
            for row in rows:
                project = self.company._project(conn, row['project_id']) if row['project_id'] else None
                if not project or not project['thread_ts'] or project['status'] != 'active':
                    continue
                mid = stable('video-skip:'+str(row['edition_id']))
                self.company._message(conn, project, None, 'market_brief', 'video_status',
                    f"영상 제작 안 함 · {row['day']:%m/%d} {EDITION_NAMES[EDITIONS[row['kind']]]}\n"
                    f"이번 발송본은 본문이 없는 대체 공지(사유: {row['reason']})여서 영상을 만들지 않았습니다. 크레딧은 쓰지 않았습니다.",
                    message_id=mid)
                conn.execute('UPDATE video_skips SET alert_message_id=%s WHERE edition_id=%s', (mid, row['edition_id']))
                sent += 1
            return sent

    # ---- Image rotation ----------------------------------------------------------------------------------------

    def asset_usage(self):
        """{asset_id: last used_at} over every episode and slot."""
        with self.db.transaction() as conn:
            return {r['asset_id']: r['at'] for r in conn.execute(
                'SELECT asset_id,max(used_at) AS at FROM video_asset_usage GROUP BY asset_id').fetchall()}

    def record_assets(self, job, plan, library, at=None):
        from .episode import assets

        rows = [(plan.thumbnail_image, 'thumbnail')] if getattr(plan, 'thumbnail_image', '') else []
        rows += [(a, 'scene') for scene in getattr(plan, 'scenes', []) for a in assets(getattr(scene, 'data', {}))]
        with self.db.transaction() as conn:
            for asset, slot in dict.fromkeys(rows):
                conn.execute("""INSERT INTO video_asset_usage(job_id,asset_id,file,slot,used_at) VALUES(%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING""", (job['id'], asset, library.get(asset, {}).get('file', ''), slot, at or utcnow()))

    def status(self):
        with self.db.transaction() as conn:
            rows = conn.execute("""SELECT id,edition_id,version,state,youtube_id,error,created_at,publish_deadline
                FROM video_jobs ORDER BY created_at DESC LIMIT 30""").fetchall()
            usage = conn.execute("SELECT state,COALESCE(sum(credits),0) AS credits FROM video_effects GROUP BY state").fetchall()
            deliveries = conn.execute("""SELECT job_id,attempt,state,error,updated_at FROM video_deliveries
                ORDER BY updated_at DESC LIMIT 30""").fetchall()
            skips = conn.execute("SELECT edition_id,reason,alert_message_id IS NOT NULL AS alerted,created_at FROM video_skips "
                                 "ORDER BY created_at DESC LIMIT 30").fetchall()
        return as_json({"enabled": self.enabled(), "upload_enabled": self.settings.video_upload_enabled,
                        "publish_enabled": self.settings.video_publish_enabled, "jobs": rows, "effects": usage,
                        "deliveries": deliveries, "skips": skips})
