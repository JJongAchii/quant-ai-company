"""Signal slots in PostgreSQL: one row per KRX session and kind, the frozen snapshot source, the Analyst task, the
interim post and the outbox gate.

Every time decision takes the caller's `at` from briefing.schedule.utcnow() (the one signals clock) and stores it. The
turn hooks and the outbox gate below run inside Company's and SlackOutbox's transactions and read that clock themselves.
Lock order is always the thread's project, then the slot row (R8)."""

import json
import logging
from datetime import UTC, date, datetime, time, timedelta

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from ..briefing import schedule
from ..briefing.contracts import BRIEFER
from ..briefing.editor import artifact
from ..briefing.schedule import KST, overrides
from ..briefing.store import BriefStore
from ..company import PolicyError, as_json, stable
from .contracts import (
    FALLBACK_DIRECTION,
    FAULT_RETRY_MINUTES,
    MAX_FAULT_RETRIES,
    MAX_FORMAT_RETRIES,
    MAX_READS,
    MAX_SEARCHES,
    MAX_TOOL_TURNS,
    SIGNAL_KIND,
    SignalProposal,
    Slot,
)
from .grading import INSTRUMENTS, analyst_counts, summarize
from .instructions import final_note, instruction
from .render import (
    KIND_WORDS,
    analyst_post,
    day_text,
    fallback_post,
    result_line,
    scorecard_post,
    unparsed_post,
)
from .schedule import kst_day, slots_for
from .snapshot import follow_us, snapshot_text

LOG = logging.getLogger(__name__)
TIMES = ("snapshot_at", "snapshot_latest", "tools_until", "fallback_at", "send_at", "entry_at", "exit_at")
PRIORITY = -100
# The trade started before the interim post could go out (the loop was down): recorded, never posted (R3).
OUTAGE = "서비스 중단"
# The interim post's reason after an unparsed answer, which no Analyst signal follows (R13).
UNPARSED_REASON = "형식 확인 실패"
# The scorecard goes out at 18:30 KST at the earliest, and at 21:00 whatever is still pending.
REPORT_FROM, REPORT_LATEST = time(18, 30), time(21, 0)
STANDALONE_INSTRUCTION = "이 스레드의 매매 신호와 후속 질문에 Analyst가 답한다."
# The re-ask reason for any answer briefing.editor.artifact rejects before reading its JSON.
ANSWER_SHAPE = "answer_shape: status complete with exactly one artifact, its source_ids empty, and nothing else"
# The re-ask reason for a tool round that also carries other effects.
TOOLS_ONLY = "tools_only: a tool round has no messages, delegations, memories, follow_up or artifacts"
# The day's AM edition counts once its parent post is delivered: only then is there a thread to reply in.
AM_EDITION = """SELECT p.id FROM brief_editions b JOIN projects p ON p.id=b.project_id
    WHERE b.day=%s AND b.kind='am' AND b.state='committed' AND b.channel=%s AND b.owner_user=%s
    AND p.thread_ts IS NOT NULL AND p.status='active'"""


def slot_id(day: date, kind: str) -> str:
    return stable(f"trading-signal:{day}:{kind}")


def slot_task_id(day: date, kind: str, revision: int) -> str:
    """The slot ID at project revision 1; a revised project gets a new task ID per revision."""
    return slot_id(day, kind) if revision == 1 else stable(f"trading-signal:{day}:{kind}:r{revision}")


def snapshot_source_id(day: date, kind: str) -> str:
    return f"signal:{day}:{kind}:snapshot"


def slot_of(row: dict) -> Slot:
    return Slot(day=row["session_day"], kind=row["kind"], exit_day=row["exit_day"],
                **{name: row[name].astimezone(UTC) for name in TIMES})


