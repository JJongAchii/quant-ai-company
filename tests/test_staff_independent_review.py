import json

import pytest
from psycopg.types.json import Jsonb

from quant_company.company import load_roles
from quant_company.contracts import AgentDecision, ProviderFault, ProviderResponse
from quant_company.maintenance.policy import writable
from quant_company.staff.independent_review import IndependentReviewRunner, IndependentReviewStore
from quant_company.staff.review_contract import REVIEW_MODEL
from quant_company.staff.runner import StaffRunner
from quant_company.staff.store import StaffStore, status


@pytest.fixture
def completed(company):
    company.roles = load_roles(company.settings)
    company.settings.company_staff_review_enabled = True
    company.settings.model_runtime_token = "unused-fixture-token"
    staff = StaffStore(company)
    identity = staff.enqueue("financial_strategist", "UHUMAN")
    ready = staff.prepare()
    with company.db.transaction() as conn:
        key = conn.execute("SELECT answer_key FROM staff_runs WHERE id=%s", (identity,)).fetchone()["answer_key"]
    answer = {"metrics": key["metrics"], "reject_ids": key["reject_ids"],
              "explanation": "수치 계산은 문제의 가정을 따랐으며 실제 미래 성과로 일반화하지 않습니다."}
    staff.commit(identity, ProviderResponse(request_id=ready["request"]["request_id"], provider="fixture",
        decision=AgentDecision(status="complete", say="fixture only",
                               artifacts=[{"title": "answer", "content": json.dumps(answer)}])))
    return company, identity, answer


def response(request):
    item = {"assessment": "concern", "explanation": "Synthetic review fixture: this is not a real evaluator result.",
            "evidence_quotes": ["문제의 가정을 따랐으며"]}
    result = {**{name: item for name in ("grounding", "reasoning", "assumptions", "limitations")},
              "conclusion": "Synthetic independent commentary cannot override an objective grade."}
    return ProviderResponse(request_id=request["request_id"], provider="claude", usage={"actual_model": REVIEW_MODEL},
        decision=AgentDecision(status="complete", say="fixture", artifacts=[{"title": "review",
            "content": json.dumps(result, ensure_ascii=False)}]))


def test_blinded_durable_review_and_advisory_commit(completed):
    company, identity, _ = completed
    store = IndependentReviewStore(company)
    prepared = store.prepare()
    request = prepared["request"]
    assert prepared["state"] == "ready" and request == IndependentReviewStore(company).prepare()["request"]
    for forbidden in ("answer_key", "relative_tolerance", "objective_passed", "UHUMAN", "gpt-", "financial_strategist"):
        assert forbidden not in request["prompt"]
    with company.db.transaction() as conn:
        grade = conn.execute("SELECT grade FROM staff_runs WHERE id=%s", (identity,)).fetchone()["grade"]
    result = response(request)
    assert store.commit(result)["state"] == "completed"
    assert store.commit(result)["duplicate"]
    assert store.prepare()["state"] == "idle"
    with company.db.transaction() as conn:
        assert conn.execute("SELECT grade FROM staff_runs WHERE id=%s", (identity,)).fetchone()["grade"] == grade
        report = status(conn, company, "UHUMAN")["independent_review"]
        assert report["recent"][0]["result"]["objective_grade_unchanged"]
        assert report["recent"][0]["result"]["calibration_status"] == "not_yet_calibrated"
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0
    changed = result.model_copy(update={"usage": {"actual_model": "other"}})
    with pytest.raises(ValueError, match="replaced"):
        store.commit(changed)


@pytest.mark.parametrize("change", ["wrong_model", "wrong_provider", "invented_quote", "tool_action"])
def test_invalid_commentary_cannot_enter_results(completed, change):
    company, _, _ = completed
    store = IndependentReviewStore(company)
    result = response(store.prepare()["request"])
    if change == "wrong_model":
        result.usage["actual_model"] = "claude-sonnet-5"
    elif change == "wrong_provider":
        result.provider = "codex"
    elif change == "invented_quote":
        result.decision.artifacts[0].content = result.decision.artifacts[0].content.replace(
            "문제의 가정을 따랐으며", "출처에 없는 가짜 인용문")
    else:
        result.decision = AgentDecision(status="continue", say="unsafe",
                                        tools=[{"name": "calculate", "arguments": {}}])
    with pytest.raises(ValueError):
        store.commit(result)
    with company.db.transaction() as conn:
        assert conn.execute("SELECT result FROM staff_independent_reviews").fetchone()["result"] is None


