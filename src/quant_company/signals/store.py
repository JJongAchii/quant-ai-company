"""Signal slots in PostgreSQL: one row per KRX session and kind, the frozen snapshot source and the Analyst task.

Every time decision takes the caller's `at` from briefing.schedule.utcnow() (the one signals clock) and stores it."""

from datetime import UTC, date, datetime, timedelta

from psycopg.types.json import Jsonb

from ..briefing.contracts import BRIEFER
from ..briefing.schedule import overrides
from ..briefing.store import BriefStore
from ..company import as_json, stable
from .contracts import SIGNAL_KIND, Slot
from .instructions import instruction
from .render import KIND_WORDS, day_text
from .schedule import kst_day, slots_for
from .snapshot import follow_us, snapshot_text

TIMES = ("snapshot_at", "snapshot_latest", "tools_until", "fallback_at", "send_at", "entry_at", "exit_at")
PRIORITY = -100
STANDALONE_INSTRUCTION = "이 스레드의 매매 신호와 후속 질문에 Analyst가 답한다."
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