class SignalStore:
    def __init__(self, company):
        self.company, self.db = company, company.db

    def authorized(self):
        # Signals post where the briefing posts, so they run only while the briefing itself may.
        return bool(self.company.settings.analyst_signals_enabled and BriefStore(self.company).authorized())

    def schedule(self, at: datetime) -> int:
        s = self.company.settings
        changes, first = overrides(s), kst_day(at)
        slots = [slot for day in (first, first + timedelta(days=1)) for slot in slots_for(day, changes)]
        inserted = 0
        with self.db.transaction() as conn:
            for slot in slots:
                inserted += conn.execute("""INSERT INTO trading_signals(id,session_day,kind,channel,owner_user,
                    snapshot_at,snapshot_latest,tools_until,fallback_at,send_at,entry_at,exit_at,exit_day)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                                         (slot_id(slot.day, slot.kind), slot.day, slot.kind, s.briefing_channel_id,
                                          s.briefing_owner_user, *(getattr(slot, name) for name in TIMES),
                                          slot.exit_day)).rowcount
        return inserted

    def due_asks(self, at: datetime) -> list[dict]:
        """Slots to snapshot and ask now. The intraday snapshot waits for the AM edition until snapshot_latest; a
        slot whose trade has started (after an outage) is not asked. A slot whose interim post went out before it
        could be asked is still asked, so the late signal that post promises can follow."""
        with self.db.transaction() as conn:
            rows = conn.execute("""SELECT * FROM trading_signals WHERE (state='scheduled' OR state='fallback_sent'
                AND task_id IS NULL AND fallback_message_id IS NOT NULL) AND snapshot_at<=%s AND entry_at>%s
                ORDER BY snapshot_at,kind""", (at, at)).fetchall()
            return [row for row in rows if row["kind"] != "intraday" or at >= row["snapshot_latest"]
                    or self._am_edition(conn, row["session_day"])]

    def grading_due(self, at: datetime) -> list[dict]:
        """Slots whose trade has closed and that still lack a final or excluded return for either instrument, from
        18:30 KST of the grading day (session_day intraday, exit_day overnight). A slot with a pending return from the
        last 30 minutes waits for the next attempt. A slot that never got a snapshot is not graded."""
        with self.db.transaction() as conn:
            return conn.execute("""SELECT s.* FROM trading_signals s WHERE s.exit_at<%s AND s.baselines IS NOT NULL
                AND (((CASE WHEN s.kind='intraday' THEN s.session_day ELSE s.exit_day END)+time '18:30')
                     AT TIME ZONE 'Asia/Seoul')<=%s
                AND (SELECT count(*) FROM trading_signal_returns r WHERE r.signal_id=s.id
                     AND r.state IN ('final','excluded'))<2
                AND NOT EXISTS (SELECT 1 FROM trading_signal_returns r WHERE r.signal_id=s.id AND r.state='pending'
                                AND r.updated_at>%s)
                ORDER BY s.session_day,s.kind""", (at, at, at - timedelta(minutes=30))).fetchall()

    def save_returns(self, returns: list[dict]) -> int:
        """Upsert one row per (signal, instrument). A final or excluded row is never overwritten; a pending row may
        be replaced by a later pending or final one. Returns the rows written."""
        at, written = schedule.utcnow(), 0
        with self.db.transaction() as conn:
            for r in returns:
                written += conn.execute("""INSERT INTO trading_signal_returns(signal_id,instrument,state,entry_price,
                    exit_price,long_bp,detail,receipt,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(signal_id,instrument) DO UPDATE SET state=EXCLUDED.state,
                    entry_price=EXCLUDED.entry_price,exit_price=EXCLUDED.exit_price,long_bp=EXCLUDED.long_bp,
                    detail=EXCLUDED.detail,receipt=EXCLUDED.receipt,updated_at=EXCLUDED.updated_at
                    WHERE trading_signal_returns.state='pending'""",
                                         (r["signal_id"], r["instrument"], r["state"], r.get("entry_price"),
                                          r.get("exit_price"), r.get("long_bp"), r.get("detail"),
                                          Jsonb(r["receipt"]) if r.get("receipt") is not None else None, at)).rowcount
        return written

    def history(self, at: datetime) -> list[dict]:
        with self.db.transaction() as conn:
            return self._history(conn, at)

    def _history(self, conn, at: datetime) -> list[dict]:
        """Every slot since the first that was asked (it has baselines) and has exited, oldest first, as summarize()
        reads it, plus its stored returns. A returned instrument that was never graded counts as pending. R17: an
        interim post is counted by its message id; 'received' is the direction of the last delivered post before
        entry_at, by Slack sent_ts or, without one, the dispatch start."""
        slots = conn.execute("""SELECT * FROM trading_signals WHERE baselines IS NOT NULL AND exit_at<=%s
            ORDER BY session_day,kind""", (at,)).fetchall()
        stored: dict = {}
        for r in conn.execute("SELECT * FROM trading_signal_returns WHERE signal_id=ANY(%s::uuid[])",
                              ([str(s["id"]) for s in slots],)):
            stored.setdefault(str(r["signal_id"]), {})[r["instrument"]] = r
        messages = [str(m) for s in slots for m in (s["analyst_message_id"], s["fallback_message_id"]) if m]
        delivered = {str(r["id"]): r for r in conn.execute(
            "SELECT id,sent_ts,started_at FROM outbox WHERE status='delivered' AND id=ANY(%s::uuid[])", (messages,))}
        for slot in slots:
            posts = []
            for part, message in (("fallback", slot["fallback_message_id"]), ("analyst", slot["analyst_message_id"])):
                row = delivered.get(str(message))
                if row and (moment := _delivery_time(row)) is not None and moment < slot["entry_at"]:
                    posts.append((moment, part == "analyst", part))
            last = max(posts)[2] if posts else None
            slot["analyst"] = (slot["proposal"] or {}).get("direction")
            slot["received"] = (slot["analyst"] if last == "analyst" else FALLBACK_DIRECTION if last else None)
            slot["fallback"] = slot["fallback_message_id"] is not None
            slot["returns"] = {i: stored.get(str(slot["id"]), {}).get(i, {"state": "pending", "long_bp": None,
                                                                          "detail": None, "reported_on": None})
                               for i in INSTRUMENTS}
        return slots

    def report(self, at: datetime) -> bool:
        """The evening scorecard, once per KST day from 18:30: when every return due today is final or excluded, else
        at 21:00 with what is pending marked. It goes into the day's thread (today's intraday slot's project, else
        the overnight slot's) and the report row is written in the same transaction, so the day's primary key makes
        it once. Returns whether a post was made."""
        day = kst_day(at)
        if at < datetime.combine(day, REPORT_FROM, KST):
            return False
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM trading_signal_reports WHERE day=%s", (day,)).fetchone():
                return False
            plan = self._report_plan(conn, day, at)
            if plan is None:
                return False
            project = self.company._project(conn, plan["project_id"])  # R8: the project before anything else
            if conn.execute("SELECT 1 FROM trading_signal_reports WHERE day=%s", (day,)).fetchone():
                return False
            plan = self._report_plan(conn, day, at)  # again under the lock
            if plan is None or plan["project_id"] != project["id"]:
                return False
            message_id = stable(f"trading-signal-report:{day}")
            self.company._message(conn, project, None, BRIEFER, SIGNAL_KIND,
                                  scorecard_post(day, plan["lines"], plan["summary"]), message_id=message_id)
            conn.execute("INSERT INTO trading_signal_reports(day,project_id,message_id,summary) VALUES (%s,%s,%s,%s)",
                         (day, project["id"], message_id, Jsonb(as_json(plan["summary"]))))
            for signal_id, instrument in plan["shown"]:
                conn.execute("UPDATE trading_signal_returns SET reported_on=%s WHERE signal_id=%s AND instrument=%s",
                             (day, signal_id, instrument))
            self.company._event(conn, "trading_signal_report", {"day": str(day), "message_id": message_id},
                                project["id"])
        return True

    def _report_plan(self, conn, day: date, at: datetime) -> dict | None:
        """What the scorecard of day says, or None when there is nothing to post yet (or at all)."""
        history = self._history(conn, at)
        today = [s for s in history if (s["kind"] == "intraday" and s["session_day"] == day)
                 or (s["kind"] == "overnight" and s["exit_day"] == day)]
        past = []
        for slot in history:
            fresh = tuple(i for i in INSTRUMENTS if slot["returns"][i]["state"] in ("final", "excluded")
                          and slot["returns"][i]["reported_on"] is None)
            if slot not in today and fresh:
                past.append((slot, fresh))
        if not today and not past:
            return None
        if at < datetime.combine(day, REPORT_LATEST, KST) and any(
                r["state"] == "pending" for s in today for r in s["returns"].values()):
            return None
        lines, shown = [], []
        for slot in today:
            lines.append(_slot_line(slot, INSTRUMENTS, show_date=False))
            shown += [(slot["id"], i) for i in INSTRUMENTS if slot["returns"][i]["state"] != "pending"]
        if past:
            lines.append("지난 보류분")
        for slot, fresh in past:
            lines.append(_slot_line(slot, fresh, show_date=True))
            shown += [(slot["id"], i) for i in fresh]
        known = conn.execute("""SELECT project_id FROM trading_signals WHERE project_id IS NOT NULL AND
            (session_day=%s OR kind='overnight' AND exit_day=%s) ORDER BY (session_day=%s) DESC,kind LIMIT 1""",
                             (day, day, day)).fetchone()
        project_id = (known or {}).get("project_id") or next(
            (s["project_id"] for s in [*today, *(p[0] for p in reversed(past))] if s["project_id"]), None)
        if project_id is None:
            return None
        return {"project_id": project_id, "lines": lines, "shown": shown, "summary": summarize(history)}

    def _am_edition(self, conn, day: date) -> dict | None:
        s = self.company.settings
        return conn.execute(AM_EDITION, (day, s.briefing_channel_id, s.briefing_owner_user)).fetchone()

    def _thread_project(self, conn, row: dict) -> dict:
        """The day's one signal thread, locked: the project a slot of the day already has (this slot's first), else
        the delivered AM edition's, else the day's standalone project."""
        day = row["session_day"]
        known = conn.execute("""SELECT project_id FROM trading_signals WHERE session_day=%s
            AND project_id IS NOT NULL ORDER BY id=%s DESC LIMIT 1""", (day, row["id"])).fetchone()
        if known:
            return self.company._project(conn, known["project_id"])
        edition = self._am_edition(conn, day)
        if edition:
            return self.company._project(conn, edition["id"])
        s = self.company.settings
        identity = stable(f"signal-project:{day}")
        # The outbox fills thread_ts from the first delivered post.
        conn.execute("""INSERT INTO projects(id,title,instruction,owner_user,channel) VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING""", (identity, f"{day.month}월 {day.day}일 매매 신호", STANDALONE_INSTRUCTION,
                                         s.briefing_owner_user, s.briefing_channel_id))
        return self.company._project(conn, identity)

    def ask(self, row: dict, snapshot: dict, at: datetime) -> bool:
        """Freeze the snapshot as an approved source and open the Analyst's task in one transaction. A slot asked
        meanwhile (before a restart, or by another worker) is left as it is. An interim post sent before the ask keeps
        the slot's fallback_sent state, so the answer counts as late."""
        with self.db.transaction() as conn:
            current = conn.execute("SELECT * FROM trading_signals WHERE id=%s", (row["id"],)).fetchone()
            if not _askable(current):
                return False
            # R15: find or make the thread and lock it, then lock the slot and look again.
            project = self._thread_project(conn, current)
            current = conn.execute("SELECT * FROM trading_signals WHERE id=%s FOR UPDATE", (row["id"],)).fetchone()
            if not _askable(current) or current["project_id"] not in (None, project["id"]):
                return False
            slot = slot_of(current)
            identity, text = snapshot_source_id(slot.day, slot.kind), snapshot_text(snapshot)
            conn.execute("""INSERT INTO sources(id,title,uri,content,available_at,approved,project_id,synthetic,metadata)
                VALUES (%s,%s,%s,%s,%s,true,%s,%s,%s) ON CONFLICT DO NOTHING""",
                         (identity, f"{day_text(slot.day)} {KIND_WORDS[slot.kind]} 신호 기준 시세",
                          f"company://signals/{slot.day}/{slot.kind}/snapshot", text,
                          datetime.fromisoformat(snapshot["observed_at"]), project["id"],
                          self.company.settings.fixture_mode,
                          Jsonb(as_json({"observed_at": snapshot["observed_at"], "receipts": snapshot["receipts"]}))))
            task = self.company._new_task(conn, project, BRIEFER, instruction(slot, identity, text),
                                          task_id=slot_task_id(slot.day, slot.kind, project["revision"]),
                                          priority=PRIORITY, kind=SIGNAL_KIND)
            conn.execute("""UPDATE trading_signals SET state=CASE WHEN state='scheduled' THEN 'asked' ELSE state END,
                project_id=%s,task_id=%s,snapshot=%s,snapshot_source_id=%s,baselines=%s,updated_at=%s WHERE id=%s""",
                         (project["id"], task["id"], Jsonb(as_json(snapshot)), identity,
                          Jsonb({"always_long": "long", "follow_us": follow_us(snapshot)}), at, current["id"]))
            self.company._event(conn, "trading_signal_asked", {"slot_id": current["id"], "task_id": task["id"],
                                                               "source_id": identity}, project["id"])
        return True

    def send_fallbacks(self, at: datetime) -> int:
        """The interim 'always long' post of every slot the Analyst has not settled by its fallback_at, two minutes
        before the send time. Each slot has its own transaction, so one failing slot never holds back another (R10).
        Returns the posts made."""
        with self.db.transaction() as conn:
            due = conn.execute("""SELECT id FROM trading_signals WHERE state IN ('scheduled','asked')
                AND fallback_at<=%s ORDER BY fallback_at,kind""", (at,)).fetchall()
        sent = 0
        for row in due:
            try:
                sent += self._send_fallback(row["id"], at)
            except Exception as exc:  # noqa: BLE001 - the next slot's interim post must still go out
                LOG.error("Signal interim post failed for %s: %s", row["id"], type(exc).__name__)
        return sent

    def _send_fallback(self, identity, at: datetime) -> int:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM trading_signals WHERE id=%s", (identity,)).fetchone()
            if not _fallback_due(row, at):
                return 0
            if at >= row["entry_at"]:
                return self._outage(conn, row, at)
            # R15: find or make the thread and lock it, then lock the slot and look again.
            project = self._thread_project(conn, row)
            row = conn.execute("SELECT * FROM trading_signals WHERE id=%s FOR UPDATE", (identity,)).fetchone()
            if not _fallback_due(row, at) or row["project_id"] not in (None, project["id"]):
                return 0  # settled meanwhile, or asked into another thread: the next tick looks again
            reason = UNPARSED_REASON if row["unparsed"] else _late_reason(conn, row)
            message_id = stable(f"trading-signal-post:{row['id']}:fallback")
            self.company._message(conn, project, row["task_id"], BRIEFER, SIGNAL_KIND,
                                  fallback_post(slot_of(row), reason, unparsed=row["unparsed"]), message_id=message_id)
            conn.execute("""UPDATE trading_signals SET state='fallback_sent',fallback_reason=%s,fallback_sent_at=%s,
                fallback_message_id=%s,project_id=%s,updated_at=%s WHERE id=%s""",
                         (reason, at, message_id, project["id"], at, row["id"]))
            self.company._event(conn, "trading_signal_fallback", {"slot_id": row["id"], "reason": reason,
                                                                  "message_id": message_id}, project["id"])
        return 1

    def _outage(self, conn, row: dict, at: datetime) -> int:
        """The trade has started and no interim post went out (the loop was down): an "enter at the open" post is
        useless now, so the slot records the reason and nothing is posted (R3)."""
        if row["project_id"]:
            self.company._project(conn, row["project_id"])  # R8: the project before the slot
        row = conn.execute("SELECT * FROM trading_signals WHERE id=%s FOR UPDATE", (row["id"],)).fetchone()
        if not _fallback_due(row, at):
            return 0
        conn.execute("UPDATE trading_signals SET state='fallback_sent',fallback_reason=%s,updated_at=%s WHERE id=%s",
                     (OUTAGE, at, row["id"]))
        self.company._event(conn, "trading_signal_fallback", {"slot_id": row["id"], "reason": OUTAGE,
                                                              "message_id": None}, row["project_id"])
        return 0

    def retry(self, at: datetime) -> int:
        """Ask the Analyst again where a fault or a thread revision stopped an unsettled signal:
        - a blocked task gets a new turn from its retry_at (5 minutes after the fault) until the exit, at most twice;
        - a task a "수정:" revision made stale is recreated at the new revision, until the entry.
        Each slot has its own transaction (R10). Returns the tasks resumed."""
        with self.db.transaction() as conn:
            due = conn.execute("""SELECT s.id FROM trading_signals s JOIN tasks k ON k.id=s.task_id
                JOIN projects p ON p.id=s.project_id WHERE s.state IN ('asked','fallback_sent') AND NOT s.unparsed
                AND (k.revision<>p.revision AND s.entry_at>%s OR k.status='blocked' AND k.revision=p.revision
                     AND s.retry_at<=%s AND s.exit_at>%s AND s.fault_retries<%s)
                ORDER BY s.fallback_at,s.kind""", (at, at, at, MAX_FAULT_RETRIES)).fetchall()
        resumed = 0
        for row in due:
            try:
                resumed += self._retry(row["id"], at)
            except Exception as exc:  # noqa: BLE001 - one slot's failure never holds back another
                LOG.error("Signal retry failed for %s: %s", row["id"], type(exc).__name__)
        return resumed

    def _retry(self, identity, at: datetime) -> int:
        with self.db.transaction() as conn:
            known = conn.execute("SELECT project_id FROM trading_signals WHERE id=%s", (identity,)).fetchone()
            project = self.company._project(conn, known["project_id"])  # R8: the project before the slot
            row = conn.execute("SELECT * FROM trading_signals WHERE id=%s FOR UPDATE", (identity,)).fetchone()
            if row["state"] not in {"asked", "fallback_sent"} or row["unparsed"] or row["project_id"] != project["id"]:
                return 0
            task = conn.execute("SELECT * FROM tasks WHERE id=%s", (row["task_id"],)).fetchone()
            if task["revision"] != project["revision"]:
                return self._recreate(conn, project, row, task, at)
            due = row["retry_at"] is not None and row["retry_at"] <= at < row["exit_at"]
            if task["status"] != "blocked" or not due or row["fault_retries"] >= MAX_FAULT_RETRIES:
                return 0
            if task["turn_count"] >= self.company.settings.company_max_task_turns:
                # Another turn would pass the task's turn limit: stop retrying; the interim post stands.
                conn.execute("UPDATE trading_signals SET retry_at=NULL,updated_at=%s WHERE id=%s", (at, identity))
                return 0
            turn_id = self.company._new_turn(conn, task)
            conn.execute("""UPDATE trading_signals SET fault_retries=fault_retries+1,retry_at=%s,updated_at=%s
                WHERE id=%s""", (at + timedelta(minutes=FAULT_RETRY_MINUTES), at, identity))
            self.company._event(conn, "trading_signal_retried", {
                "slot_id": identity, "task_id": task["id"], "turn_id": turn_id, "error": task["error"],
                "fault_retries": row["fault_retries"] + 1}, project["id"])
        return 1

    def _recreate(self, conn, project: dict, row: dict, task: dict, at: datetime) -> int:
        """A "수정:" revision superseded the slot's task: the same instruction becomes a task of the new revision, even
        after the send time, as long as the trade has not started (spec §3)."""
        if at >= row["entry_at"]:
            return 0
        fresh = self.company._new_task(conn, project, task["agent"], task["instruction"],
                                       task_id=slot_task_id(row["session_day"], row["kind"], project["revision"]),
                                       priority=PRIORITY, kind=SIGNAL_KIND)
        conn.execute("UPDATE trading_signals SET task_id=%s,updated_at=%s WHERE id=%s", (fresh["id"], at, row["id"]))
        self.company._event(conn, "trading_signal_reasked", {"slot_id": row["id"], "task_id": fresh["id"],
                                                             "revision": project["revision"]}, project["id"])
        return 1

    def gate(self, conn, row: dict) -> bool:
        """SlackOutbox._claim_one's check for a pending trading_signal post (spec §8). Unlike other posts, a signal
        post ignores routing holds, paused projects and thread revisions; instead:
        - a slot's post goes out only before the slot's exit, else it becomes stale;
        - the Analyst's post waits while the slot's interim post is pending or sending, so the interim goes first;
        - a post with no thread takes the project's thread_ts once there is one. While another top-level post of the
          project is in flight it waits, since that post may start the thread; otherwise it starts the thread itself.
        A post of no slot (the evening scorecard) always passes."""
        slot = conn.execute("""SELECT exit_at,analyst_message_id,fallback_message_id FROM trading_signals
            WHERE %s IN (analyst_message_id,fallback_message_id,unparsed_message_id)""", (row["id"],)).fetchone()
        if not slot:
            return True
        if schedule.utcnow() >= slot["exit_at"]:
            conn.execute("UPDATE outbox SET status='stale',error='signal_after_exit' WHERE id=%s", (row["id"],))
            return False
        if (str(slot["analyst_message_id"]) == str(row["id"]) and slot["fallback_message_id"]
                and conn.execute("SELECT 1 FROM outbox WHERE id=%s AND status IN ('pending','sending')",
                                 (slot["fallback_message_id"],)).fetchone()):
            return _defer(conn, row, "awaiting_interim_post")
        if row["thread_ts"] is None:
            # The project lock makes concurrent senders agree on one root post.
            thread = conn.execute("SELECT thread_ts FROM projects WHERE id=%s FOR UPDATE",
                                  (row["project_id"],)).fetchone()["thread_ts"]
            if thread:
                conn.execute("UPDATE outbox SET thread_ts=%s WHERE id=%s", (thread, row["id"]))
                row["thread_ts"] = thread
            elif conn.execute("""SELECT 1 FROM outbox WHERE project_id=%s AND id<>%s AND status='sending'
                    AND thread_ts IS NULL""", (row["project_id"], row["id"])).fetchone():
                return _defer(conn, row, "awaiting_thread_root")
        return True


def _askable(row: dict | None) -> bool:
    """Not asked yet: scheduled, or its interim post went out before the service could ask (after an outage)."""
    return bool(row) and (row["state"] == "scheduled" or (row["state"] == "fallback_sent" and row["task_id"] is None
                                                          and row["fallback_message_id"] is not None))


def _fallback_due(row: dict | None, at: datetime) -> bool:
    return bool(row) and row["state"] in {"scheduled", "asked"} and row["fallback_at"] <= at


def _late_reason(conn, row: dict) -> str:
    """Why the Analyst's signal is not in yet, from its task and that task's latest turn (spec §3)."""
    task = row["task_id"] and conn.execute("SELECT status,error FROM tasks WHERE id=%s", (row["task_id"],)).fetchone()
    if not task:
        return "준비 중 오류"
    turn = conn.execute("SELECT status,error FROM turns WHERE task_id=%s ORDER BY sequence DESC LIMIT 1",
                        (row["task_id"],)).fetchone() or {}
    if task["status"] == "blocked":
        return "모델 로그인 만료" if "auth" in {task["error"], turn.get("error")} else "모델 응답 오류"
    if turn.get("status") == "waiting":  # a provider fault deferred the turn; its error is the fault code
        return "모델 사용 한도" if turn["error"] == "quota" else "모델 혼잡"
    if turn.get("status") == "queued" and conn.execute(
            "SELECT 1 FROM turns WHERE status='running' AND task_id<>%s LIMIT 1", (row["task_id"],)).fetchone():
        return "앞선 업무가 끝나기를 기다리는 중"
    return "분석이 아직 끝나지 않음"


def _defer(conn, row: dict, reason: str) -> bool:
    """Leave the post pending and look again in 5 seconds, on the outbox's own clock (DB now())."""
    conn.execute("UPDATE outbox SET status='pending',next_at=now()+interval '5 seconds',error=%s WHERE id=%s",
                 (reason, row["id"]))
    return False


def _tool_budget(conn, task: dict) -> tuple[bool, int, int]:
    """Whether the signal task may still use tools now, and its web searches and reads so far."""
    slot = conn.execute("SELECT tools_until,format_failures FROM trading_signals WHERE task_id=%s",
                        (task["id"],)).fetchone()
    tool_turns = conn.execute("""SELECT count(*) AS n FROM turns WHERE task_id=%s AND status='completed'
        AND jsonb_array_length(response->'decision'->'tools')>0""", (task["id"],)).fetchone()["n"]
    # project_id lets the message_context index narrow the scan to the signal's thread.
    rows = conn.execute("""SELECT author,count(*) AS n FROM messages WHERE project_id=%s AND task_id=%s
        AND author IN ('tool:web_search','tool:web_read') GROUP BY author""", (task["project_id"], task["id"]))
    calls = {row["author"]: row["n"] for row in rows}
    usable = (bool(slot) and schedule.utcnow() < slot["tools_until"] and tool_turns < MAX_TOOL_TURNS
              and not slot["format_failures"])
    return usable, calls.get("tool:web_search", 0), calls.get("tool:web_read", 0)


def allowed_tools(conn, task: dict, tools: list[str]) -> list[str]:
    """The tools a signal turn may still use: none from tools_until, after three tool turns or once an answer failed the
    format check; web_search twice and web_read four times per signal."""
    usable, searches, reads = _tool_budget(conn, task)
    spent = {"web_search": searches >= MAX_SEARCHES, "web_read": reads >= MAX_READS}
    return [name for name in tools if usable and not spent.get(name)]


def _round_problem(conn, task: dict, decision, tools: list[str]) -> str | None:
    """Why this signal round may not run its tools (the re-ask reason), or None."""
    if (decision.status != "continue" or not decision.tools or decision.artifacts or decision.messages
            or decision.delegations or decision.memories or decision.follow_up):
        return TOOLS_ONLY
    usable, searches, reads = _tool_budget(conn, task)
    if not usable:
        return "tools_not_allowed"
    names = [action.name for action in decision.tools]
    over = {"web_search": searches + names.count("web_search") > MAX_SEARCHES,
            "web_read": reads + names.count("web_read") > MAX_READS}
    refused = [name for name in dict.fromkeys(names) if name not in tools or over.get(name)]
    return f"tools_not_allowed: {', '.join(refused)}" if refused else None


def tools_allowed(conn, task: dict, decision, tools: list[str]) -> bool:
    """A round the signal task may run: tools only, each in allowed_tools(conn, task, tools) for the role's tools, with
    the web searches and reads within their limits after this turn."""
    return _round_problem(conn, task, decision, tools) is None


def _fetch_argument_error(decision) -> str | None:
    """What web_tools.prepare or finance_sources.prefetch would reject before fetching, if anything."""
    from ..finance_sources import requested_url
    from ..web_tools import WEB_TOOLS, arguments_for

    if sum(action.name == "finance_read" for action in decision.tools) > 1:
        return "At most one official document fetch per turn"
    try:
        for action in decision.tools:
            if action.name in WEB_TOOLS:
                arguments_for(action)
            elif action.name == "finance_read":
                requested_url(action.arguments)
    except ValueError as exc:
        return str(exc)
    return None


def skip_prefetch(company, turn_id, decision) -> bool:
    """For the steps that fetch before the commit (web_tools.prepare, finance_sources.prefetch): True for a signal turn
    whose tool round is refused or whose fetch arguments are malformed. Those steps then fetch and raise nothing, and
    the commit asks for the final answer without tools. False for every other task."""
    with company.db.transaction() as conn:
        task = conn.execute("SELECT k.* FROM tasks k JOIN turns t ON t.task_id=k.id WHERE t.id=%s",
                            (turn_id,)).fetchone()
        if not task or task["kind"] != SIGNAL_KIND:
            return False
        if _round_problem(conn, task, decision, company.role(task["agent"]).tools):
            return True
    return _fetch_argument_error(decision) is not None


def parse_proposal(response, slot: dict, approved_ids: set[str], snapshot_id: str) -> SignalProposal:
    """The Analyst's final answer as this slot's typed proposal. Pure: approved_ids are the sources the thread may cite.
    Raises ValueError with a short reason, which the re-ask sends back to the Analyst."""
    if response.decision.tools:
        raise ValueError("tools_not_allowed")
    try:
        proposal = artifact(response, SignalProposal)
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_url=False)[:3]
        detail = "; ".join(f"{'.'.join(map(str, error['loc'])) or 'proposal'}: {error['msg']}" for error in errors)
        raise ValueError(f"invalid_proposal: {detail}"[:300]) from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid_json: {exc.msg}") from None
    except ValueError:
        raise ValueError(ANSWER_SHAPE) from None
    if (proposal.kind, proposal.session_day) != (slot["kind"], slot["session_day"]):
        raise ValueError(f"wrong_slot: expected {slot['kind']} {slot['session_day']}")
    cited = [*proposal.source_ids, *(source for reason in proposal.reasons for source in reason.source_ids)]
    unapproved = [source for source in dict.fromkeys(cited) if source not in approved_ids]
    if unapproved:
        raise ValueError(f"unapproved_sources: {', '.join(unapproved)}"[:300])
    if snapshot_id not in proposal.source_ids:
        raise ValueError(f"snapshot_not_cited: {snapshot_id}")
    return proposal