@pytest.mark.parametrize("change", ["disputed", "answer", "request", "permission"])
def test_source_and_authorization_changes_block_commit(completed, change):
    company, identity, _ = completed
    store = IndependentReviewStore(company)
    result = response(store.prepare()["request"])
    with company.db.transaction() as conn:
        if change == "disputed":
            conn.execute("UPDATE staff_runs SET state='disputed' WHERE id=%s", (identity,))
        elif change == "answer":
            conn.execute("UPDATE staff_runs SET final_answer=%s WHERE id=%s", (Jsonb({"changed": True}), identity))
        elif change == "request":
            conn.execute("UPDATE staff_independent_reviews SET request=%s", (Jsonb({"altered": True}),))
        else:
            company.settings.slack_allowed_users = []
    assert store.commit(result)["state"] == "blocked"


def test_later_dispute_hides_prior_review(completed):
    company, identity, _ = completed
    store = IndependentReviewStore(company)
    store.commit(response(store.prepare()["request"]))
    with company.db.transaction() as conn:
        conn.execute("UPDATE staff_runs SET state='disputed' WHERE id=%s", (identity,))
        review = status(conn, company, "UHUMAN")["independent_review"]["recent"][0]
        assert review["source_state"] == "disputed" and review["result"] is None


async def test_quota_defers_without_affecting_grade_or_new_identity(completed):
    company, identity, _ = completed
    calls = []

    class Quota:
        async def run(self, request):
            calls.append(request.request_id)
            raise ProviderFault("quota", "fixture", 900)

    runner = IndependentReviewRunner(company, Quota())
    assert (await runner.tick())["reason"] == "claude_quota"
    assert (await runner.tick())["state"] == "idle"
    assert len(calls) == 1
    with company.db.transaction() as conn:
        run = conn.execute("SELECT state,grade FROM staff_runs WHERE id=%s", (identity,)).fetchone()
        assert run["state"] == "completed" and run["grade"]["objective_passed"]
        conn.execute("UPDATE staff_independent_reviews SET next_at=now()")
    assert IndependentReviewStore(company).prepare()["request"]["request_id"] == calls[0]


def test_deferred_previous_day_review_consumes_todays_limit(completed):
    company, _, _ = completed
    company.settings.staff_review_daily_limit = 1
    store = IndependentReviewStore(company)
    ready = store.prepare()
    with company.db.transaction() as conn:
        conn.execute("UPDATE staff_independent_reviews SET schedule_day=schedule_day-1")
    resumed = store.prepare()
    assert resumed["request"] == ready["request"]
    store.commit(response(resumed["request"]))
    assert store.prepare() == {"state": "idle", "reason": "review_daily_limit"}


async def test_staff_loop_runs_review_only_when_exercises_and_owner_work_idle(completed, monkeypatch):
    company, _, _ = completed
    company.settings.company_staff_development_enabled = True
    calls = []

    class Reviewer:
        async def run(self, request):
            calls.append(request.request_id)
            return response(request.model_dump())

    runner = StaffRunner(company, review_provider=Reviewer())
    monkeypatch.setattr(runner.store, "schedule", lambda: None)
    company.ingest(event_key="owner-first", text="현재 요청 먼저 처리", owner="UHUMAN", agent="director")
    assert (await runner.tick())["state"] == "defer" and not calls
    with company.db.transaction() as conn:
        conn.execute("UPDATE projects SET status='completed'")
    assert (await runner.tick())["state"] == "completed" and len(calls) == 1


def test_review_contract_and_grader_are_not_bot_writable():
    for path in ("staff/independent_review.py", "staff/review_contract.py", "staff/progress.py",
                 "providers/claude_runner.py"):
        assert not writable("src/quant_company/" + path)
