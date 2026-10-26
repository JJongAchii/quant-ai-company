"""In-process loops (ADR 0049): retries, timeouts, lanes, the turn loop and the video question loop."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from quant_company import loops as loop_module
from quant_company.company import Company
from quant_company.contracts import ProviderFault
from quant_company.execution import FixtureProvider, TurnExecutor
from quant_company.loops import QUESTION_TRIES, QUESTION_WAIT, Loop, QuestionLoop, TurnLoop, attempt, run

from .conftest import queued_turns


async def test_attempt_retries_with_backoff_and_reports_failure(monkeypatch):
    calls, waits = [], []

    async def fail():
        calls.append(1)
        raise RuntimeError("synthetic")

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(loop_module.asyncio, "sleep", sleep)
    assert await attempt("fail", fail, timeout=1, attempts=3, retry_seconds=5) is None
    assert len(calls) == 3 and waits == [5, 10]


async def test_attempt_returns_the_first_success_and_cancels_a_slow_tick():
    results = iter([RuntimeError("lost worker"), {"state": "done"}])

    async def flaky():
        value = next(results)
        if isinstance(value, Exception):
            raise value
        return value

    assert await attempt("flaky", flaky, timeout=1, attempts=2, retry_seconds=0) == {"state": "done"}
    cancelled = asyncio.Event()

    async def slow():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    assert await attempt("slow", slow, timeout=0.05) is None
    assert cancelled.is_set()


async def test_loops_in_one_lane_never_tick_together():
    lane, running, overlaps = asyncio.Semaphore(1), set(), []

    def tick(name):
        async def inner():
            running.add(name)
            overlaps.append(len(running))
            await asyncio.sleep(0.01)
            running.discard(name)
            return {"state": "ok"}
        return inner

    tasks = [asyncio.create_task(run(Loop(name, tick(name), timeout=1, delay=0, lane=lane))) for name in "abc"]
    await asyncio.sleep(0.2)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert len(overlaps) > 6 and max(overlaps) == 1


async def test_the_wait_after_a_tick_sees_its_result_or_none_after_failures():
    seen, results = [], iter([{"state": "collected"}, RuntimeError("down")])

    async def tick():
        value = next(results)
        if isinstance(value, Exception):
            raise value
        return value

    def delay(result):
        seen.append(result)
        if len(seen) == 2:
            raise asyncio.CancelledError
        return 0

    with pytest.raises(asyncio.CancelledError):
        await run(Loop("news", tick, timeout=1, delay=delay))
    assert seen == [{"state": "collected"}, None]


async def drive(turns, company, project_id, seconds=30):
    async with asyncio.timeout(seconds):
        while True:
            await turns.tick()
            state = await asyncio.to_thread(company.project_state, project_id)
            assert not any(task["status"] == "blocked" for task in state["tasks"]), state
            if state["tasks"] and all(task["status"] == "completed" for task in state["tasks"]):
                return state
            await asyncio.sleep(0.05)


@pytest.mark.integration
async def test_turn_loop_runs_a_four_role_collaboration_from_postgres(company):
    result = company.ingest(event_key="loop-collaboration", text="Synthetic collaboration", owner="user")
    turns = TurnLoop(company, TurnExecutor(company, FixtureProvider()), asyncio.Semaphore(1))
    state = await drive(turns, company, result["project_id"])
    assert len(state["tasks"]) == 4 and len(state["artifacts"]) == 4 and len(state["turns"]) == 7


@pytest.mark.integration
async def test_a_restarted_worker_runs_a_turn_once_it_is_due(company):
    result = company.ingest(event_key="delayed", text="Delayed fixture", owner="user", agent="data")
    turn_id = queued_turns(company, result["project_id"])[0]
    with company.db.transaction() as conn:
        conn.execute("UPDATE turns SET due_at=now()+interval '1 second' WHERE id=%s", (turn_id,))
    first = TurnLoop(company, TurnExecutor(company, FixtureProvider()), asyncio.Semaphore(1))
    assert await first.tick() == {"state": "idle"}  # not due yet, nothing in memory to lose
    restarted = Company(company.settings, company.roles)
    loop = TurnLoop(restarted, TurnExecutor(restarted, FixtureProvider()), asyncio.Semaphore(1))
    state = await drive(loop, restarted, result["project_id"], seconds=10)
    assert len(state["artifacts"]) == 1 and state["tasks"][0]["status"] == "completed"


class Provider(FixtureProvider):
    def __init__(self, fault=None):
        self.fault, self.calls = fault, 0

    async def run(self, request):
        self.calls += 1
        if self.fault:
            raise self.fault
        return await super().run(request)


@pytest.mark.integration
async def test_a_deferred_turn_waits_in_the_database_and_an_account_change_wakes_it(company):
    result = company.ingest(event_key="quota-wait", text="Synthetic task", owner="UHUMAN", agent="data")
    turn_id = queued_turns(company, result["project_id"])[0]
    provider = Provider(ProviderFault("quota", "synthetic quota", 3600))
    turns = TurnLoop(company, TurnExecutor(company, provider), asyncio.Semaphore(1))
    assert (await turns.tick())["result"] == "defer"
    assert company.project_state(result["project_id"])["turns"][0]["status"] == "waiting"
    assert await turns.tick() == {"state": "idle"} and provider.calls == 1
    # What accounts.resume_quota_waits does for a switched account, then the wake it records.
    with company.db.transaction() as conn:
        conn.execute("UPDATE turns SET due_at=now() WHERE id=%s AND status='waiting' AND error='quota'", (turn_id,))
        conn.execute("UPDATE runtime_control SET paused_until=NULL,reason=NULL WHERE id=1 AND reason='quota'")
    turns.wake(turn_id)
    provider.fault = None
    state = await drive(turns, company, result["project_id"], seconds=10)
    assert provider.calls == 2 and state["turns"][0]["id"] == turn_id


@pytest.mark.integration
async def test_a_failing_turn_is_tried_three_times_quickly_then_after_a_minute(company, monkeypatch):
    result = company.ingest(event_key="lost-runtime", text="Synthetic task", owner="UHUMAN", agent="data")
    turn_id = queued_turns(company, result["project_id"])[0]
    clock = [1000.0]
    monkeypatch.setattr(loop_module.time, "monotonic", lambda: clock[0])

    class Lost:
        calls = 0

        async def execute(self, turn):
            self.calls += 1
            raise ConnectionError("runtime restarted")

    executor = Lost()
    turns = TurnLoop(company, executor, asyncio.Semaphore(1))
    waits = []
    for _ in range(3):
        assert (await turns.tick())["state"] == "failed"
        waits.append(turns.next_at[turn_id] - clock[0])
        clock[0] = turns.next_at[turn_id]
    assert waits == [2, 4, 60] and executor.calls == 3


class Analytics:
    def __init__(self, questions, enabled=True, answers=()):
        self.answers, self.calls = list(answers), []
        self.store = SimpleNamespace(enabled=lambda: enabled, pending_questions=lambda: questions)

    async def answer(self, question_id):
        self.calls.append(("answer", question_id))
        return self.answers.pop(0)

    async def give_up(self, question_id):
        self.calls.append(("give_up", question_id))
        return "expired"


async def test_a_waiting_question_is_asked_again_after_five_minutes(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(loop_module.time, "monotonic", lambda: clock[0])
    question = {"id": "q1", "created_at": datetime.now(UTC)}
    analytics = Analytics([question], answers=["waiting", "answered"])
    questions = QuestionLoop(analytics, asyncio.Semaphore(1))
    assert await questions.tick() == {"state": "waiting", "question": "q1"}
    assert await questions.tick() == {"state": "idle"}
    clock[0] = QUESTION_WAIT.total_seconds()
    assert await questions.tick() == {"state": "answered", "question": "q1"}
    assert analytics.calls == [("answer", "q1"), ("answer", "q1")]
    assert await questions.tick() == {"state": "idle"}  # finished in this process even if still listed


async def test_a_question_is_closed_after_seven_hours_of_tries_and_nothing_runs_while_disabled(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(loop_module.time, "monotonic", lambda: clock[0])
    # Created long ago (the video worker was down): it still gets its tries before it is closed.
    old = {"id": "q2", "created_at": datetime.now(UTC) - timedelta(days=1)}
    analytics = Analytics([old], answers=["waiting"])
    questions = QuestionLoop(analytics, asyncio.Semaphore(1))
    assert await questions.tick() == {"state": "waiting", "question": "q2"}
    clock[0] = (QUESTION_WAIT * QUESTION_TRIES).total_seconds()
    assert await questions.tick() == {"state": "expired", "question": "q2"}
    assert analytics.calls == [("answer", "q2"), ("give_up", "q2")]
    disabled = Analytics([old], enabled=False)
    assert await QuestionLoop(disabled, asyncio.Semaphore(1)).tick() == {"state": "disabled"}
    assert disabled.calls == []


async def test_a_wait_that_cannot_be_computed_does_not_stop_the_loop(monkeypatch):
    waits = []

    async def sleep(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            raise asyncio.CancelledError

    async def tick():
        return {"no_state": True}

    monkeypatch.setattr(loop_module.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await run(Loop("research", tick, timeout=None, delay=lambda result: result["state"]))
    assert waits == [60, 60]


def test_question_patience_matches_the_former_workflow():
    assert QUESTION_WAIT * QUESTION_TRIES == timedelta(hours=7)
