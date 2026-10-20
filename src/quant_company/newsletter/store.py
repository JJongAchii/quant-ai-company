"""PostgreSQL records of the brief email: recipients, one issue per AM or PM edition, per-recipient send receipts.

Only masked addresses leave this module through logs, receipts and Slack text."""

import logging
import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from ..company import as_json, fingerprint, stable
from . import checks, contracts
from .contracts import KIND_NAMES

LOG = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")
# A checked draft is due this long after the edition's commit; then the deterministic fallback goes out (design §3.3).
# The episode starts in the same minute as the commit and takes the model first for 1.5-2 hours (10/19 PM 16:46-18:31,
# 10/16 PM 18:14-20:04), so a shorter wait sent the fallback every day. A test send waits only the short time.
FALLBACK_AFTER = timedelta(hours=2, minutes=30)
TEST_FALLBACK_AFTER = timedelta(minutes=45)
# The morning email goes out before the 09:00 KRX open, so an AM edition waits until 08:40 KST at the latest; a late
# commit still gets the short wait.
AM_FALLBACK_BY = time(8, 40)
# Regular sends of an edition start within this window; a flag switched on later never mails an old edition.
SEND_WINDOW = timedelta(hours=12)
AUTO_ATTEMPTS, TEST_ATTEMPTS = 3, 1
# claude-runtime runs one turn at a time and the edition's video comes first: no email turn starts while an episode of
# the last day is in a stage that calls the model (video/store.py LOGIN_STAGES).
VIDEO_MODEL_STATES = ("queued", "reviewing", "synthesizing", "inspecting")
VIDEO_NEEDS_MODEL = """EXISTS (SELECT 1 FROM video_jobs v WHERE v.state=ANY(%(video_states)s)
    AND v.created_at>%(video_since)s)"""
# Turns that wrote something; a turn given up for the video ('yielded') does not use up an attempt.
WRITTEN = "(SELECT count(*) FROM jsonb_array_elements(attempts) a WHERE a->>'outcome'<>'yielded')"
# No SMTP session lasts this long, so a row still 'sending' was interrupted (crash, release, lost connection).
STALE_SENDING = timedelta(minutes=10)
TERMINAL = ("sent", "uncertain", "failed", "skipped")
ADDRESS = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
ANY_ADDRESS = re.compile(r"[^\s<>|@:]+@[^\s<>|]+")
STATE_NAMES = {"active": "수신 중", "removed": "삭제됨", "unsubscribed": "본인 수신 거부"}
SKIPPED = {"fallback_failed": "메일을 만드는 중 오류가 나서 보내지 못했습니다",
           "enqueue_failed": "메일 작업을 만드는 중 서버 오류가 나서 메일을 만들지 않았습니다. 브리핑 본문은 그대로 발송됐습니다"}
NO_ISSUES = "이번 {name} 브리핑에는 주요 이슈 분석이 없어 메일을 만들지 않았습니다"


def utcnow():
    return datetime.now(UTC)


def fallback_at(edition, at, *, auto):
    """When the deterministic fallback replaces a draft that is not ready (see FALLBACK_AFTER, AM_FALLBACK_BY)."""
    if not auto:
        return at + TEST_FALLBACK_AFTER
    due = at + FALLBACK_AFTER
    if edition["kind"] == "am":
        due = min(due, datetime.combine(date.fromisoformat(str(edition["day"])), AM_FALLBACK_BY, KST))
    return max(due, at + TEST_FALLBACK_AFTER)


def video_window(at):
    return {"video_states": list(VIDEO_MODEL_STATES), "video_since": at - timedelta(hours=24)}


def mask(address):
    local, _, domain = (address or "").partition("@")
    keep = local[:2] if len(local) > 2 else local[:1]
    return f"{keep}***@{domain}" if domain else "***"


def masked(text):
    """Owner command text as it may be stored outside the recipient table (task, message, project title)."""
    text = re.sub(r"<mailto:[^|>]+\|([^>]+)>", r"\1", text)
    text = re.sub(r"<mailto:([^>]+)>", r"\1", text)
    return ANY_ADDRESS.sub(lambda m: mask(m[0]), text)


