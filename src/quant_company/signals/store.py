"""Signal slots in PostgreSQL: one row per KRX session and kind, the frozen snapshot source and the Analyst task.

Every time decision takes the caller's `at` from briefing.schedule.utcnow() (the one signals clock) and stores it. The
turn hooks below run inside Company's transactions and read that clock themselves."""

import json
from datetime import UTC, date, datetime, timedelta

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from ..briefing import schedule
from ..briefing.contracts import BRIEFER
from ..briefing.editor import artifact
from ..briefing.schedule import overrides
from ..briefing.store import BriefStore
from ..company import PolicyError, as_json, stable
from .contracts import (
    FALLBACK_DIRECTION,
    FAULT_RETRY_MINUTES,
    MAX_FORMAT_RETRIES,
    MAX_READS,
    MAX_SEARCHES,
    MAX_TOOL_TURNS,
    SIGNAL_KIND,
    SignalProposal,
    Slot,
)
from .instructions import final_note, instruction
from .render import KIND_WORDS, analyst_post, day_text, unparsed_post
from .schedule import kst_day, slots_for
from .snapshot import follow_us, snapshot_text

TIMES = ("snapshot_at", "snapshot_latest", "tools_until", "fallback_at", "send_at", "entry_at", "exit_at")
PRIORITY = -100
STANDALONE_INSTRUCTION = "이 스레드의 매매 신호와 후속 질문에 Analyst가 답한다."
# The re-ask reason for any answer briefing.editor.artifact rejects before reading its JSON.
ANSWER_SHAPE = "answer_shape: status complete with exactly one artifact, its source_ids empty, and nothing else"
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
        slot whose trade has started (after an outage) is not asked."""
        with self.db.transaction() as conn:
            rows = conn.execute("""SELECT * FROM trading_signals WHERE state='scheduled' AND snapshot_at<=%s
                AND entry_at>%s ORDER BY snapshot_at,kind""", (at, at)).fetchall()
            return [row for row in rows if row["kind"] != "intraday" or at >= row["snapshot_latest"]
                    or self._am_edition(conn, row["session_day"])]

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
        """Freeze the snapshot as an approved source and open the Analyst's task in one transaction. A slot that is
        no longer scheduled (asked before a restart, or by another worker) is left as it is."""
        with self.db.transaction() as conn:
            current = conn.execute("SELECT * FROM trading_signals WHERE id=%s FOR UPDATE", (row["id"],)).fetchone()
            if not current or current["state"] != "scheduled":
                return False
            slot, project = slot_of(current), self._thread_project(conn, current)
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
            conn.execute("""UPDATE trading_signals SET state='asked',project_id=%s,task_id=%s,snapshot=%s,
                snapshot_source_id=%s,baselines=%s,updated_at=%s WHERE id=%s""",
                         (project["id"], task["id"], Jsonb(as_json(snapshot)), identity,
                          Jsonb({"always_long": "long", "follow_us": follow_us(snapshot)}), at, current["id"]))
            self.company._event(conn, "trading_signal_asked", {"slot_id": current["id"], "task_id": task["id"],
                                                               "source_id": identity}, project["id"])
        return True


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


def tools_allowed(conn, task: dict, decision) -> bool:
    """Every requested tool is still allowed, and the searches and reads stay within their limits after this turn."""
    usable, searches, reads = _tool_budget(conn, task)
    names = [action.name for action in decision.tools]
    return (usable and searches + names.count("web_search") <= MAX_SEARCHES
            and reads + names.count("web_read") <= MAX_READS)


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
    """The signal step of Company.commit_turn, under its project lock. None hands a tool round that is still allowed back
    to the general path. Anything else is the Analyst's answer: a valid proposal is stored and posted once; a format
    error re-asks without tools at most twice, then the say is posted as unparsed. A format problem never raises."""
    if (decision.status == "continue" and decision.tools and not decision.artifacts
            and tools_allowed(conn, task, decision)):
        return None
    at = schedule.utcnow()
    slot = conn.execute("SELECT * FROM trading_signals WHERE task_id=%s FOR UPDATE", (task["id"],)).fetchone()
    if not slot:
        raise PolicyError("Signal task has no slot")
    if slot["state"] in {"committed", "committed_late"} or slot["unparsed"]:
        # Another task of this slot settled it already; the slot keeps that one outcome and post.
        return _finish(company, conn, project, task, turn, response, done=True)
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
