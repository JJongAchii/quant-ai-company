"""Signal turns on real PostgreSQL: the Analyst's tool rounds, the typed commit, format re-asks and fault blocks.

Model answers are fixture decisions and web search is a stub provider. Posts are only inspected as outbox rows; nothing
is sent to Slack. The session days are in the past (Fri 2026-11-13, Mon 2026-11-16) so their snapshot sources pass the
DB now() filters of the context and the source check."""

import json
from datetime import date, datetime, timedelta

from quant_company.briefing.contracts import BRIEFER
from quant_company.company import stable
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.execution import TurnExecutor
from quant_company.signals.contracts import SignalProposal
from quant_company.signals.instructions import final_note
from quant_company.signals.schedule import slots_for
from quant_company.signals.store import (
    ANSWER_SHAPE,
    SignalStore,
    allowed_tools,
    parse_proposal,
    slot_id,
    tools_allowed,
)
from quant_company.web_tools import prepare

from .signals_fixtures import KST, am_project, clock, proposal, signals, snapshot  # noqa: F401

MONDAY, FRIDAY = date(2026, 11, 16), date(2026, 11, 13)
TASK = slot_id(MONDAY, "intraday")  # the signal task ID at project revision 1
SOURCE = "signal:2026-11-16:intraday:snapshot"
CALCULATE = {"name": "calculate", "arguments": {"expression": "6848.5 / 6812.25 - 1"}}
SEARCH = {"name": "web_search", "arguments": {"query": "코스피 오늘 일정"}}
READ = {"name": "web_read", "arguments": {"url": "https://www.example.com/krx-notice"}}


def slot(day=MONDAY, kind="intraday"):
    return next(item for item in slots_for(day) if item.kind == kind)


def ask(company, clock, day=MONDAY, kind="intraday"):  # noqa: F811
    """At the slot's snapshot time: schedule, freeze the recorded snapshot and open the task. Returns its first turn."""
    at = slot(day, kind).snapshot_at
    clock["at"] = at
    store = SignalStore(company)
    store.schedule(at)
    [row] = [row for row in store.due_asks(at) if row["kind"] == kind]
    assert store.ask(row, snapshot(kind, day), at)
    return queued(company, slot_id(day, kind))


def queued(company, task_id):
    row = one(company, "SELECT id FROM turns WHERE task_id=%s AND status='queued'", task_id)
    return str(row["id"]) if row else None


def one(company, query, *args):
    with company.db.transaction() as conn:
        return conn.execute(query, args).fetchone()


def rows(company, query, *args):
    with company.db.transaction() as conn:
        return conn.execute(query, args).fetchall()


def signal(company, day=MONDAY, kind="intraday"):
    return one(company, "SELECT * FROM trading_signals WHERE id=%s", slot_id(day, kind))


def posts(company):
    return rows(company, """SELECT m.id,m.author,m.text,o.agent,o.status FROM messages m LEFT JOIN outbox o ON o.id=m.id
        WHERE m.kind='trading_signal' ORDER BY m.created_at""")


def answer(turn, value=None, say="미국 선물이 강해 롱으로 봅니다."):
    """The Analyst's final answer: one artifact holding the SignalProposal JSON."""
    content = json.dumps(value or proposal(session_day=str(MONDAY)), ensure_ascii=False)
    return ProviderResponse(request_id=turn, decision=AgentDecision(
        status="complete", say=say, artifacts=[{"title": "매매 신호", "content": content}]))


def tools(turn, *requests, say=""):
    return ProviderResponse(request_id=turn, decision=AgentDecision(status="continue", say=say, tools=list(requests)))