def valid_address(address):
    return bool(address) and len(address) <= 254 and bool(ADDRESS.fullmatch(address))


class NewsletterStore:
    def __init__(self, company):
        self.company, self.db, self.settings = company, company.db, company.settings

    # ---- Issues --------------------------------------------------------------------------------------------

    def enqueue_isolated(self, conn, edition, at):
        """Called inside the edition's commit: a failure here never holds back the brief body. The email is then
        recorded as skipped, so the brief's thread says once that there is no email today."""
        try:
            with conn.transaction():
                return self.enqueue(conn, edition, at)
        except Exception:
            LOG.exception("newsletter enqueue failed for edition %s; the brief is committed without an email",
                          edition["id"])
        try:
            with conn.transaction():
                self.skip(conn, edition, at, "enqueue_failed")
        except Exception:
            LOG.exception("newsletter skip record failed for edition %s", edition["id"])
        return None

    def skip(self, conn, edition, at, reason):
        conn.execute("""INSERT INTO newsletter_issues(id,edition_id,state,auto,reason,max_attempts,fallback_at,expires_at,
            next_at,created_at) VALUES(%s,%s,'skipped',true,%s,%s,%s,%s,%s,%s) ON CONFLICT(edition_id) DO NOTHING""",
                     (stable(f"newsletter:{edition['id']}"), edition["id"], reason, AUTO_ATTEMPTS,
                      fallback_at(edition, at, auto=True), at + SEND_WINDOW, at, at))

    def enqueue(self, conn, edition, at):
        if not self.settings.newsletter_enabled or edition["kind"] not in KIND_NAMES or not edition["publish"]:
            return None
        return self.ensure_issue(conn, edition, at, auto=True)

    def ensure_issue(self, conn, edition, at, *, auto):
        identity = stable(f"newsletter:{edition['id']}")
        proposal = edition["proposal"] or {}
        # A notice-only or numbers-only edition has nothing to write about: no email that day, one line in the thread.
        reason = "no_proposal" if not edition["proposal"] else "no_issues" if not proposal.get("issues") else None
        source = None if reason else contracts.source(edition)
        if source is not None and not source["issues"]:
            reason, source = "no_issues", None
        conn.execute("""INSERT INTO newsletter_issues(id,edition_id,state,auto,source,source_digest,reason,max_attempts,
            fallback_at,expires_at,next_at,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(edition_id) DO NOTHING""",
                     (identity, edition["id"], "skipped" if reason else "writing", auto,
                      Jsonb(source) if source else None, fingerprint(source) if source else None, reason,
                      AUTO_ATTEMPTS if auto else TEST_ATTEMPTS, fallback_at(edition, at, auto=auto), at + SEND_WINDOW,
                      at, at))
        return conn.execute("SELECT * FROM newsletter_issues WHERE edition_id=%s", (edition["id"],)).fetchone()

    def has_writing(self):
        with self.db.transaction() as conn:
            return bool(conn.execute("SELECT 1 FROM newsletter_issues WHERE state='writing' LIMIT 1").fetchone())

    def claim_writing(self, at=None):
        """The next issue the writer may ask claude-runtime about: before its fallback time, attempts left, either an
        automatic issue with the flag on or a waiting test send, and no episode needing the model."""
        at = at or utcnow()
        with self.db.transaction() as conn:
            row = conn.execute(f"""SELECT * FROM newsletter_issues i WHERE state='writing' AND next_at<=%(at)s
                AND (lease_until IS NULL OR lease_until<%(at)s) AND fallback_at>%(at)s AND {WRITTEN}<max_attempts
                AND ((auto AND %(enabled)s) OR EXISTS (SELECT 1 FROM newsletter_sends s WHERE s.issue_id=i.id
                     AND s.kind='test' AND s.state='pending'))
                AND NOT {VIDEO_NEEDS_MODEL}
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""",
                               {"at": at, "enabled": self.settings.newsletter_enabled, **video_window(at)}).fetchone()
            if row:
                conn.execute("UPDATE newsletter_issues SET lease_until=%s WHERE id=%s", (at + timedelta(minutes=15), row["id"]))
            return row

    def freeze_request(self, issue, request):
        with self.db.transaction() as conn:
            conn.execute("UPDATE newsletter_issues SET request=%s,updated_at=now() WHERE id=%s AND state='writing'",
                         (Jsonb(request), issue["id"]))

    def defer(self, issue, seconds, code, at=None):
        at = at or utcnow()
        with self.db.transaction() as conn:
            conn.execute("""UPDATE newsletter_issues SET next_at=%s,lease_until=NULL,reason=%s,updated_at=now()
                WHERE id=%s AND state='writing'""", (at + timedelta(seconds=seconds), "model_" + code, issue["id"]))

    def attempt(self, issue, record):
        """A finished turn without a passing draft; the next attempt is a new request."""
        with self.db.transaction() as conn:
            conn.execute("""UPDATE newsletter_issues SET attempts=attempts||%s::jsonb,request=NULL,lease_until=NULL,
                updated_at=now() WHERE id=%s AND state='writing'""", (Jsonb([as_json(record)]), issue["id"]))

    def accept(self, issue, draft, record):
        with self.db.transaction() as conn:
            row = conn.execute("""UPDATE newsletter_issues SET state='ready',draft=%s,origin='model',
                attempts=attempts||%s::jsonb,request=NULL,lease_until=NULL,reason=NULL,updated_at=now()
                WHERE id=%s AND state='writing' RETURNING id""",
                               (Jsonb(draft.model_dump(mode="json")), Jsonb([as_json(record)]), issue["id"])).fetchone()
            return bool(row)

    def in_flight_while_video(self, at=None):
        """Emails whose frozen turn may hold the runtime slot while an episode needs the model."""
        at = at or utcnow()
        with self.db.transaction() as conn:
            return conn.execute(f"""SELECT * FROM newsletter_issues WHERE state='writing' AND request IS NOT NULL
                AND {VIDEO_NEEDS_MODEL}""", video_window(at)).fetchall()

    def yielded(self, issue, request_id, at=None):
        """The turn was given up for the video: a new request later, without using an attempt."""
        at = at or utcnow()
        record = {"n": len(issue["attempts"]) + 1, "request_id": request_id, "outcome": "yielded", "at": at.isoformat()}
        with self.db.transaction() as conn:
            conn.execute("""UPDATE newsletter_issues SET attempts=attempts||%s::jsonb,request=NULL,next_at=%s,
                updated_at=now() WHERE id=%s AND state='writing'""",
                         (Jsonb([record]), at + timedelta(minutes=1), issue["id"]))

    def cancels(self):
        with self.db.transaction() as conn:
            return conn.execute("""SELECT id,cancel_request FROM newsletter_issues WHERE cancel_request IS NOT NULL
                AND state<>'writing'""").fetchall()

    def cancelled(self, row):
        with self.db.transaction() as conn:
            conn.execute("UPDATE newsletter_issues SET cancel_request=NULL WHERE id=%s", (row["id"],))

    def release(self, issue):
        with self.db.transaction() as conn:
            conn.execute("UPDATE newsletter_issues SET lease_until=NULL WHERE id=%s", (issue["id"],))

    def fallbacks(self, at=None):
        """Issues past their fallback time (or out of attempts) get the deterministic draft from the body text."""
        at = at or utcnow()
        done = 0
        with self.db.transaction() as conn:
            rows = conn.execute(f"""SELECT *,{WRITTEN} AS written FROM newsletter_issues WHERE state='writing'
                AND (fallback_at<=%s OR {WRITTEN}>=max_attempts)
                AND (auto OR EXISTS (SELECT 1 FROM newsletter_sends s WHERE s.issue_id=newsletter_issues.id
                     AND s.kind='test' AND s.state='pending'))
                FOR UPDATE SKIP LOCKED""", (at,)).fetchall()
            for row in rows:
                try:
                    draft = checks.fallback(row["source"])
                    found = checks.findings(draft, row["source"])
                except (ValueError, KeyError, TypeError, IndexError):
                    # Should never happen (the fallback is the body's own text); said once in the thread, not retried.
                    LOG.exception("newsletter fallback failed for issue %s", row["id"])
                    conn.execute("""UPDATE newsletter_issues SET state='skipped',reason='fallback_failed',request=NULL,
                        lease_until=NULL,updated_at=now() WHERE id=%s""", (row["id"],))
                    conn.execute("""UPDATE newsletter_sends SET state='failed',error='email_not_built',updated_at=now()
                        WHERE issue_id=%s AND state='pending'""", (row["id"],))
                    continue
                why = "model_checks_failed" if row["written"] >= row["max_attempts"] else (
                    row["reason"] or "model_not_ready")
                conn.execute("""UPDATE newsletter_issues SET state='ready',draft=%s,origin='fallback',findings=%s,
                    reason=%s,cancel_request=request->>'request_id',request=NULL,lease_until=NULL,updated_at=now()
                    WHERE id=%s""", (Jsonb(draft.model_dump(mode="json")), Jsonb(found), why, row["id"]))
                done += 1
        return done

    # ---- Recipients ----------------------------------------------------------------------------------------

    @staticmethod
    def recipient(conn, address, lock=False):
        return conn.execute("SELECT * FROM newsletter_recipients WHERE address=%s" + (" FOR UPDATE" if lock else ""),
                            (address,)).fetchone()

    @staticmethod
    def set_state(conn, recipient_id, state):
        conn.execute("""UPDATE newsletter_recipients SET state=%s,updated_at=now(),
            unsubscribed_at=CASE WHEN %s='unsubscribed' THEN now() ELSE unsubscribed_at END WHERE id=%s""",
                     (state, state, recipient_id))

    def latest_edition(self, conn):
        return conn.execute("""SELECT * FROM brief_editions WHERE kind IN ('am','pm') AND publish AND state='committed'
            AND channel=%s AND owner_user=%s ORDER BY day DESC,committed_at DESC LIMIT 1""",
                            (self.settings.briefing_channel_id, self.settings.briefing_owner_user)).fetchone()

    def brief_thread(self, conn, edition_id=None):
        """The brief's Slack thread (the edition's, or the latest AM or PM one) once its root post is delivered."""
        return conn.execute("""SELECT p.* FROM brief_editions e JOIN projects p ON p.id=e.project_id
            WHERE e.kind IN ('am','pm') AND e.state='committed' AND p.thread_ts IS NOT NULL AND p.status='active'
            AND (%s::uuid IS NULL OR e.id=%s::uuid) ORDER BY e.day DESC,e.committed_at DESC LIMIT 1""",
                            (edition_id, edition_id)).fetchone()

    def notice(self, conn, project, key, text):
        """One line from the briefing identity, never an owner mention (nothing in it asks for action)."""
        identity = stable("newsletter-notice:" + key)
        if not conn.execute("SELECT 1 FROM messages WHERE id=%s", (identity,)).fetchone():
            self.company._message(conn, project, None, "market_brief", "newsletter_status", text, message_id=identity)
        return identity

    def unsubscribe(self, address, mail_digest):
        """An unsubscribe mail from a recipient: the state the owner cannot undo with 이메일 추가. Returns True when it
        changed the recipient (and posted one masked line in the latest brief thread)."""
        with self.db.transaction() as conn:
            row = self.recipient(conn, address, lock=True)
            if not row:
                return None
            if conn.execute("SELECT 1 FROM newsletter_unsubscribes WHERE mail_digest=%s", (mail_digest,)).fetchone():
                return False
            changed = row["state"] != "unsubscribed"
            notice = None
            if changed:
                self.set_state(conn, row["id"], "unsubscribed")
                project = self.brief_thread(conn)
                if project:
                    notice = self.notice(conn, project, "unsubscribe:" + mail_digest,
                                         f"이메일 수신 거부 · {mask(address)} · 이후 브리핑 메일에서 제외했습니다.")
            conn.execute("INSERT INTO newsletter_unsubscribes(mail_digest,recipient_id,notice_message_id) VALUES(%s,%s,%s)",
                         (mail_digest, row["id"], notice))
            self.company._event(conn, "newsletter_unsubscribed", {"recipient": mask(address), "changed": changed})
            return changed

    # ---- Sends ---------------------------------------------------------------------------------------------

    def message_id(self, send_id):
        domain = self.settings.newsletter_from_address.partition("@")[2] or "newsletter.invalid"
        return f"<{send_id}@{domain}>"

    def add_test(self, conn, issue, address, project, event_key, at):
        identity = stable(f"newsletter-test:{event_key}")
        conn.execute("""INSERT INTO newsletter_sends(id,issue_id,address,kind,message_id,project_id,next_at)
            VALUES(%s,%s,%s,'test',%s,%s,%s) ON CONFLICT(id) DO NOTHING""",
                     (identity, issue["id"], address, self.message_id(identity), project["id"], at))
        return identity

    def recover(self, at=None):
        at = at or utcnow()
        with self.db.transaction() as conn:
            rows = conn.execute("""UPDATE newsletter_sends SET state='uncertain',error='send_interrupted',updated_at=now()
                WHERE state='sending' AND started_at<%s RETURNING id""", (at - STALE_SENDING,)).fetchall()
        for row in rows:
            LOG.warning("newsletter send %s left in progress is uncertain; it is not sent again", row["id"])
        return len(rows)

    def fan_out(self, at=None):
        """Ready automatic issues within their window: one regular row per active recipient, fixed at this moment."""
        at = at or utcnow()
        with self.db.transaction() as conn:
            issues = conn.execute("""SELECT * FROM newsletter_issues WHERE state='ready' AND auto AND expires_at>%s
                FOR UPDATE SKIP LOCKED""", (at,)).fetchall()
            for issue in issues:
                active = conn.execute("SELECT * FROM newsletter_recipients WHERE state='active' ORDER BY created_at")
                for recipient in active.fetchall():
                    identity = stable(f"newsletter:{issue['edition_id']}:{recipient['id']}")
                    conn.execute("""INSERT INTO newsletter_sends(id,issue_id,recipient_id,address,kind,message_id,next_at)
                        VALUES(%s,%s,%s,%s,'regular',%s,%s) ON CONFLICT DO NOTHING""",
                                 (identity, issue["id"], recipient["id"], recipient["address"], self.message_id(identity),
                                  at))
                conn.execute("UPDATE newsletter_issues SET state='sending',updated_at=now() WHERE id=%s", (issue["id"],))
            return len(issues)

    def due(self, at=None, limit=10):
        """Pending rows whose email exists: test rows always, regular rows only with the flag on and in the window."""
        at = at or utcnow()
        with self.db.transaction() as conn:
            conn.execute("""UPDATE newsletter_sends s SET state='skipped',error='send_window_closed',updated_at=now()
                FROM newsletter_issues i WHERE i.id=s.issue_id AND s.kind='regular' AND s.state='pending'
                AND i.expires_at<=%s""", (at,))
            return conn.execute("""SELECT s.*,i.draft,i.source,i.origin FROM newsletter_sends s
                JOIN newsletter_issues i ON i.id=s.issue_id WHERE s.state='pending' AND s.next_at<=%s
                AND i.draft IS NOT NULL AND (s.kind='test' OR %s) ORDER BY s.kind DESC,s.created_at LIMIT %s""",
                                (at, self.settings.newsletter_enabled, limit)).fetchall()

    def begin(self, row, at=None):
        """'sending' is committed before the SMTP session hears about this recipient. False: do not send."""
        at = at or utcnow()
        with self.db.transaction() as conn:
            current = conn.execute("SELECT state FROM newsletter_sends WHERE id=%s FOR UPDATE", (row["id"],)).fetchone()
            if not current or current["state"] != "pending":
                return False
            recipient = self.recipient(conn, row["address"])
            # A regular row needs a still-active recipient; a test row only an address that did not unsubscribe.
            state = recipient["state"] if recipient else "active"
            if state == "unsubscribed" or (row["kind"] == "regular" and state != "active"):
                conn.execute("""UPDATE newsletter_sends SET state='skipped',error=%s,updated_at=now() WHERE id=%s""",
                             ("recipient_" + state, row["id"]))
                return False
            conn.execute("""UPDATE newsletter_sends SET state='sending',attempts=attempts+1,started_at=%s,
                updated_at=now() WHERE id=%s""", (at, row["id"]))
            return True

    def finish(self, row, state, *, error=None, retry=None, receipt=None, at=None):
        """Settles a row this tick began. RETRY (seconds): the server never took the message, ask again later."""
        at = at or utcnow()
        with self.db.transaction() as conn:
            if retry is not None:
                conn.execute("""UPDATE newsletter_sends SET state='pending',error=%s,started_at=NULL,next_at=%s,
                    updated_at=now() WHERE id=%s AND state='sending'""", (error, at + timedelta(seconds=retry), row["id"]))
                return
            conn.execute("""UPDATE newsletter_sends SET state=%s,error=%s,receipt=%s,updated_at=now(),
                sent_at=CASE WHEN %s='sent' THEN %s ELSE sent_at END WHERE id=%s AND state='sending'""",
                         (state, error, Jsonb(receipt) if receipt else None, state, at, row["id"]))

    def retry_session(self, rows, code, attempts, at=None):
        """The session never opened (connection, TLS, login), so no row was handed over: rows still 'pending' wait
        five minutes, and fail after ATTEMPTS sessions."""
        at = at or utcnow()
        with self.db.transaction() as conn:
            conn.execute("""UPDATE newsletter_sends SET attempts=attempts+1,error=%s,updated_at=now(),next_at=%s,
                state=CASE WHEN attempts+1>=%s THEN 'failed' ELSE 'pending' END WHERE id=ANY(%s) AND state='pending'""",
                         (code, at + timedelta(minutes=5), attempts, [row["id"] for row in rows]))

    def fail_unconfigured(self):
        """Test rows whose email is ready while no sender is configured: nothing was sent, the thread is told."""
        with self.db.transaction() as conn:
            return len(conn.execute("""UPDATE newsletter_sends s SET state='failed',error='sender_not_configured',
                updated_at=now() FROM newsletter_issues i WHERE i.id=s.issue_id AND s.kind='test' AND s.state='pending'
                AND i.draft IS NOT NULL RETURNING s.id""").fetchall())

    # ---- Notices -------------------------------------------------------------------------------------------

    def notices(self, at=None):
        """Skipped editions, finished regular sends and finished test sends: one line each in their thread."""
        at = at or utcnow()
        posted = 0
        with self.db.transaction() as conn:
            for issue in conn.execute("""SELECT i.*,e.day,e.kind FROM newsletter_issues i
                JOIN brief_editions e ON e.id=i.edition_id
                WHERE i.notice_message_id IS NULL AND i.auto AND i.created_at>%s
                AND (i.state='skipped' OR (i.state='sending' AND NOT EXISTS (SELECT 1 FROM newsletter_sends s
                     WHERE s.issue_id=i.id AND s.kind='regular' AND s.state IN ('pending','sending'))))
                FOR UPDATE OF i SKIP LOCKED""", (at - timedelta(hours=24),)).fetchall():
                project = self.brief_thread(conn, issue["edition_id"])
                if not project:
                    continue
                if issue["state"] == "skipped":
                    name = KIND_NAMES[issue["kind"]]
                    why = SKIPPED.get(issue["reason"], NO_ISSUES.format(name=name))
                    key, text = f"skip:{issue['id']}", f"{name} 브리핑 메일 보내지 않음 · {issue['day']:%m/%d} · {why}."
                else:
                    key, text = f"result:{issue['id']}", self.result_text(conn, issue)
                mid = self.notice(conn, project, key, text)
                conn.execute("""UPDATE newsletter_issues SET notice_message_id=%s,
                    state=CASE WHEN state='sending' THEN 'sent' ELSE state END,updated_at=now() WHERE id=%s""",
                             (mid, issue["id"]))
                posted += 1
            for row in conn.execute("""SELECT s.*,e.day,e.kind AS edition_kind,i.origin FROM newsletter_sends s
                JOIN newsletter_issues i ON i.id=s.issue_id
                JOIN brief_editions e ON e.id=i.edition_id WHERE s.kind='test' AND s.state=ANY(%s)
                AND s.notice_message_id IS NULL FOR UPDATE OF s SKIP LOCKED""", (list(TERMINAL),)).fetchall():
                project = self.company._project(conn, row["project_id"])
                mid = self.notice_from_director(conn, project, f"test:{row['id']}", self.test_text(row))
                conn.execute("UPDATE newsletter_sends SET notice_message_id=%s WHERE id=%s", (mid, row["id"]))
                posted += 1
        return posted

    def unconfigured(self, at=None):
        """NEWSLETTER_ENABLED without a sender address or app password: one line per edition in its thread, once its
        email is ready (it still goes out if the sender is configured within the send window)."""
        at = at or utcnow()
        posted = 0
        with self.db.transaction() as conn:
            for issue in conn.execute("""SELECT i.id,i.edition_id,e.day,e.kind FROM newsletter_issues i
                JOIN brief_editions e ON e.id=i.edition_id WHERE i.auto AND i.state='ready' AND i.expires_at>%s""",
                                      (at,)).fetchall():
                project = self.brief_thread(conn, issue["edition_id"])
                if project and not conn.execute("SELECT 1 FROM messages WHERE id=%s",
                                                (stable(f"newsletter-notice:unconfigured:{issue['id']}"),)).fetchone():
                    self.notice(conn, project, f"unconfigured:{issue['id']}",
                                f"{KIND_NAMES[issue['kind']]} 브리핑 메일 보내지 못함 · {issue['day']:%m/%d} · 발신 Gmail "
                                "주소나 앱 비밀번호가 아직 설정되지 않았습니다(docs/newsletter.md). 설정되면 커밋 후 12시간 "
                                "안에는 이어서 보냅니다.")
                    posted += 1
        return posted

    def notice_from_director(self, conn, project, key, text):
        identity = stable("newsletter-notice:" + key)
        if not conn.execute("SELECT 1 FROM messages WHERE id=%s", (identity,)).fetchone():
            self.company._message(conn, project, None, "director", "status", text, message_id=identity)
        return identity

    @staticmethod
    def result_text(conn, issue):
        counts = {row["state"]: row["n"] for row in conn.execute("""SELECT state,count(*) AS n FROM newsletter_sends
            WHERE issue_id=%s AND kind='regular' GROUP BY state""", (issue["id"],)).fetchall()}
        head = f"{KIND_NAMES[issue['kind']]} 브리핑 메일 · {issue['day']:%m/%d}"
        if not counts:
            return f"{head} · 수신 중인 주소가 없어 보내지 않았습니다."
        parts = [f"{counts.get('sent', 0)}명 발송"]
        if counts.get("failed"):
            parts.append(f"실패 {counts['failed']}명")
        if counts.get("uncertain"):
            parts.append(f"결과 불확실 {counts['uncertain']}명(자동으로 다시 보내지 않음)")
        if counts.get("skipped"):
            parts.append(f"제외 {counts['skipped']}명")
        origin = "" if issue["origin"] != "fallback" else (
            " · 작성본이 검사를 통과하지 못해 브리핑 문장으로 만든 대체본을 보냈습니다" if issue["reason"] == "model_checks_failed"
            else " · 정해진 시간 안에 작성본이 준비되지 않아 브리핑 문장으로 만든 대체본을 보냈습니다")
        return f"{head} · " + " · ".join(parts) + origin + "."

    @staticmethod
    def test_text(row):
        what = f"{row['day']:%m/%d} {KIND_NAMES[row['edition_kind']]} 브리핑 메일" + (
            " (대체본)" if row["origin"] == "fallback" else "")
        if row["state"] == "sent":
            return f"이메일 시험 발송 완료 · {mask(row['address'])} · {what}"
        reasons = {"sender_not_configured": "발신 Gmail 주소나 앱 비밀번호가 아직 설정되지 않았습니다(docs/newsletter.md)",
                   "recipient_unsubscribed": "본인이 수신 거부한 주소입니다",
                   "send_interrupted": "보내는 중 연결이 끊겨 도착 여부를 알 수 없습니다. 자동으로 다시 보내지 않습니다"}
        reason = reasons.get(row["error"] or "", f"메일 서버가 받지 않았습니다({row['error']})")
        label = {"uncertain": "결과 불확실", "skipped": "보내지 않음"}.get(row["state"], "실패")
        return f"이메일 시험 발송 {label} · {mask(row['address'])} · {what} · {reason}"
