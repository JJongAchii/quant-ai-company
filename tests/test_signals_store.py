"""Signal slots on real PostgreSQL: scheduling, the frozen snapshot source and the Analyst task. Slack ingress is
simulated (a direct SlackIngress.accept call), never a delivered Slack event."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

from quant_company.briefing.contracts import BRIEFER
from quant_company.company import stable
from quant_company.runtime import brief_data_loops
from quant_company.signals.contracts import SignalProposal
from quant_company.signals.runner import SignalRunner
from quant_company.signals.schedule import slots_for
from quant_company.signals.snapshot import CATALOGUE, snapshot_text
from quant_company.signals.store import SignalStore, slot_of
from quant_company.slack import SlackIngress

from .signals_fixtures import (  # noqa: F401
    DAY,
    KST,
    OBSERVED,
    am_project,
    clock,
    proposal,
    recorded,
    signals,
    snapshot,
)

STANDALONE = stable("signal-project:2026-11-20")
INTRADAY_SOURCE = "signal:2026-11-20:intraday:snapshot"


def kst(hour, minute=0, day=DAY):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST)


def slot_rows(company):
    with company.db.transaction() as conn:
        return conn.execute("SELECT * FROM trading_signals ORDER BY session_day,kind").fetchall()


def one(company, query, *args):
    with company.db.transaction() as conn:
        return conn.execute(query, args).fetchone()


def ask_slot(store, at, kind, value=None):
    [row] = [row for row in store.due_asks(at) if row["kind"] == kind]
    assert store.ask(row, value or snapshot(kind), at)
    return row


def test_schedule_is_idempotent(signals):  # noqa: F811
    store = SignalStore(signals)
    thursday = date(2026, 11, 19)
    assert store.schedule(kst(6, day=thursday)) == 4
    assert store.schedule(kst(6, day=thursday)) == 0
    rows = slot_rows(signals)
    assert [slot_of(row) for row in rows] == slots_for(thursday) + slots_for(DAY)
    assert slot_of(rows[0]).entry_at.utcoffset() == timedelta(0)
    assert str(rows[0]["id"]) == stable("trading-signal:2026-11-19:intraday")
    assert {(row["state"], row["channel"], row["owner_user"]) for row in rows} == {("scheduled", "CQUANT", "UHUMAN")}


async def test_tick_at_kst_midnight_schedules_kst_day(signals, clock):  # noqa: F811
    calls = []
    # 00:30 KST on Friday DAY is still Thursday in UTC, and Thursday is a trading day too.
    clock["at"] = datetime(2026, 11, 19, 15, 30, tzinfo=UTC)
    runner = SignalRunner(signals, fetch=calls.append)
    assert await runner.tick() == {"state": "ran", "scheduled": 2, "asked": 0}
    assert [(row["session_day"], row["kind"]) for row in slot_rows(signals)] == [(DAY, "intraday"), (DAY, "overnight")]
    assert calls == []


def test_ask_uses_am_thread_and_priority(signals):  # noqa: F811
    store = SignalStore(signals)
    project = am_project(signals, DAY)
    store.schedule(kst(7))
    assert store.due_asks(kst(7, 29)) == []
    row = ask_slot(store, kst(7, 30), "intraday")
    task = one(signals, "SELECT * FROM tasks WHERE id=%s", row["id"])
    assert (task["agent"], task["kind"], task["priority"], task["project_id"]) == (
        BRIEFER, "trading_signal", -100, project["id"])
    assert INTRADAY_SOURCE in task["instruction"] and "08:30" in task["instruction"]
    assert one(signals, "SELECT count(*) n FROM turns WHERE task_id=%s AND status='queued'", task["id"])["n"] == 1
    signal = one(signals, "SELECT * FROM trading_signals WHERE id=%s", row["id"])
    assert (signal["state"], signal["project_id"], signal["task_id"]) == ("asked", project["id"], task["id"])
    assert not one(signals, "SELECT 1 FROM projects WHERE id=%s", STANDALONE)


def test_ask_in_a_revised_thread_uses_the_revision_task_id(signals):  # noqa: F811
    store = SignalStore(signals)
    project = am_project(signals, DAY)
    with signals.db.transaction() as conn:  # the owner revised the AM thread before the snapshot
        conn.execute("UPDATE projects SET revision=2 WHERE id=%s", (project["id"],))
    store.schedule(kst(7))
    row = ask_slot(store, kst(7, 30), "intraday")
    task_id = stable("trading-signal:2026-11-20:intraday:r2")
    assert one(signals, "SELECT revision FROM tasks WHERE id=%s", task_id)["revision"] == 2
    assert str(one(signals, "SELECT task_id FROM trading_signals WHERE id=%s", row["id"])["task_id"]) == task_id


def test_ask_freezes_snapshot_source_and_baselines(signals):  # noqa: F811
    store = SignalStore(signals)
    store.schedule(kst(7))
    frozen = snapshot("intraday")
    row = ask_slot(store, kst(8), "intraday", frozen)
    signal = one(signals, "SELECT * FROM trading_signals WHERE id=%s", row["id"])
    # The full snapshot, originals included, stays with the slot; the Analyst's source has the text only.
    assert signal["snapshot"] == frozen and frozen["raw"]
    assert signal["snapshot_source_id"] == INTRADAY_SOURCE
    assert signal["baselines"] == {"always_long": "long", "follow_us": "long"}
    source = one(signals, "SELECT * FROM sources WHERE id=%s", INTRADAY_SOURCE)
    assert (source["approved"], str(source["project_id"]), source["synthetic"]) == (True, STANDALONE, True)
    assert source["content"] == snapshot_text(frozen)
    assert source["available_at"] == OBSERVED["intraday"]
    assert source["uri"] == "company://signals/2026-11-20/intraday/snapshot"
    assert source["metadata"] == {"observed_at": frozen["observed_at"], "receipts": frozen["receipts"]}


def test_ask_twice_creates_one_task_and_source(signals):  # noqa: F811
    store = SignalStore(signals)
    store.schedule(kst(7))
    [row] = store.due_asks(kst(8))
    first = snapshot("intraday")
    assert store.ask(row, first, kst(8))
    # A restart between the snapshot and the commit asks again with the stale row and a newer snapshot.
    assert not store.ask(row, {**first, "observed_at": kst(8, 1).isoformat()}, kst(8, 1))
    assert one(signals, "SELECT snapshot FROM trading_signals WHERE id=%s", row["id"])["snapshot"] == first
    # Two workers asking the same slot at once: one task, one source.
    [night] = store.due_asks(kst(14, 30))
    frozen = snapshot("overnight")
    with ThreadPoolExecutor(2) as pool:
        won = list(pool.map(lambda _: store.ask(night, frozen, kst(14, 30)), range(2)))
    assert sorted(won) == [False, True]
    with signals.db.transaction() as conn:
        assert conn.execute("SELECT count(*) n FROM tasks WHERE kind='trading_signal'").fetchone()["n"] == 2
        assert conn.execute("SELECT count(*) n FROM turns").fetchone()["n"] == 2
        assert conn.execute("SELECT count(*) n FROM sources WHERE id LIKE 'signal:%'").fetchone()["n"] == 2


def test_intraday_waits_for_am_edition_until_latest(signals):  # noqa: F811
    store = SignalStore(signals)
    store.schedule(kst(7))
    assert store.due_asks(kst(7, 29)) == []
    # A committed AM edition whose parent post has not been delivered has no thread to reply in yet.
    am_project(signals, DAY, thread_ts=None)
    assert store.due_asks(kst(7, 31)) == []
    row = ask_slot(store, kst(8), "intraday")
    project = one(signals, "SELECT * FROM projects WHERE id=%s", STANDALONE)
    assert (project["title"], project["channel"], project["owner_user"], project["thread_ts"]) == (
        "11월 20일 매매 신호", "CQUANT", "UHUMAN", None)
    assert project["instruction"] == "이 스레드의 매매 신호와 후속 질문에 Analyst가 답한다."
    assert str(one(signals, "SELECT project_id FROM tasks WHERE id=%s", row["id"])["project_id"]) == STANDALONE


def test_no_ask_from_entry_on(signals):  # noqa: F811
    """After a worker outage a slot is not asked once its trade has started."""
    store = SignalStore(signals)
    am_project(signals, DAY)
    store.schedule(kst(7))
    assert store.due_asks(kst(9)) == []
    assert [row["kind"] for row in store.due_asks(kst(15, 29))] == ["overnight"]
    assert store.due_asks(kst(15, 30)) == []
    assert store.due_asks(kst(9, day=date(2026, 11, 23))) == []


def test_overnight_reuses_intraday_project(signals):  # noqa: F811
    store = SignalStore(signals)
    store.schedule(kst(7))
    ask_slot(store, kst(8), "intraday")
    edition = am_project(signals, DAY)  # the AM edition arrives after the standalone thread was made
    night = ask_slot(store, kst(14, 30), "overnight")
    assert {str(row["project_id"]) for row in slot_rows(signals)} == {STANDALONE}
    assert str(one(signals, "SELECT project_id FROM tasks WHERE id=%s", night["id"])["project_id"]) == STANDALONE
    assert one(signals, "SELECT count(*) n FROM tasks WHERE project_id=%s", edition["id"])["n"] == 0
    with signals.db.transaction() as conn:
        intraday = conn.execute("SELECT * FROM trading_signals WHERE kind='intraday'").fetchone()
        # A slot that already has a project keeps it, so its later posts stay in the same thread.
        assert str(store._thread_project(conn, intraday)["id"]) == STANDALONE


def test_thread_reply_in_standalone_project_reaches_analyst(signals, credentials):  # noqa: F811
    store = SignalStore(signals)
    store.schedule(kst(7))
    ask_slot(store, kst(8), "intraday")
    with signals.db.transaction() as conn:  # the first signal post's delivery receipt
        conn.execute("UPDATE projects SET thread_ts='300.001' WHERE id=%s", (STANDALONE,))
    credentials[BRIEFER] = {**credentials["director"], "app_id": "ABRIEF", "bot_user_id": "UBRIEF"}
    ingress = SlackIngress(signals.settings, signals, credentials)
    event = {"type": "message", "channel": "CQUANT", "user": "UHUMAN", "ts": "300.2", "thread_ts": "300.001",
             "text": "왜 롱으로 봤어?"}
    director = {"team_id": "TTEST", "api_app_id": credentials["director"]["app_id"], "event": event}
    assert ingress.accept("director", director, credentials["director"])["ignored"]
    payload = {"team_id": "TTEST", "api_app_id": "ABRIEF", "event_id": "signal-followup", "event": event}
    result = ingress.accept(BRIEFER, payload, credentials[BRIEFER])
    task = one(signals, "SELECT agent,project_id,kind FROM tasks WHERE id=%s", result["task_id"])
    assert (task["agent"], str(task["project_id"]), task["kind"]) == (BRIEFER, STANDALONE, "work")


async def test_tick_freezes_the_snapshot_and_asks_once(signals, clock):  # noqa: F811
    am_project(signals, DAY)
    runner = SignalRunner(signals, fetch=recorded("intraday"))
    assert await runner.tick() == {"state": "ran", "scheduled": 2, "asked": 1}
    assert await runner.tick() == {"state": "idle", "scheduled": 0, "asked": 0}
    assert one(signals, "SELECT snapshot FROM trading_signals WHERE kind='intraday'")["snapshot"] == snapshot("intraday")


async def test_tick_waits_while_the_briefing_is_not_authorized(signals, clock):  # noqa: F811
    signals.settings.slack_allowed_channels = []
    assert await SignalRunner(signals, fetch=recorded("intraday")).tick() == {"state": "paused"}
    assert slot_rows(signals) == []


def test_loop_only_when_enabled(signals):  # noqa: F811
    signals.settings.analyst_signals_enabled = False
    assert [loop.name for loop in brief_data_loops(signals)] == ["brief-data"]
    signals.settings.analyst_signals_enabled = True
    loops = brief_data_loops(signals)
    assert [loop.name for loop in loops] == ["brief-data", "signals"]
    assert [(loop.timeout, loop.attempts, loop.retry_seconds, loop.wait(None), loop.lane) for loop in loops] == [
        (90, 2, 5, 30, None), (120, 2, 5, 15, None)]
    signals.settings.briefing_enabled = False
    assert brief_data_loops(signals) == []


def test_shared_fixtures_are_valid():
    assert SignalProposal.model_validate(proposal()).source_ids == [INTRADAY_SOURCE]
    for kind in ("intraday", "overnight"):
        frozen = snapshot(kind)
        assert not frozen["errors"] and set(frozen["quotes"]) == {k for k, i in CATALOGUE.items() if kind in i.kinds}
