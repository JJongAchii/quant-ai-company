"""Real PostgreSQL; deterministic model/GitHub fixtures, no paid API or external Slack writes."""

import json
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from quant_company.company import PolicyError
from quant_company.contracts import AgentDecision, ProviderResponse, ToolRequest
from quant_company.maintenance.applications import accept_approval
from quant_company.maintenance.requests import status
from quant_company.maintenance.runner import Maintainer

from .conftest import queued_turns
from .test_maintenance import GitHubFixture, config, prepare


class ReviewModel:
    def __init__(self):
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        data = json.loads(request.prompt.split("EVIDENCE JSON:\n", 1)[1])
        assert data["requested_diagnosis"]
        assert data["history"]["evidence"]
        return ProviderResponse(request_id=request.request_id, provider="fixture", decision=AgentDecision(
            say="fixture diagnostic", status="complete", artifacts=[{
                "title": "Diagnostic result", "content": json.dumps({
                    "finding": None, "reason": "검토한 실제 요청 표본에서 수정 근거를 확인하지 못했습니다."}),
            }]))


def runner(company, **options):
    prepare(company)
    role = company.roles["director"]
    company.roles["director"] = role.model_copy(update={
        "tools": role.tools + ["company_history", "maintenance_review", "maintenance_status"]})
    result = Maintainer(company, config(**options), github=GitHubFixture(), provider=ReviewModel())
    result.store.initialize()
    return result


def invoke(company, name, arguments=None, *, event=None, text="개선BOT으로 이전 요청의 개선점을 진단해줘", thread="22.33"):
    project = company.ingest(event_key=event or str(uuid4()), owner="UHUMAN", text=text,
                             channel="CQUANT", thread_ts=thread)
    turn = queued_turns(company, project["project_id"])[0]
    company.prepare_turn(turn)
    response = ProviderResponse(request_id=turn, provider="fixture", decision=AgentDecision(
        say="실제 기록을 조회합니다.", status="continue", tools=[{"name": name, "arguments": arguments or {}}]))
    company.commit_turn(turn, response)
    with company.db.transaction() as conn:
        result = json.loads(conn.execute("SELECT text FROM messages WHERE task_id=%s AND kind='tool'",
                                         (project["task_id"],)).fetchone()["text"])["receipt"]
    followup = queued_turns(company, project["project_id"])[0]
    company.prepare_turn(followup)
    company.commit_turn(followup, ProviderResponse(request_id=followup, provider="fixture", decision=AgentDecision(
        say="접수 또는 조회 결과를 확인했습니다.", status="complete")))
    return project, result, turn, response


@pytest.mark.integration
async def test_human_tool_to_durable_review_to_same_thread_result_and_duplicate(company):
    maintenance = runner(company)
    project, receipt, turn, response = invoke(company, "maintenance_review")
    assert receipt["accepted"] and receipt["source_id"].startswith("company:")
    assert receipt["service"]["enabled"] and receipt["service"]["worker_recently_seen"]
    assert company.commit_turn(turn, response)["duplicate"]
    # A second tool call in the same task is also idempotent, without a second review/model call.
    with company.db.transaction() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id=%s", (project["task_id"],)).fetchone()
        repeated = company._tool(conn, project["project_id"], ToolRequest(name="maintenance_review", arguments={}), task=task)
        assert repeated["request_id"] == receipt["request_id"] and repeated["duplicate"]
    assert (await maintenance.tick())["state"] == "advanced"  # Freeze history.
    assert (await maintenance.tick())["state"] == "advanced"  # Real transaction commits fixture result.
    maintenance.store.report_reviews()
    with company.db.transaction() as conn:
        value = status(conn, company, company._project(conn, project["project_id"]))
        assert value["requests"][0]["state"] == "done"
        notices = conn.execute("SELECT * FROM outbox WHERE project_id=%s AND text LIKE '[개선 담당]%%'",
                               (project["project_id"],)).fetchall()
        assert len(notices) == 2 and all(n["channel"] == "CQUANT" and n["thread_ts"] == "22.33" for n in notices)
        assert sum("점검을 마쳤습니다" in n["text"] for n in notices) == 1
        assert conn.execute("SELECT count(*) AS n FROM maintenance_jobs WHERE kind='review'").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM maintenance_calls").fetchone()["n"] == 1
    assert len(maintenance.provider.requests) == 1 and maintenance.github.published == 0


@pytest.mark.integration
def test_history_is_citable_and_filters_owner_channels_and_secrets(company):
    runner(company)
    for owner, channel, text in [("UNAUTHORIZED", "CQUANT", "OTHER_OWNER_PRIVATE"),
                                  ("UHUMAN", "CHIDDEN", "REVOKED_CHANNEL_PRIVATE"),
                                  ("UHUMAN", "CQUANT", "Bearer " + "s" * 30),
                                  ("UHUMAN", "CQUANT", "ETF 데이터 날짜 조회 요청")]:
        company.ingest(event_key=str(uuid4()), owner=owner, text=text, channel=channel, thread_ts=str(uuid4()))
    # Only the new task's turn should be selected by invoke; the other projects may remain pending.
    project, result, _, _ = invoke(company, "company_history")
    encoded = json.dumps(result, ensure_ascii=False)
    assert "ETF 데이터 날짜" in encoded
    assert "OTHER_OWNER_PRIVATE" not in encoded and "REVOKED_CHANNEL_PRIVATE" not in encoded and "s"*30 not in encoded
    assert any(row.get("omitted") == "possible_secret" for row in result["records"])
    with company.db.transaction() as conn:
        company._check_sources(conn, project["project_id"], [result["source_id"]])
        source = conn.execute("SELECT project_id,synthetic FROM sources WHERE id=%s", (result["source_id"],)).fetchone()
        assert str(source["project_id"]) == project["project_id"] and not source["synthetic"]
    _, limited, _, _ = invoke(company, "company_history", {"limit": 1}, thread="33.44")
    assert limited["truncated"] and len(limited["records"]) == 1