def commit_turn(company, conn, project, task, turn, response, decision) -> dict | None:
    """The signal step of Company.commit_turn, under its project lock. None hands an allowed tool round back to the
    general path, which applies its tools in a savepoint (see tool_error). Anything else is the Analyst's answer: a
    valid proposal is stored and posted once; a refused tool round or a format error re-asks without tools at most
    twice, then the say is posted as unparsed. A model mistake never raises."""
    slot = _locked_slot(conn, task)
    if slot["state"] in {"committed", "committed_late"} or slot["unparsed"]:
        # Another task of this slot settled it already; the slot keeps that one outcome and post.
        return _finish(company, conn, project, task, turn, response, done=True)
    problem = None
    if decision.tools:
        problem = _round_problem(conn, task, decision, company.role(task["agent"]).tools)
        if problem is None:
            return None
    at = schedule.utcnow()
    if problem:
        return _format_error(company, conn, project, task, turn, response, slot, problem, at)
    approved = {row["id"] for row in conn.execute("""SELECT id FROM sources WHERE approved AND available_at<=now()
        AND (project_id IS NULL OR project_id=%s)""", (project["id"],))}
    try:
        proposal = parse_proposal(response, slot, approved, slot["snapshot_source_id"])
    except ValueError as exc:
        return _format_error(company, conn, project, task, turn, response, slot, str(exc), at)
    # On time only while no interim post went out and the send time has not passed.
    timing = ("on_time" if slot["state"] == "asked" and at < slot["send_at"]
              else "late" if at < slot["entry_at"] else "after_entry")
    message_id = None
    if at < slot["exit_at"]:
        interim = slot["state"] == "fallback_sent" and slot["fallback_message_id"] is not None
        text = analyst_post(slot_of(slot), proposal, slot["snapshot"], late_at=None if timing == "on_time" else at,
                            fallback=FALLBACK_DIRECTION if interim else None)
        message_id = stable(f"trading-signal-post:{slot['id']}:analyst")
        company._message(conn, project, task["id"], BRIEFER, SIGNAL_KIND, text, message_id=message_id)
    conn.execute("""UPDATE trading_signals SET state=%s,timing=%s,proposal=%s,committed_at=%s,analyst_message_id=%s,
        updated_at=%s WHERE id=%s""", ("committed_late" if slot["state"] == "fallback_sent" else "committed", timing,
                                       Jsonb(proposal.model_dump(mode="json")), at, message_id, at, slot["id"]))
    return _finish(company, conn, project, task, turn, response, done=True)