def test_on_time_commit_posts_once(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    turn = ask(signals, clock)
    clock["at"] += timedelta(minutes=20)  # 07:50 KST, before the 08:30 send time
    assert signals.prepare_turn(turn)["state"] == "ready"
    response = answer(turn)
    assert signals.commit_turn(turn, response) == {"state": "completed", "task_status": "completed", "duplicate": False}
    assert signals.commit_turn(turn, response) == {"state": "completed", "duplicate": True}
    [post] = posts(signals)
    assert post["text"].startswith("📈 장중 신호 · 11월 16일(월)  시가 09:00 진입 → 종가 15:30 청산\n롱 · 확신도 60%\n")
    assert (str(post["id"]), post["author"], post["agent"], post["status"]) == (
        stable(f"trading-signal-post:{TASK}:analyst"), BRIEFER, BRIEFER, "pending")
    row = signal(signals)
    assert (row["state"], row["timing"], row["committed_at"], str(row["analyst_message_id"])) == (
        "committed", "on_time", clock["at"], str(post["id"]))
    assert row["proposal"] == SignalProposal.model_validate(proposal(session_day=str(MONDAY))).model_dump(mode="json")
    task = one(signals, "SELECT status,result FROM tasks WHERE id=%s", TASK)
    assert (task["status"], task["result"]) == ("completed", response.decision.say)
    assert one(signals, "SELECT status,response FROM turns WHERE id=%s", turn) == {
        "status": "completed", "response": response.model_dump(mode="json")}
    assert one(signals, "SELECT count(*) n FROM events WHERE kind='turn_completed' AND detail->>'turn_id'=%s",
               turn)["n"] == 1
    assert queued(signals, TASK) is None


def test_tool_round_say_not_published(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    turn = ask(signals, clock)
    prompt = signals.prepare_turn(turn)["request"]["prompt"]
    assert f"Allowed tools: {signals.roles[BRIEFER].tools}\n" in prompt
    assert signals.commit_turn(turn, tools(turn, CALCULATE, say="확인 중"))["task_status"] == "pending"
    [say] = rows(signals, "SELECT id,text FROM messages WHERE task_id=%s AND kind='answer'", TASK)
    assert say["text"] == "확인 중"
    assert rows(signals, "SELECT id FROM outbox") == []
    assert one(signals, "SELECT count(*) n FROM messages WHERE task_id=%s AND author='tool:calculate'", TASK)["n"] == 1
    assert queued(signals, TASK) == stable(f"turn:{TASK}:2")


async def test_tools_after_cutoff_are_refused(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    turn = ask(signals, clock)
    clock["at"] = slot().tools_until  # 08:15 KST

    class Provider:
        prompts, searches = [], []

        async def run(self, request):
            if request.web_search:
                self.searches.append(request.request_id)
            self.prompts.append(request.prompt)
            return tools(request.request_id, SEARCH)

        async def cancel(self, request_id):
            return "cancelled"

    provider = Provider()
    assert (await TurnExecutor(signals, provider).execute(turn))["state"] == "completed"
    assert "Allowed tools: []\n" in provider.prompts[0]
    assert provider.searches == [] and rows(signals, "SELECT id FROM web_requests") == []
    [note] = rows(signals, "SELECT author,recipient,text FROM messages WHERE kind='signal_note'")
    assert note == {"author": "service:signals", "recipient": BRIEFER, "text": final_note("tools_not_allowed")}
    assert rows(signals, "SELECT id FROM outbox") == []
    row = signal(signals)
    assert (row["format_failures"], row["last_error"], row["state"]) == (1, "tools_not_allowed", "asked")
    assert queued(signals, TASK) == stable(f"turn:{TASK}:2")


def test_search_caps(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    turn = ask(signals, clock)
    signals.prepare_turn(turn)
    role_tools = signals.roles[BRIEFER].tools

    def receipts(name, count):
        """Tool messages of earlier tool rounds of this signal task."""
        with signals.db.transaction() as conn:
            project = signals._project(conn, signal(signals)["project_id"])
            for _ in range(count):
                signals._message(conn, project, TASK, f"tool:{name}", "tool", json.dumps({"receipt": {"ok": True}}),
                                 BRIEFER)

    def check(*requests):
        with signals.db.transaction() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=%s", (TASK,)).fetchone()
            decision = AgentDecision(status="continue", say="", tools=list(requests))
            return allowed_tools(conn, task, role_tools), tools_allowed(conn, task, decision)

    receipts("web_search", 1)
    allowed, both = check(SEARCH, {"name": "web_search", "arguments": {"query": "반도체 수출"}})
    assert "web_search" in allowed and not both  # one search left: two in one turn would go over
    receipts("web_search", 1)
    allowed, third = check(SEARCH)
    assert "web_search" not in allowed and "web_read" in allowed and not third
    assert prepare(signals, turn, AgentDecision(status="continue", say="", tools=[SEARCH])) == []
    assert rows(signals, "SELECT id FROM web_requests") == []
    receipts("web_read", 3)
    allowed, fourth = check(READ)
    assert "web_read" in allowed and fourth
    receipts("web_read", 1)
    allowed, fifth = check(READ)
    assert allowed == [name for name in role_tools if name not in {"web_search", "web_read"}] and not fifth


def test_three_tool_turns_then_final_answer(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    turn = ask(signals, clock)
    for _ in range(3):
        assert "Allowed tools: []" not in signals.prepare_turn(turn)["request"]["prompt"]
        signals.commit_turn(turn, tools(turn, CALCULATE))
        turn = queued(signals, TASK)
    assert "Allowed tools: []\n" in signals.prepare_turn(turn)["request"]["prompt"]
    signals.commit_turn(turn, tools(turn, CALCULATE))  # a fourth tool round is a format error
    assert one(signals, "SELECT count(*) n FROM messages WHERE author='tool:calculate'")["n"] == 3
    assert signal(signals)["last_error"] == "tools_not_allowed"
    turn = queued(signals, TASK)
    signals.prepare_turn(turn)
    signals.commit_turn(turn, answer(turn))
    assert (signal(signals)["state"], len(posts(signals))) == ("committed", 1)


def test_foreign_source_is_a_format_error(signals, clock):  # noqa: F811
    am_project(signals, FRIDAY, thread_ts="100.013")
    ask(signals, clock, FRIDAY)  # the previous session's signal, in that day's thread
    am_project(signals, MONDAY)
    turn = ask(signals, clock)
    signals.prepare_turn(turn)
    foreign = "signal:2026-11-13:intraday:snapshot"
    value = proposal(session_day=str(MONDAY))
    value["reasons"] = [{"text": "지난 금요일 스냅샷보다 선물이 높다", "source_ids": [SOURCE, foreign]}]
    assert signals.commit_turn(turn, answer(turn, value))["task_status"] == "pending"
    row = signal(signals)
    assert (row["format_failures"], row["last_error"], row["proposal"], row["state"]) == (
        1, f"unapproved_sources: {foreign}", None, "asked")
    assert posts(signals) == [] and rows(signals, "SELECT id FROM outbox") == []
    assert queued(signals, TASK) == stable(f"turn:{TASK}:2")


def test_third_format_failure_posts_say_unparsed(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    turn = ask(signals, clock)
    say = "롱으로 봅니다. 미국 선물이 강합니다."
    for attempt in range(3):
        prompt = signals.prepare_turn(turn)["request"]["prompt"]
        assert ("Allowed tools: []\n" in prompt) == (attempt > 0)
        signals.commit_turn(turn, ProviderResponse(request_id=turn, decision=AgentDecision(status="complete", say=say)))
        turn = queued(signals, TASK)
    assert turn is None
    [post] = posts(signals)
    assert post["text"].startswith("📈 장중 신호 · 형식 확인 실패 · 채점 제외\n")
    assert post["text"] == "\n".join(["📈 장중 신호 · 형식 확인 실패 · 채점 제외", "시가 09:00 진입 → 종가 15:30 청산", say])
    assert (str(post["id"]), post["agent"]) == (stable(f"trading-signal-post:{TASK}:unparsed"), BRIEFER)
    row = signal(signals)
    assert (row["unparsed"], row["format_failures"], row["last_error"], str(row["unparsed_message_id"])) == (
        True, 3, ANSWER_SHAPE, str(post["id"]))
    # Nothing to grade later: grading reads committed proposals only.
    assert (row["state"], row["proposal"], row["timing"], row["committed_at"]) == ("asked", None, None, None)
    assert len(rows(signals, "SELECT id FROM messages WHERE kind='signal_note'")) == 2
    assert one(signals, "SELECT status,result FROM tasks WHERE id=%s", TASK) == {"status": "completed", "result": say}


def test_daily_cap_does_not_hold_signals(signals, clock):  # noqa: F811
    project = am_project(signals, MONDAY)
    turn = ask(signals, clock)
    work = signals.ingest(event_key="slack:cap-question", text="오늘 반도체 업종은 어때?", owner="UHUMAN",
                          agent=BRIEFER, project_id=str(project["id"]))
    signals.settings.company_max_daily_turns = 1
    with signals.db.transaction() as conn:  # the day's company cap is used up
        conn.execute("""INSERT INTO daily_usage(day,reserved) VALUES (CURRENT_DATE,1)
            ON CONFLICT(day) DO UPDATE SET reserved=1""")
    assert signals.prepare_turn(queued(signals, work["task_id"])) == {
        "state": "defer", "seconds": 3600, "reason": "daily_turn_budget"}
    assert signals.prepare_turn(turn)["state"] == "ready"
    # Its web search is not refused by the cap either; both still count toward the day's turns.
    assert len(prepare(signals, turn, AgentDecision(status="continue", say="", tools=[SEARCH]))) == 1
    assert one(signals, "SELECT reserved FROM daily_usage WHERE day=CURRENT_DATE")["reserved"] == 3


def test_routing_hold_does_not_hold_signal(signals, clock):  # noqa: F811
    project = am_project(signals, MONDAY)
    turn = ask(signals, clock)
    signals.ingest(event_key="slack:routing", text="이 신호 쉽게 설명해줘", owner="UHUMAN", agent="director",
                   project_id=str(project["id"]), interpret=True)
    question = signals.ingest(event_key="slack:held-question", text="오늘 환율은?", owner="UHUMAN", agent=BRIEFER,
                              project_id=str(project["id"]))
    assert signals.prepare_turn(queued(signals, question["task_id"]))["reason"] == "owner_input_or_project_pause"
    assert signals.prepare_turn(turn)["state"] == "ready"
    assert signals.commit_turn(turn, answer(turn))["task_status"] == "completed"
    assert (signal(signals)["state"], len(posts(signals))) == ("committed", 1)


def test_owner_question_runs_after_signal(signals, clock):  # noqa: F811
    project = am_project(signals, MONDAY)
    turn = ask(signals, clock)
    signals.prepare_turn(turn)
    signals.commit_turn(turn, tools(turn, CALCULATE, say="확인 중"))
    question = signals.ingest(event_key="slack:owner-question", text="오늘 반도체 업종은 어때?", owner="UHUMAN",
                              agent=BRIEFER, project_id=str(project["id"]))
    owner_turn, turn = queued(signals, question["task_id"]), queued(signals, TASK)
    assert signals.due_turns() == [turn, owner_turn]  # the signal goes first
    signals.prepare_turn(turn)
    signals.commit_turn(turn, answer(turn))
    assert signals.due_turns() == [owner_turn]
    assert signals.prepare_turn(owner_turn)["state"] == "ready"
    # Only the signal reached the outbox; the tool round's say stays in the records.
    assert [row["text"].split("\n")[0] for row in rows(signals, "SELECT text FROM outbox")] == [
        "📈 장중 신호 · 11월 16일(월)  시가 09:00 진입 → 종가 15:30 청산"]
    assert one(signals, "SELECT count(*) n FROM messages WHERE kind='answer' AND text='확인 중'")["n"] == 1


def test_block_turn_posts_nothing_for_signal(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    turn = ask(signals, clock)
    signals.prepare_turn(turn)
    signals.block_turn(turn, "auth")
    assert rows(signals, "SELECT id FROM messages WHERE task_id=%s AND kind='status'", TASK) == []
    assert rows(signals, "SELECT id FROM outbox") == []
    row = signal(signals)
    assert (row["last_error"], row["retry_at"]) == ("auth", clock["at"] + timedelta(minutes=5))
    assert one(signals, "SELECT status,error FROM turns WHERE id=%s", turn) == {"status": "blocked", "error": "auth"}
    assert one(signals, "SELECT status,error FROM tasks WHERE id=%s", TASK) == {"status": "blocked", "error": "auth"}
    assert one(signals, "SELECT count(*) n FROM events WHERE kind='turn_blocked' AND detail->>'turn_id'=%s",
               turn)["n"] == 1


def test_turn_limit_posts_nothing_for_signal(signals, clock):  # noqa: F811
    signals.settings.company_max_task_turns = 1
    am_project(signals, MONDAY)
    turn = ask(signals, clock)
    signals.prepare_turn(turn)
    signals.commit_turn(turn, tools(turn, CALCULATE))
    assert one(signals, "SELECT status,error FROM tasks WHERE id=%s", TASK) == {
        "status": "blocked", "error": "task_turn_limit"}
    assert rows(signals, "SELECT id FROM messages WHERE kind='status'") == []
    assert rows(signals, "SELECT id FROM outbox") == []


def test_late_commit_after_interim_post(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    turn = ask(signals, clock)
    signals.prepare_turn(turn)
    with signals.db.transaction() as conn:  # the interim post went out at 08:28
        conn.execute("""UPDATE trading_signals SET state='fallback_sent',fallback_reason='모델 혼잡',fallback_sent_at=%s,
            fallback_message_id=%s WHERE id=%s""", (slot().fallback_at, stable(f"trading-signal-post:{TASK}:fallback"),
                                                    TASK))
    clock["at"] = datetime(2026, 11, 16, 8, 41, tzinfo=KST)
    signals.commit_turn(turn, answer(turn, proposal(session_day=str(MONDAY), direction="short")))
    [post] = posts(signals)
    assert post["text"].split("\n")[:3] == [
        "📈 장중 신호 · 늦음 08:41 · 임시 신호와 반대", "시가 09:00 진입 → 종가 15:30 청산", "숏 · 확신도 60%"]
    row = signal(signals)
    assert (row["state"], row["timing"], row["committed_at"]) == ("committed_late", "late", clock["at"])


def test_commit_after_entry_and_after_exit(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    intraday = ask(signals, clock)
    overnight = ask(signals, clock, kind="overnight")
    with signals.db.transaction() as conn:  # the worker was down at 08:28: no interim post, the reason recorded
        conn.execute("UPDATE trading_signals SET state='fallback_sent',fallback_reason='서비스 중단' WHERE id=%s", (TASK,))
    clock["at"] = datetime(2026, 11, 16, 9, 5, tzinfo=KST)
    signals.prepare_turn(intraday)
    signals.commit_turn(intraday, answer(intraday))
    [post] = posts(signals)
    assert post["text"].split("\n")[:3] == ["📈 장중 신호 · 늦음 09:05", "시가 09:00 진입 → 종가 15:30 청산", "롱 · 확신도 60%"]
    assert (signal(signals)["state"], signal(signals)["timing"]) == ("committed_late", "after_entry")
    # After the exit (the next session's open) the signal is recorded, not posted.
    clock["at"] = datetime(2026, 11, 17, 9, 1, tzinfo=KST)
    signals.prepare_turn(overnight)
    signals.commit_turn(overnight, answer(overnight, proposal(kind="overnight", session_day=str(MONDAY))))
    night = signal(signals, kind="overnight")
    assert (night["state"], night["timing"], night["analyst_message_id"], night["proposal"]["kind"]) == (
        "committed", "after_entry", None, "overnight")
    assert len(posts(signals)) == 1


def test_settled_slot_keeps_its_first_outcome(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    turn = ask(signals, clock)
    signals.prepare_turn(turn)
    signals.commit_turn(turn, answer(turn))
    first = signal(signals)
    second = stable("trading-signal:2026-11-16:intraday:r2")
    with signals.db.transaction() as conn:  # a second task for the same slot answers later
        project = signals._project(conn, first["project_id"])
        signals._new_task(conn, project, BRIEFER, "신호 다시", task_id=second, priority=-100, kind="trading_signal")
        conn.execute("UPDATE trading_signals SET task_id=%s WHERE id=%s", (second, TASK))
    turn = queued(signals, second)
    signals.prepare_turn(turn)
    late = answer(turn, proposal(session_day=str(MONDAY), direction="short"))
    assert signals.commit_turn(turn, late)["task_status"] == "completed"
    row = signal(signals)
    assert (row["proposal"], row["timing"], row["committed_at"]) == (
        first["proposal"], first["timing"], first["committed_at"])
    assert len(posts(signals)) == 1


def test_parse_proposal_reasons():
    row = {"kind": "intraday", "session_day": MONDAY}
    approved = {SOURCE, "web:kospi-news"}

    def reason(response):
        try:
            parse_proposal(response, row, approved, SOURCE)
        except ValueError as exc:
            return str(exc)

    valid = proposal(session_day=str(MONDAY))
    assert parse_proposal(answer("t", valid), row, approved, SOURCE) == SignalProposal.model_validate(valid)
    not_json, cited_artifact = answer("t"), answer("t")
    not_json.decision.artifacts[0].content = "롱"
    cited_artifact.decision.artifacts[0].source_ids = [SOURCE]
    cases = [
        ("tools_not_allowed", tools("t", CALCULATE)),
        (ANSWER_SHAPE, ProviderResponse(request_id="t", decision=AgentDecision(status="complete", say="롱"))),
        (ANSWER_SHAPE, cited_artifact),
        ("invalid_json: Expecting value", not_json),
        ("invalid_proposal: confidence: Input should be less than or equal to 90", answer("t", {**valid, "confidence": 95})),
        ("wrong_slot: expected intraday 2026-11-16", answer("t", proposal(session_day="2026-11-13"))),
        ("unapproved_sources: lake:unknown", answer("t", {**valid, "source_ids": [SOURCE, "lake:unknown"]})),
        (f"snapshot_not_cited: {SOURCE}", answer("t", {**valid, "source_ids": ["web:kospi-news"]})),
    ]
    assert [reason(response) for _, response in cases] == [expected for expected, _ in cases]
