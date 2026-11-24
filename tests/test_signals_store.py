"""Signal slots on real PostgreSQL: scheduling, the frozen snapshot source, the Analyst task, the interim post, retries
and the outbox gate. Slack is simulated throughout: ingress is a direct SlackIngress.accept call and every send goes
through httpx.MockTransport. Nothing here is a delivered Slack message."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from quant_company.briefing.contracts import BRIEFER
from quant_company.company import stable
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.newsletter.store import NewsletterStore
from quant_company.runtime import brief_data_loops
from quant_company.signals.contracts import SignalProposal
from quant_company.signals.runner import SignalRunner
from quant_company.signals.schedule import slots_for
from quant_company.signals.snapshot import CATALOGUE, snapshot_text
from quant_company.signals.store import SignalStore, slot_id, slot_of
from quant_company.slack import SlackIngress, SlackOutbox
from quant_company.video.store import VideoStore

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
from .test_signals_turns import MONDAY, TASK, answer, ask, posts, queued, rows, signal

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
    assert await runner.tick() == {"state": "ran", "fallbacks": 0, "scheduled": 2, "asked": 0, "retried": 0, "demoted": 0, "retired": 0, "graded": 0, "reported": 0}
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
    [night] = store.due_asks(kst(15, 0))
    frozen = snapshot("overnight")
    with ThreadPoolExecutor(2) as pool:
        won = list(pool.map(lambda _: store.ask(night, frozen, kst(15, 0)), range(2)))
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
    night = ask_slot(store, kst(15, 0), "overnight")
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
    assert await runner.tick() == {"state": "ran", "fallbacks": 0, "scheduled": 2, "asked": 1, "retried": 0, "demoted": 0, "retired": 0, "graded": 0, "reported": 0}
    assert await runner.tick() == {"state": "idle", "fallbacks": 0, "scheduled": 0, "asked": 0, "retried": 0, "demoted": 0, "retired": 0, "graded": 0, "reported": 0}
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


# The interim signal, late posts, retries and the outbox gate. Turns run on past session days (R9).
TUESDAY, WEDNESDAY, FRIDAY = date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 13)
MONDAY_THREAD = stable("signal-project:2026-11-16")  # Monday's standalone signal thread
INTERIM = "롱 · Analyst 판단 아님 · 규칙 신호(항상 롱, 과거 적중 약 50%)"


def late(reason):
    return f"Analyst 신호가 늦어지고 있습니다(사유: {reason}). 나오는 대로 이어서 올립니다."


def post_id(part, day=MONDAY, kind="intraday"):
    return stable(f"trading-signal-post:{slot_id(day, kind)}:{part}")


def ask_at(company, clock, at, kind="intraday", day=MONDAY):  # noqa: F811
    """Schedule and ask day's slot at `at` (from snapshot_latest on, no AM edition is needed). Returns its turn."""
    clock["at"] = at
    store = SignalStore(company)
    store.schedule(at)
    [row] = [row for row in store.due_asks(at) if (row["session_day"], row["kind"]) == (day, kind)]
    assert store.ask(row, snapshot(kind, day), at)
    return queued(company, slot_id(day, kind))


def answer_at(company, clock, turn, at, value=None, *, prepared=None):  # noqa: F811
    """The Analyst's turn starts at `prepared` (by default `at`) and commits its final answer at `at`."""
    clock["at"] = prepared or at
    assert company.prepare_turn(turn)["state"] == "ready"
    clock["at"] = at
    return company.commit_turn(turn, answer(turn, value))


def fallback_at(company, clock, day=MONDAY, kind="intraday"):  # noqa: F811
    """send_fallbacks at the slot's fallback_at (08:28 or 15:13 on a normal session)."""
    clock["at"] = next(item for item in slots_for(day) if item.kind == kind).fallback_at
    return SignalStore(company).send_fallbacks(clock["at"])


def claimable(conn, message_id):
    """The outbox row as SlackOutbox._claim_one hands it to the gate: still pending, and locked."""
    return conn.execute("""SELECT o.*,m.kind AS message_kind FROM outbox o JOIN messages m ON m.id=o.id
        WHERE o.id=%s FOR UPDATE OF o""", (message_id,)).fetchone()