def tool_error(company, conn, project, task, turn, response, error: ValueError) -> dict:
    """A tool of an allowed signal round rejected its arguments, and Company.commit_turn rolled back the round's
    savepoint: a format error like any other, so the Analyst is asked again without tools."""
    reason = f"tool_error: {_fetch_argument_error(response.decision) or error}"[:300]
    return _format_error(company, conn, project, task, turn, response, _locked_slot(conn, task), reason,
                         schedule.utcnow())


def _locked_slot(conn, task: dict) -> dict:
    """The task's slot row, locked after the project (R8)."""
    slot = conn.execute("SELECT * FROM trading_signals WHERE task_id=%s FOR UPDATE", (task["id"],)).fetchone()
    if not slot:
        raise PolicyError("Signal task has no slot")
    return slot


def _format_error(company, conn, project, task, turn, response, slot, reason, at) -> dict:
    """Re-ask without tools while re-asks remain before the exit; after that, post the say as unparsed and close."""
    failures = slot["format_failures"] + 1
    if failures <= MAX_FORMAT_RETRIES and at < slot["exit_at"]:
        conn.execute("UPDATE trading_signals SET format_failures=%s,last_error=%s,updated_at=%s WHERE id=%s",
                     (failures, reason, at, slot["id"]))
        company._message(conn, project, task["id"], "service:signals", "signal_note", final_note(reason),
                         recipient=BRIEFER, publish=False)
        result = _finish(company, conn, project, task, turn, response, done=False)
        company._new_turn(conn, task)
        return result
    say, message_id = response.decision.say, None
    if say.strip() and at < slot["exit_at"]:
        message_id = stable(f"trading-signal-post:{slot['id']}:unparsed")
        company._message(conn, project, task["id"], BRIEFER, SIGNAL_KIND, unparsed_post(slot_of(slot), say),
                         message_id=message_id)
    conn.execute("""UPDATE trading_signals SET format_failures=%s,last_error=%s,unparsed=true,unparsed_message_id=%s,
        updated_at=%s WHERE id=%s""", (failures, reason, message_id, at, slot["id"]))
    return _finish(company, conn, project, task, turn, response, done=True)