@pytest.mark.integration
async def test_budget_wait_is_reported_once_without_increasing_the_cap(company):
    maintenance = runner(company, max_daily_calls=1)
    project, receipt, _, _ = invoke(company, "maintenance_review")
    await maintenance.tick()
    with company.db.transaction() as conn:
        conn.execute("UPDATE daily_usage SET reserved=%s", (company.settings.company_max_daily_turns,))
    assert await maintenance.tick() == {"state": "deferred", "reason": "daily_model_budget"}
    assert await maintenance.tick() == {"state": "deferred", "reason": "daily_model_budget"}
    with company.db.transaction() as conn:
        notices = conn.execute("SELECT text FROM outbox WHERE project_id=%s", (project["project_id"],)).fetchall()
        assert sum("모델 호출 한도로 대기" in n["text"] for n in notices) == 1
        assert conn.execute("SELECT count(*) AS n FROM maintenance_calls").fetchone()["n"] == 0
        assert conn.execute("SELECT state FROM maintenance_jobs WHERE id=%s", (receipt["request_id"],)).fetchone()["state"] == "triage"
    assert not maintenance.provider.requests


@pytest.mark.integration
async def test_validation_failure_and_revision_change_are_not_silent(company):
    maintenance = runner(company)
    project, receipt, _, _ = invoke(company, "maintenance_review")
    await maintenance.tick()
    maintenance.store.save(receipt["request_id"], "blocked", error="replay_expectation_requires_unknown_source")
    maintenance.store.report_reviews()
    maintenance.store.report_reviews()
    with company.db.transaction() as conn:
        notices = conn.execute("SELECT text FROM outbox WHERE project_id=%s", (project["project_id"],)).fetchall()
        assert sum("검증에서 멈췄습니다" in n["text"] for n in notices) == 1
    # New revision cancels pending analysis rather than running under superseded instructions.
    project2, receipt2, _, _ = invoke(company, "maintenance_review", thread="44.55")
    with company.db.transaction() as conn:
        conn.execute("UPDATE projects SET revision=revision+1 WHERE id=%s", (project2["project_id"],))
    result = await maintenance.tick()
    assert result == {"state": "blocked", "reason": "review_revision_changed"}
    assert not maintenance.provider.requests


@pytest.mark.integration
def test_revoked_owner_and_other_employee_cannot_access_history_or_enqueue(company):
    runner(company)
    project = company.ingest(event_key="forbidden", owner="UHUMAN", text="test", channel="CQUANT", thread_ts="55.66")
    with company.db.transaction() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id=%s", (project["task_id"],)).fetchone()
        wrong = {**task, "agent": "data"}
        with pytest.raises(PolicyError, match="require the director"):
            company._tool(conn, project["project_id"], ToolRequest(name="company_history", arguments={}), task=wrong)
        company.settings.slack_allowed_users = []
        with pytest.raises(PolicyError, match="not authorized"):
            company._tool(conn, project["project_id"], ToolRequest(name="maintenance_review", arguments={}), task=task)


@pytest.mark.integration
def test_offline_service_does_not_claim_to_accept_a_review(company):
    role = company.roles["director"]
    company.roles["director"] = role.model_copy(update={"tools": role.tools + ["maintenance_review"]})
    _, result, _, _ = invoke(company, "maintenance_review")
    assert not result["accepted"] and result["service"]["reason"] == "maintenance_not_installed"


@pytest.mark.integration
def test_existing_case_reports_its_pr_in_request_thread_before_approval_is_possible(company):
    from .test_maintenance_applications import pending

    maintenance = runner(company)
    case_id, _ = pending(company)
    project, receipt, _, _ = invoke(company, "maintenance_review")
    with company.db.transaction() as conn:
        conn.execute("UPDATE maintenance_jobs SET payload=payload || %s WHERE id=%s",
                     (Jsonb({"owners": ["UHUMAN"]}), case_id))
        conn.execute("UPDATE maintenance_jobs SET state='done',receipt=%s WHERE id=%s",
                     (Jsonb({"case_id": case_id, "reason": "Related case already exists"}), receipt["request_id"]))
    maintenance.store.report_reviews()
    kwargs = {"text": "반영해", "owner": "UHUMAN", "channel": "CQUANT", "thread_ts": "22.33",
              "event_key": "new-thread-approval", "event_ts": "25.0"}
    assert accept_approval(company, **kwargs) is None  # A queued notice is not proof of delivery.
    with company.db.transaction() as conn:
        conn.execute("UPDATE outbox SET status='delivered',sent_ts='24.0' WHERE project_id=%s",
                     (project["project_id"],))
    result = accept_approval(company, **kwargs)
    assert result["maintenance_approval"] == "approved"
    assert accept_approval(company, **kwargs)["duplicate"]