def simulated_slack(company, credentials, reply=None):
    """SlackOutbox over httpx.MockTransport: simulated Slack, never a delivered message. Each request body is recorded;
    the answer is ok with ts 300.001, 300.002, ... unless reply(request, n) returns another response or raises."""
    sent = []

    def post(request):
        sent.append(json.loads(request.content))
        response = reply(request, len(sent)) if reply else None
        return response or httpx.Response(200, json={"ok": True, "ts": f"300.{len(sent):03d}"})
    credentials[BRIEFER] = {**credentials["director"], "app_id": "ABRIEF", "bot_user_id": "UBRIEF"}
    return SlackOutbox(company, credentials, httpx.MockTransport(post)), sent


def test_fallback_sent_once_at_fallback_at(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    ask(signals, clock)  # the Analyst's first turn stays queued
    store = SignalStore(signals)
    assert store.send_fallbacks(kst(8, 27, MONDAY)) == 0
    clock["at"] = kst(8, 28, MONDAY)
    assert store.send_fallbacks(clock["at"]) == 1
    assert store.send_fallbacks(clock["at"]) == 0
    [post] = posts(signals)
    assert post["text"].split("\n") == [
        "⏱️ 임시 신호 · 11월 16일(월) 장중  시가 09:00 진입 → 종가 15:30 청산", INTERIM, late("분석이 아직 끝나지 않음")]
    assert (str(post["id"]), post["author"], post["agent"], post["status"]) == (
        post_id("fallback"), BRIEFER, BRIEFER, "pending")
    assert one(signals, "SELECT thread_ts FROM outbox WHERE id=%s", post["id"])["thread_ts"] == "100.001"
    row = signal(signals)
    assert (row["state"], row["fallback_reason"], row["fallback_sent_at"], str(row["fallback_message_id"])) == (
        "fallback_sent", "분석이 아직 끝나지 않음", clock["at"], str(post["id"]))
    # Two workers at the overnight fallback time still make one interim post, with the overnight hit rate.
    ask(signals, clock, kind="overnight")
    clock["at"] = kst(15, 13, MONDAY)
    with ThreadPoolExecutor(2) as pool:
        made = list(pool.map(lambda _: store.send_fallbacks(clock["at"]), range(2)))
    assert sorted(made) == [0, 1]
    assert posts(signals)[1]["text"].split("\n")[:2] == [
        "⏱️ 임시 신호 · 11월 16일(월) 오버나잇  종가 15:30 진입 → 11월 17일(화) 시가 09:00 청산",
        "롱 · Analyst 판단 아님 · 규칙 신호(항상 롱, 과거 적중 약 59%)"]


def test_no_fallback_when_on_time(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    turn = ask(signals, clock)
    answer_at(signals, clock, turn, kst(8, 20, MONDAY))
    assert fallback_at(signals, clock) == 0
    [post] = posts(signals)
    assert post["text"].startswith("📈 장중 신호 · 11월 16일(월)  시가 09:00 진입 → 종가 15:30 청산\n")
    row = signal(signals)
    assert (row["state"], row["timing"], row["fallback_message_id"], row["fallback_reason"]) == (
        "committed", "on_time", None, None)


def test_fallback_reason_from_auth_block(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    turn = ask(signals, clock)
    signals.prepare_turn(turn)
    signals.block_turn(turn, "auth")
    assert fallback_at(signals, clock) == 1
    [post] = posts(signals)
    assert post["text"].split("\n")[2] == late("모델 로그인 만료")
    assert signal(signals)["fallback_reason"] == "모델 로그인 만료"


def running_question(company, turn):
    """An owner question in the thread is running, so the signal's queued turn waits behind it."""
    work = company.ingest(event_key="slack:running-question", text="오늘 반도체 업종은 어때?", owner="UHUMAN",
                          agent=BRIEFER, project_id=str(signal(company)["project_id"]))
    assert company.prepare_turn(queued(company, work["task_id"]))["state"] == "ready"


@pytest.mark.parametrize("setup, reason", [
    (lambda company, turn: None, "분석이 아직 끝나지 않음"),
    (running_question, "앞선 업무가 끝나기를 기다리는 중"),
    (lambda company, turn: company.prepare_turn(turn), "분석이 아직 끝나지 않음"),
    (lambda company, turn: (company.prepare_turn(turn), company.defer_turn(turn, 60, "quota")), "모델 사용 한도"),
    (lambda company, turn: (company.prepare_turn(turn), company.defer_turn(turn, 60, "busy")), "모델 혼잡"),
    (lambda company, turn: (company.prepare_turn(turn), company.block_turn(turn, "invalid_output")), "모델 응답 오류"),
], ids=["queued", "behind-a-running-turn", "running", "quota", "busy", "blocked"])
def test_fallback_reason_table(signals, clock, setup, reason):  # noqa: F811
    am_project(signals, MONDAY)
    turn = ask(signals, clock)
    setup(signals, turn)
    assert fallback_at(signals, clock) == 1
    assert posts(signals)[0]["text"].split("\n")[2] == late(reason)
    assert signal(signals)["fallback_reason"] == reason


def test_unparsed_answer_gets_the_interim_without_a_promise(signals, clock):  # noqa: F811
    """R13: no Analyst signal follows an unparsed answer, so the interim post says the rule stands in for it."""
    am_project(signals, MONDAY)
    turn = ask(signals, clock)
    for _ in range(3):  # three say-only answers: the say goes out as unparsed
        signals.prepare_turn(turn)
        signals.commit_turn(turn, ProviderResponse(request_id=turn, decision=AgentDecision(
            status="complete", say="롱으로 봅니다.")))
        turn = queued(signals, TASK)
    assert fallback_at(signals, clock) == 1
    unparsed, interim = posts(signals)
    assert unparsed["text"].startswith("📈 장중 신호 · 형식 확인 실패 · 채점 제외\n")
    assert interim["text"].split("\n")[1:] == [
        INTERIM, "Analyst 답 형식을 확인하지 못했습니다(원문은 위 글). 규칙 신호로 대신합니다."]
    row = signal(signals)
    assert (row["state"], row["unparsed"], row["fallback_reason"]) == ("fallback_sent", True, "형식 확인 실패")


async def test_fallback_when_model_never_answers(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    runner = SignalRunner(signals, fetch=recorded("intraday", MONDAY))
    clock["at"] = kst(7, 30, MONDAY)
    assert (await runner.tick())["asked"] == 1
    clock["at"] = kst(8, 28, MONDAY)  # no worker ever ran the Analyst's turn
    assert await runner.tick() == {"state": "ran", "fallbacks": 1, "scheduled": 0, "asked": 0, "retried": 0, "demoted": 0, "retired": 0, "graded": 0, "reported": 0}
    [post] = posts(signals)
    assert post["text"].split("\n")[:2] == ["⏱️ 임시 신호 · 11월 16일(월) 장중  시가 09:00 진입 → 종가 15:30 청산", INTERIM]
    assert one(signals, "SELECT status FROM turns WHERE task_id=%s", TASK)["status"] == "queued"


async def test_no_fallback_post_after_entry(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    runner = SignalRunner(signals, fetch=recorded("intraday", MONDAY))
    clock["at"] = kst(7, 30, MONDAY)
    await runner.tick()
    clock["at"] = kst(9, 5, MONDAY)  # the worker was down from 07:31: a "buy at the open" post is useless now (R3)
    # The trade has started, so the unanswered task now waits behind any slot still before its send time.
    assert await runner.tick() == {"state": "ran", "fallbacks": 0, "scheduled": 0, "asked": 0, "retried": 0, "demoted": 1, "retired": 0, "graded": 0, "reported": 0}
    row = signal(signals)
    assert (row["state"], row["fallback_reason"], row["fallback_sent_at"], row["fallback_message_id"]) == (
        "fallback_sent", "서비스 중단", None, None)
    assert posts(signals) == [] and rows(signals, "SELECT id FROM outbox") == []


@pytest.mark.parametrize("day, prepared, at, timing, header", [
    (MONDAY, (8, 41), (8, 41), "late", "📈 장중 신호 · 늦음 08:41 · 임시 신호와 반대"),
    (TUESDAY, (9, 5), (9, 5), "after_entry", "📈 장중 신호 · 늦음 09:05 · 임시 신호와 반대 · 진입 뒤(채점 제외)"),
    (WEDNESDAY, (15, 29), (15, 31), "after_entry", None),
], ids=["late", "after-entry", "after-exit"])
def test_late_signal_after_fallback(signals, clock, day, prepared, at, timing, header):  # noqa: F811
    am_project(signals, day)
    turn = ask(signals, clock, day)
    assert fallback_at(signals, clock, day) == 1
    answer_at(signals, clock, turn, kst(*at, day=day), proposal(session_day=str(day), direction="short"),
              prepared=kst(*prepared, day=day))
    row = signal(signals, day)
    assert (row["state"], row["timing"], row["committed_at"]) == ("committed_late", timing, clock["at"])
    texts = [post["text"].split("\n") for post in posts(signals)]
    assert texts[0][0].startswith("⏱️ 임시 신호")
    if header:  # posted until the exit: the first line gives the time and how it compares with the interim post
        assert texts[1][:3] == [header, "시가 09:00 진입 → 종가 15:30 청산", "숏 · 확신도 60% (반반 50%에 오늘 근거 +10%p)"]
    else:  # after the exit (15:30) the signal is recorded, not posted
        assert len(texts) == 1 and row["analyst_message_id"] is None


def test_analyst_post_waits_for_fallback_delivery(signals, clock, credentials):  # noqa: F811
    am_project(signals, MONDAY)
    turn = ask(signals, clock)
    fallback_at(signals, clock)
    answer_at(signals, clock, turn, kst(8, 41, MONDAY), proposal(session_day=str(MONDAY), direction="short"))
    store, analyst = SignalStore(signals), post_id("analyst")
    before = one(signals, "SELECT next_at FROM outbox WHERE id=%s", analyst)["next_at"]
    with signals.db.transaction() as conn:
        assert store.gate(conn, claimable(conn, analyst)) is False
    waiting = one(signals, "SELECT status,next_at,error FROM outbox WHERE id=%s", analyst)
    assert (waiting["status"], waiting["error"]) == ("pending", "awaiting_interim_post")
    assert waiting["next_at"] > before
    # The dispatcher claims the interim post first; once it is delivered the late signal may go.
    sender, _ = simulated_slack(signals, credentials)
    claimed = sender.claim()
    assert claimed["id"] == post_id("fallback") and sender.claim() is None
    sender.settle(claimed, "delivered", sent_ts="100.002")
    with signals.db.transaction() as conn:
        assert store.gate(conn, claimable(conn, analyst)) is True


def test_interim_post_goes_ahead_of_older_posts(signals, clock, credentials):  # noqa: F811
    """A backlog of other posts never holds the interim post past the send time; their own order is unchanged."""
    work = signals.ingest(event_key="older-work", text="오늘 시장 정리해줘", owner="UHUMAN", channel="CQUANT",
                          thread_ts="200.001")
    with signals.db.transaction() as conn:  # an older answer in another thread, still pending
        signals._message(conn, signals._project(conn, work["project_id"]), work["task_id"], "director", "answer",
                         "앞선 답", message_id=stable("older-answer"))
    am_project(signals, MONDAY)
    ask(signals, clock)
    fallback_at(signals, clock)
    sender, _ = simulated_slack(signals, credentials)
    assert [sender.claim()["id"] for _ in range(2)] == [post_id("fallback"), stable("older-answer")]


async def test_signal_post_passes_routing_hold(signals, clock, credentials):  # noqa: F811
    """Simulated Slack: a routing task in the thread holds the thread's other posts, never the signal."""
    project = am_project(signals, MONDAY)
    turn = ask(signals, clock)
    with signals.db.transaction() as conn:  # an earlier Analyst answer in the thread, not yet delivered
        signals._message(conn, signals._project(conn, project["id"]), None, BRIEFER, "answer", "앞선 답",
                         message_id=stable("held-answer"))
    signals.ingest(event_key="slack:routing", text="이 신호 쉽게 설명해줘", owner="UHUMAN", agent="director",
                   project_id=str(project["id"]), interpret=True)
    answer_at(signals, clock, turn, kst(8, 20, MONDAY))
    sender, sent = simulated_slack(signals, credentials)
    assert await sender.send_one()
    assert await sender.send_one() is False
    assert [(body["text"].split("\n")[0], body["thread_ts"]) for body in sent] == [
        ("📈 장중 신호 · 11월 16일(월)  시가 09:00 진입 → 종가 15:30 청산", "100.001")]
    statuses = {str(row["id"]): row["status"] for row in rows(signals, "SELECT id,status FROM outbox")}
    assert statuses == {post_id("analyst"): "delivered", stable("held-answer"): "pending"}


async def test_standalone_thread_ts_from_first_delivery(signals, clock, credentials):  # noqa: F811
    """Simulated Slack: with no AM edition the first signal post starts the day's thread, and later posts reply in it,
    including one queued before the thread existed."""
    turn = ask_at(signals, clock, kst(8, 0, MONDAY))
    fallback_at(signals, clock)
    answer_at(signals, clock, turn, kst(8, 29, MONDAY))  # queued while the interim post has no thread yet
    assert one(signals, "SELECT thread_ts FROM outbox WHERE id=%s", post_id("analyst"))["thread_ts"] is None
    sender, sent = simulated_slack(signals, credentials)
    assert await sender.send_one() and await sender.send_one()
    assert "thread_ts" not in sent[0] and sent[0]["text"].startswith("⏱️ 임시 신호 · 11월 16일(월) 장중")
    assert sent[1]["thread_ts"] == "300.001"
    assert sent[1]["text"].startswith("📈 장중 신호 · 늦음 08:29 · 임시 신호와 같음\n")
    assert one(signals, "SELECT thread_ts FROM projects WHERE id=%s", MONDAY_THREAD)["thread_ts"] == "300.001"
    assert one(signals, "SELECT thread_ts FROM outbox WHERE id=%s", post_id("analyst"))["thread_ts"] == "300.001"
    # The overnight slot shares the thread; its interim post replies in it from the start.
    ask_at(signals, clock, kst(15, 0, MONDAY), "overnight")
    fallback_at(signals, clock, kind="overnight")
    assert await sender.send_one()
    assert sent[2]["thread_ts"] == "300.001" and sent[2]["text"].startswith("⏱️ 임시 신호 · 11월 16일(월) 오버나잇")


def test_post_waits_while_the_thread_root_is_in_flight(signals, clock, credentials):  # noqa: F811
    """Two senders: while the standalone thread's first post is in flight, a later post waits for its ts."""
    turn = ask_at(signals, clock, kst(8, 0, MONDAY))
    answer_at(signals, clock, turn, kst(8, 20, MONDAY))  # on time: the thread's first post
    sender, _ = simulated_slack(signals, credentials)
    root = sender.claim()  # one sender has it in flight
    ask_at(signals, clock, kst(15, 0, MONDAY), "overnight")
    fallback_at(signals, clock, kind="overnight")
    night, store = post_id("fallback", kind="overnight"), SignalStore(signals)
    with signals.db.transaction() as conn:
        assert store.gate(conn, claimable(conn, night)) is False
    assert one(signals, "SELECT status,error,thread_ts FROM outbox WHERE id=%s", night) == {
        "status": "pending", "error": "awaiting_thread_root", "thread_ts": None}
    sender.settle(root, "delivered", sent_ts="300.001")
    with signals.db.transaction() as conn:
        row = claimable(conn, night)
        assert store.gate(conn, row) is True and row["thread_ts"] == "300.001"
    assert one(signals, "SELECT thread_ts FROM outbox WHERE id=%s", night)["thread_ts"] == "300.001"

def test_blocked_signal_retried_twice(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    turn, store = ask(signals, clock), SignalStore(signals)
    for retry in (1, 2):  # blocked at 07:30, retried at 07:35, blocked again, retried at 07:40
        signals.prepare_turn(turn)
        signals.block_turn(turn, "auth")
        due = clock["at"] + timedelta(minutes=5)
        assert store.retry(due - timedelta(seconds=1)) == 0
        clock["at"] = due
        with ThreadPoolExecutor(2) as pool:  # two workers at once still make one retry
            assert sorted(pool.map(store.retry, [due, due])) == [0, 1]
        turn = queued(signals, TASK)
        assert turn == stable(f"turn:{TASK}:{retry + 1}")
        assert one(signals, "SELECT status FROM tasks WHERE id=%s", TASK)["status"] == "pending"
        assert signal(signals)["fault_retries"] == retry
    signals.prepare_turn(turn)
    signals.block_turn(turn, "auth")
    clock["at"] += timedelta(minutes=5)
    assert store.retry(clock["at"]) == 0
    assert (signal(signals)["fault_retries"], queued(signals, TASK)) == (2, None)
    assert one(signals, "SELECT count(*) n FROM events WHERE kind='trading_signal_retried'")["n"] == 2


def test_revision_change_recreates_task(signals, clock):  # noqa: F811
    project = am_project(signals, MONDAY)
    ask(signals, clock)
    with signals.db.transaction() as conn:
        conn.execute("UPDATE projects SET revision=2 WHERE id=%s", (project["id"],))
    store, fresh = SignalStore(signals), stable("trading-signal:2026-11-16:intraday:r2")
    assert store.retry(kst(8, 40, MONDAY)) == 1  # after the send time, still before the open
    task = one(signals, "SELECT * FROM tasks WHERE id=%s", fresh)
    assert (task["agent"], task["kind"], task["priority"], task["revision"]) == (BRIEFER, "trading_signal", -100, 2)
    assert task["instruction"] == one(signals, "SELECT instruction FROM tasks WHERE id=%s", TASK)["instruction"]
    assert str(signal(signals)["task_id"]) == fresh and queued(signals, fresh) == stable(f"turn:{fresh}:1")
    assert store.retry(kst(8, 41, MONDAY)) == 0
    with signals.db.transaction() as conn:  # from the open on, a stale task is not asked again
        conn.execute("UPDATE projects SET revision=3 WHERE id=%s", (project["id"],))
    assert store.retry(kst(9, 0, MONDAY)) == 0
    assert str(signal(signals)["task_id"]) == fresh


async def test_exited_signal_is_retired_without_a_model_call(signals, clock):  # noqa: F811
    """Friday's turn waited out a long quota hold (defer_turn allows up to a week). From Friday's exit the loop closes
    its task: the turn is stale, nothing runs or is posted, and on Monday morning Monday's signal is the only due
    turn."""
    am_project(signals, FRIDAY, thread_ts="100.013")
    friday = ask(signals, clock, FRIDAY)
    signals.prepare_turn(friday)
    signals.defer_turn(friday, 3 * 86400, "quota")
    with signals.db.transaction() as conn:  # the quota wait is over by Monday morning
        conn.execute("UPDATE turns SET due_at=now() WHERE id=%s", (friday,))
    store = SignalStore(signals)
    assert store.retire(kst(15, 29, FRIDAY)) == 0  # before the exit nothing changes
    assert one(signals, "SELECT status FROM turns WHERE id=%s", friday)["status"] == "waiting"
    clock["at"] = kst(15, 30, FRIDAY)
    assert (await SignalRunner(signals, fetch=recorded("intraday", MONDAY)).tick())["retired"] == 1
    assert one(signals, "SELECT status,error FROM turns WHERE id=%s", friday) == {
        "status": "stale", "error": "signal_exited"}
    assert one(signals, "SELECT status,error FROM tasks WHERE id=%s", slot_id(FRIDAY, "intraday")) == {
        "status": "superseded", "error": "signal_exited"}
    assert store.retire(clock["at"]) == 0  # once
    assert rows(signals, "SELECT id FROM outbox") == []
    am_project(signals, MONDAY)
    monday = ask(signals, clock)  # Monday 07:30
    assert signals.due_turns() == [monday]


async def test_past_entry_signal_waits_behind_a_slot_before_its_send_time(signals, clock):  # noqa: F811
    """Monday's intraday task never got its answer in. From the open it is demoted (-100 to -50), so at 15:00 the
    overnight signal runs first, while both still run ahead of every other task."""
    project = am_project(signals, MONDAY)
    intraday = ask(signals, clock)
    overnight = ask(signals, clock, kind="overnight")  # 15:00
    work = signals.ingest(event_key="slack:afternoon-question", text="오늘 장 어땠어?", owner="UHUMAN",
                          agent=BRIEFER, project_id=str(project["id"]))
    owner = queued(signals, work["task_id"])
    assert signals.due_turns() == [intraday, overnight, owner]  # both at -100: the older one first
    store = SignalStore(signals)
    assert store.demote(kst(8, 59, MONDAY)) == 0  # before the open nothing changes
    assert (await SignalRunner(signals, fetch=recorded("overnight", MONDAY)).tick())["demoted"] == 1
    assert signals.due_turns() == [overnight, intraday, owner]
    priorities = {str(row["id"]): row["priority"] for row in rows(signals, "SELECT id,priority FROM tasks")}
    assert (priorities[TASK], priorities[slot_id(MONDAY, "overnight")]) == (-50, -100)
    assert store.demote(clock["at"]) == 0  # once


async def test_revision_keeps_undelivered_signal_post(signals, clock, credentials):  # noqa: F811
    """R14, simulated Slack: a "수정:" revision stales the thread's other undelivered posts, never a signal post."""
    project = am_project(signals, MONDAY)
    turn = ask(signals, clock)
    with signals.db.transaction() as conn:
        signals._message(conn, signals._project(conn, project["id"]), None, BRIEFER, "answer", "앞선 답",
                         message_id=stable("earlier-answer"))
    answer_at(signals, clock, turn, kst(8, 20, MONDAY))
    signals.ingest(event_key="slack:revise", text="수정: 반도체 업종 중심으로 다시 봐줘", owner="UHUMAN",
                   agent=BRIEFER, project_id=str(project["id"]), revise=True)
    statuses = {str(row["id"]): row["status"] for row in rows(signals, "SELECT id,status FROM outbox")}
    assert statuses == {post_id("analyst"): "pending", stable("earlier-answer"): "stale"}
    assert SignalStore(signals).retry(kst(8, 21, MONDAY)) == 0  # a settled slot is not asked again
    sender, sent = simulated_slack(signals, credentials)
    assert await sender.send_one()
    assert [body["text"].split("\n")[0] for body in sent] == [
        "📈 장중 신호 · 11월 16일(월)  시가 09:00 진입 → 종가 15:30 청산"]
    assert one(signals, "SELECT status FROM outbox WHERE id=%s", post_id("analyst"))["status"] == "delivered"


async def test_uncertain_signal_post_not_resent(signals, clock, credentials):  # noqa: F811
    """Simulated Slack timeout: the interim post may have reached Slack, so it is never sent again. The late signal
    still goes out, and with no thread receipt it starts the thread itself."""
    turn = ask_at(signals, clock, kst(8, 0, MONDAY))
    fallback_at(signals, clock)

    def timeout_first(request, n):
        if n == 1:
            raise httpx.ReadTimeout("synthetic ambiguous response", request=request)
    sender, sent = simulated_slack(signals, credentials, timeout_first)
    assert await sender.send_one()
    assert await sender.send_one() is False
    assert len(sent) == 1 and one(signals, "SELECT status,error FROM outbox WHERE id=%s", post_id("fallback")) == {
        "status": "uncertain", "error": "delivery_outcome_unknown"}
    answer_at(signals, clock, turn, kst(8, 41, MONDAY))
    assert await sender.send_one() and await sender.send_one() is False
    assert len(sent) == 2 and sent[1]["text"].startswith("📈 장중 신호 · 늦음 08:41 · 임시 신호와 같음\n")
    assert "thread_ts" not in sent[1]


def test_signal_post_after_exit_goes_stale(signals, clock, credentials):  # noqa: F811
    am_project(signals, MONDAY)
    ask(signals, clock)
    fallback_at(signals, clock)  # Slack then stays unreachable for the rest of the session
    clock["at"] = kst(15, 30, MONDAY)
    sender, sent = simulated_slack(signals, credentials)
    assert sender.claim() is None and sent == []
    assert one(signals, "SELECT status,error FROM outbox WHERE id=%s", post_id("fallback")) == {
        "status": "stale", "error": "signal_after_exit"}


async def test_signals_never_enqueue_public_outputs(signals, clock, credentials, monkeypatch):  # noqa: F811
    """No signal step reaches the video or newsletter queues; simulated Slack for the posts."""
    calls = []

    def refuse(self, *args, **kwargs):
        calls.append(type(self).__name__)
        raise AssertionError("a trading signal must never reach a public output")
    monkeypatch.setattr(VideoStore, "enqueue_isolated", refuse)
    monkeypatch.setattr(NewsletterStore, "enqueue_isolated", refuse)
    signals.settings.video_enabled = signals.settings.newsletter_enabled = True
    am_project(signals, MONDAY)
    runner = SignalRunner(signals, fetch=recorded("intraday", MONDAY))
    clock["at"] = kst(7, 30, MONDAY)
    assert (await runner.tick())["asked"] == 1
    answer_at(signals, clock, queued(signals, TASK), kst(8, 20, MONDAY))
    runner.fetch, clock["at"] = recorded("overnight", MONDAY), kst(15, 0, MONDAY)
    assert (await runner.tick())["asked"] == 1
    clock["at"] = kst(15, 13, MONDAY)
    assert (await runner.tick())["fallbacks"] == 1
    sender, sent = simulated_slack(signals, credentials)
    assert await sender.send_one() and await sender.send_one()
    assert len(sent) == 2 and calls == []


def test_slot_asked_after_its_interim_post(signals, clock):  # noqa: F811
    """A slot the service could not ask before fallback_at (the worker was down) still gets its interim post. The
    Analyst is then asked anyway, and the late signal follows in the same thread as the interim post promises."""
    store = SignalStore(signals)
    store.schedule(kst(7, 0, MONDAY))
    assert fallback_at(signals, clock) == 1
    [interim] = posts(signals)
    assert interim["text"].split("\n")[2] == late("준비 중 오류")
    row = signal(signals)
    assert (row["state"], str(row["project_id"]), row["task_id"]) == ("fallback_sent", MONDAY_THREAD, None)
    [due] = store.due_asks(clock["at"])
    assert store.ask(due, snapshot("intraday", MONDAY), clock["at"])
    assert not store.ask(due, snapshot("intraday", MONDAY), clock["at"])
    row = signal(signals)
    assert (row["state"], str(row["task_id"]), str(row["project_id"])) == ("fallback_sent", TASK, MONDAY_THREAD)
    answer_at(signals, clock, queued(signals, TASK), kst(8, 40, MONDAY))
    assert posts(signals)[1]["text"].startswith("📈 장중 신호 · 늦음 08:40 · 임시 신호와 같음\n")
    assert (signal(signals)["state"], signal(signals)["timing"]) == ("committed_late", "late")
    assert store.due_asks(kst(8, 41, MONDAY)) == []


async def test_tick_isolates_failing_steps_and_slots(signals, clock, monkeypatch, caplog):  # noqa: F811
    """R10: the interim post goes first, and one failing slot or step never holds back another."""
    store = SignalStore(signals)
    store.schedule(kst(7, 0, FRIDAY))
    store.schedule(kst(7, 0, MONDAY))  # the worker was down from Friday morning until Monday 08:28
    am_project(signals, MONDAY)
    send, broken = SignalStore._send_fallback, slot_id(FRIDAY, "intraday")

    def flaky(self, identity, at):
        if str(identity) == broken:
            raise RuntimeError("synthetic slot failure")
        return send(self, identity, at)
    monkeypatch.setattr(SignalStore, "_send_fallback", flaky)
    monkeypatch.setattr(SignalStore, "schedule", lambda self, at: 1 / 0)
    clock["at"] = kst(8, 28, MONDAY)
    with caplog.at_level(logging.ERROR):
        result = await SignalRunner(signals, fetch=recorded("intraday", MONDAY)).tick()
    assert result == {"state": "ran", "fallbacks": 1, "scheduled": 0, "asked": 1, "retried": 0, "demoted": 0, "retired": 0, "graded": 0, "reported": 0}
    assert f"{broken}: RuntimeError" in caplog.text and "ZeroDivisionError" in caplog.text
    states = {(row["session_day"], row["kind"]): (row["state"], row["fallback_reason"]) for row in slot_rows(signals)}
    assert states[(FRIDAY, "intraday")] == ("scheduled", None)
    assert states[(FRIDAY, "overnight")] == ("fallback_sent", "서비스 중단")
    assert states[(MONDAY, "intraday")] == ("fallback_sent", "준비 중 오류")