def _finish(company, conn, project, task, turn, response, *, done: bool) -> dict:
    """The general path's bookkeeping for a turn the signal step settles: response, task status, turn_completed."""
    status = "completed" if done else "pending"
    conn.execute("UPDATE tasks SET status=%s,result=%s,error=NULL WHERE id=%s",
                 (status, response.decision.say if done else None, task["id"]))
    conn.execute("UPDATE turns SET status='completed',response=%s,updated_at=now() WHERE id=%s",
                 (Jsonb(response.model_dump(mode="json")), turn["id"]))
    company._event(conn, "turn_completed", {"turn_id": str(turn["id"]), "agent": task["agent"],
                                            "provider": response.provider, "usage": response.usage}, project["id"])
    return {"state": "completed", "task_status": status, "duplicate": False}


def turn_blocked(conn, task: dict, reason: str) -> None:
    """A fault blocked the signal turn, and block_turn posts nothing for it: keep the reason and retry in 5 minutes."""
    at = schedule.utcnow()
    conn.execute("UPDATE trading_signals SET last_error=%s,retry_at=%s,updated_at=%s WHERE task_id=%s",
                 (reason, at + timedelta(minutes=FAULT_RETRY_MINUTES), at, task["id"]))


def _delivery_time(row: dict) -> datetime | None:
    """When Slack took the post: its sent_ts (epoch seconds), else the dispatch start."""
    try:
        return datetime.fromtimestamp(float(row["sent_ts"]), UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return row["started_at"]


def _slot_line(slot: dict, instruments: tuple[str, ...], *, show_date: bool) -> str:
    """The graded signal is the Analyst's on-time or late one; else the signal that was received (the interim rule
    signal, marked); else none."""
    if analyst_counts(slot):
        direction, interim = slot["analyst"], False
    else:
        direction, interim = slot["received"], True
    return result_line(slot["kind"], slot["session_day"], direction, interim, slot["returns"], show_date=show_date,
                       instruments=instruments)
