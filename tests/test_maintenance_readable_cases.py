"""Real PostgreSQL; synthetic Slack/GitHub. What the owner reads in improvements, not internal codes."""

from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from quant_company.company import fingerprint
from quant_company.maintenance import cases, explain
from quant_company.maintenance.applications import accept_approval
from quant_company.maintenance.reporting import Reporter
from quant_company.maintenance.requests import status
from quant_company.maintenance.runner import Maintainer
from quant_company.maintenance.store import Store
from quant_company.staff.store import StaffStore

from .test_maintenance import GitHubFixture, ModelFixture, config
from .test_maintenance_cases import delivery, drain, improvements, submit  # noqa: F401

JARGON = ("대사", "영수증", "runtime", "review_reconciliation_required", "blocked", "triage", "진단 요청")


def blocked_review(company, employee="data"):
    """A completed exercise whose independent Claude review ended without a confirmed outcome."""
    run_id = StaffStore(company).enqueue(employee, "UHUMAN")
    review_id = "review-" + str(run_id)
    request = {"request_id": review_id, "prompt": "Frozen synthetic request"}
    with company.db.transaction() as conn:
        conn.execute("""UPDATE staff_runs SET state='completed',final_answer='{}',grade=%s,completed_at=now()
            WHERE id=%s""", (Jsonb({"objective_passed": True}), run_id))
        conn.execute("""INSERT INTO staff_independent_reviews
            (id,run_id,state,model,rubric_version,source_digest,request,input_digest,schedule_day,error)
            VALUES (%s,%s,'blocked','claude-opus-5','fixture-rubric',%s,%s,%s,CURRENT_DATE,'uncertain')""",
            (review_id, run_id, "b" * 64, Jsonb(request), fingerprint(request)))
        conn.execute("UPDATE maintenance_control SET next_observe_at=now()")
    return review_id


async def observe_review_case(company, credentials, box):
    """Observer intake -> no-model reconciliation hold -> reporter, as the deployed services do."""
    store = Store(company, config())
    job_id = store.collect()
    assert job_id
    await drain(box)
    with company.db.transaction() as conn:
        job = conn.execute("SELECT * FROM maintenance_jobs WHERE id=%s", (job_id,)).fetchone()
    assert store.hold_for_reconciliation(job)
    await Reporter(company, config()).tick()
    await drain(box)
    return job_id


def latest_card(calls):
    return [body["text"] for path, body in calls if path.endswith(("chat.postMessage", "chat.update"))
            and "thread_ts" not in body][-1]


def owner_notices(calls):
    return [body["text"] for _, body in calls if "<@UHUMAN>" in body["text"]]


async def test_review_failure_card_names_problem_improvement_and_who_acts(improvements):  # noqa: F811
    company, credentials = improvements
    company.roles["data"] = company.roles["data"].model_copy(update={"name": "데이터 담당"})
    blocked_review(company)
    box, calls = delivery(company, credentials)
    await observe_review_case(company, credentials, box)
    card = latest_card(calls)
    assert "데이터 담당" in card, "The owner must see whose exercise review failed"
    assert "무슨 문제:" in card and "필요한 개선:" in card and "할 일:" in card
    assert "개발 작업" in card, "The owner must learn the bot cannot repair this kind itself"
    assert "영향" in card and "실제 서비스에는 영향이 없습니다" in card
    assert not [word for word in JARGON if word in card]
    [notice] = owner_notices(calls)
    assert "필요한 개선:" in notice and "할 일:" in notice, "A phone push shows the ping without the card"
    assert not [word for word in JARGON if word in notice]


async def test_repeated_review_failure_joins_the_open_case_without_a_new_case_or_ping(improvements):  # noqa: F811
    company, credentials = improvements
    company.roles["researcher_kr"] = company.roles["researcher_kr"].model_copy(update={"name": "국내시장 연구"})
    original = blocked_review(company)
    box, calls = delivery(company, credentials)
    first = await observe_review_case(company, credentials, box)
    repeat = blocked_review(company, employee="researcher_kr")
    assert Store(company, config()).collect() is None
    await Reporter(company, config()).tick()
    await drain(box)
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM maintenance_cases").fetchone()["n"] == 1
        assert conn.execute("""SELECT count(*) AS n FROM maintenance_observations
            WHERE job_id=%s AND body->>'kind'='staff_independent_review_failure'""", (first,)).fetchone()["n"] == 2
        receipt = conn.execute("SELECT receipt FROM maintenance_jobs WHERE id=%s", (first,)).fetchone()["receipt"]
    assert receipt["review_ids"] == sorted([original, repeat]), "Every grouped review stays listed for reconciliation"
    assert set(receipt["input_digests"]) == {original, repeat}
    card = latest_card(calls)
    assert "2번" in card
    assert "국내시장 연구" in next(line for line in card.splitlines() if line.startswith("무슨 문제:")), \
        "The problem line describes the latest occurrence"
    assert len(owner_notices(calls)) == 1, "A repeat of the same problem must not ping the owner again"


@pytest.mark.parametrize("change", ["has_response", "changed_request"])
async def test_a_repeat_whose_review_record_changed_is_diagnosed_not_grouped(improvements, change):  # noqa: F811
    company, credentials = improvements
    blocked_review(company)
    box, _ = delivery(company, credentials)
    first = await observe_review_case(company, credentials, box)
    review_id = blocked_review(company)
    with company.db.transaction() as conn:
        if change == "has_response":
            conn.execute("UPDATE staff_independent_reviews SET response=%s WHERE id=%s", (Jsonb({"kept": True}), review_id))
        else:
            conn.execute("UPDATE staff_independent_reviews SET request=%s WHERE id=%s", (Jsonb({"changed": True}), review_id))
    second = Store(company, config()).collect()
    assert second and second != first, "Only an intact unconfirmed review may join the open reconciliation case"


async def test_review_failure_after_a_quiet_week_opens_a_new_case(improvements):  # noqa: F811
    company, credentials = improvements
    blocked_review(company)
    box, _ = delivery(company, credentials)
    first = await observe_review_case(company, credentials, box)
    with company.db.transaction() as conn:
        conn.execute("""UPDATE maintenance_observations SET body=jsonb_set(body,'{created_at}',
            to_jsonb((now()-interval '8 days')::text)) WHERE job_id=%s""", (first,))
    blocked_review(company)
    second = Store(company, config()).collect()
    assert second and second != first
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM maintenance_cases").fetchone()["n"] == 2


async def test_request_case_card_states_the_request_as_the_problem(improvements):  # noqa: F811
    company, credentials = improvements
    submit(company, text="브리핑 메일 제목이 너무 길어. 짧게 고쳐줘")
    box, calls = delivery(company, credentials)
    await drain(box)
    card = latest_card(calls)
    assert card.startswith("*요청: 브리핑 메일 제목이 너무 길어")
    assert "필요한 개선:" in card and "할 일: 없음" in card
    assert not [word for word in JARGON if word in card]


async def test_ready_pr_card_and_notice_tell_the_owner_the_exact_reply(improvements):  # noqa: F811
    company, credentials = improvements
    source, receipt, _ = submit(company)
    box, calls = delivery(company, credentials)
    await drain(box)
    repair_id = str(uuid4())
    finding = {"title": "브리핑 메일 제목을 40자 안으로 줄임", "problem": "메일 제목이 길어 휴대폰에서 잘립니다.",
               "expected": "제목이 40자를 넘지 않고 핵심 지수가 앞에 옵니다.", "hypothesis": "제목 조립 함수에 길이 제한이 없습니다.",
               "evaluation": {"mode": "regression"}}
    with company.db.transaction() as conn:
        conn.execute("""INSERT INTO maintenance_jobs(id,kind,state,payload,receipt)
            VALUES (%s,'repair','pr_open',%s,%s)""", (repair_id,
            Jsonb({"owners": ["UHUMAN"], "finding": finding, "observations": [{"project_id": source["project_id"]}]}),
            Jsonb({"head": "c" * 40, "pr": {"number": 31, "url": "https://github.com/example/pull/31"}})))
        conn.execute("UPDATE maintenance_jobs SET state='done',receipt=%s WHERE id=%s",
                     (Jsonb({"case_id": repair_id}), receipt["request_id"]))
    await Reporter(company, config()).tick()
    await drain(box)
    card = latest_card(calls)
    assert card.startswith("*브리핑 메일 제목을 40자 안으로 줄임*")
    assert "메일 제목이 길어 휴대폰에서 잘립니다" in card and "제목이 40자를 넘지 않고" in card
    assert "할 일:" in card and "PR 31 반영해" in card
    [notice] = owner_notices(calls)
    assert "PR 31 반영해" in notice and "https://github.com/example/pull/31" in notice


@pytest.mark.parametrize("pull,shown,approvable", [
    ({"state": "closed", "merged_at": None}, "닫혔습니다", False),
    ({"state": "closed", "merged_at": "2026-10-22T09:00:00Z"}, "병합됐습니다", False),
    ({"state": "open", "merged_at": None}, "PR 31 반영해", True),
])
async def test_a_bot_pr_closed_on_github_stops_asking_for_approval(improvements, pull, shown, approvable):  # noqa: F811
    company, credentials = improvements
    source, receipt, _ = submit(company)
    box, calls = delivery(company, credentials)
    await drain(box)
    repair_id = str(uuid4())
    with company.db.transaction() as conn:
        conn.execute("""INSERT INTO maintenance_jobs(id,kind,state,payload,receipt)
            VALUES (%s,'repair','pr_open',%s,%s)""", (repair_id,
            Jsonb({"owners": ["UHUMAN"], "finding": {"title": "브리핑 메일 제목을 40자 안으로 줄임"},
                   "observations": [{"project_id": source["project_id"]}]}),
            Jsonb({"head": "c" * 40, "pr": {"number": 31, "url": "https://github.com/example/pull/31", "state": "open"}})))
        conn.execute("UPDATE maintenance_jobs SET state='done',receipt=%s WHERE id=%s",
                     (Jsonb({"case_id": repair_id}), receipt["request_id"]))
    await Reporter(company, config()).tick()
    await drain(box)
    github = GitHubFixture()
    github.current_metadata = lambda snapshot: {"repository": "test-only-fixture", "ci": [], "pull_requests": [
        {"number": 31, "title": "fixture", "head": "c" * 40, "merge_commit": None, "url": "https://github.com/example/pull/31",
         **pull}]}
    Maintainer(company, config(), github=github, provider=ModelFixture()).refresh_repository()
    await Reporter(company, config()).tick()
    await drain(box)
    card = latest_card(calls)
    assert shown in card
    assert ("PR 31 반영해" in card) == approvable, "A PR closed on GitHub can no longer be approved from the card"
    command = dict(text="PR 31 반영해", owner="UHUMAN", channel="CIMPROVE", thread_ts="50.000001",
                   event_key="late-approval", event_ts="99.9")
    result = accept_approval(company, **command)
    assert (result["maintenance_approval"] == "approved") == approvable


async def test_case_list_counts_by_owner_action_and_collapses_repeats(improvements):  # noqa: F811
    company, credentials = improvements
    box, _ = delivery(company, credentials)
    jobs = []
    for _ in range(3):
        blocked_review(company)
        jobs.append(await observe_review_case(company, credentials, box))
        with company.db.transaction() as conn:
            conn.execute("""UPDATE maintenance_observations SET body=jsonb_set(body,'{created_at}',
                to_jsonb((now()-interval '8 days')::text)) WHERE job_id=%s""", (jobs[-1],))
    Store(company, config()).save(jobs[0], "blocked", error="investigation_budget_exhausted")
    submit(company, channel="CIMPROVE", thread="9.1", text="뉴스 요약이 너무 늦게 와. 원인을 찾아줘")
    with company.db.transaction() as conn:
        channel = conn.execute("""INSERT INTO projects(id,title,instruction,owner_user,channel)
            VALUES (%s,'목록','목록','UHUMAN','CIMPROVE') RETURNING *""", (uuid4(),)).fetchone()
        text = cases.status_text(conn, company, channel)
    lines = text.splitlines()
    assert "진행 중 1" in lines[0] and "멈춤 3" in lines[0]
    review_lines = [line for line in lines if "직원 교육 채점" in line]
    assert len(review_lines) == 2, "Cases that need a different owner action keep their own line"
    assert sum("×2" in line for line in review_lines) == 1, "Repeats of one problem collapse into one line"
    assert lines.index(next(line for line in lines if "뉴스 요약이 너무 늦게" in line)) < lines.index(review_lines[0])


@pytest.mark.parametrize("error,expected", [
    ("company_work_has_priority", "다른 업무가 먼저"), ("quota", "사용 한도"), ("subscription_paused", "사용 한도"),
    ("busy", "모델"), ("current_repository_GitHubError", "GitHub"), ("synthetic_wait", "synthetic_wait"),
])
def test_wait_status_names_what_the_case_is_waiting_for(error, expected):
    status = explain.summary({"state": "triage", "error": error}, {})["status"]
    assert expected in status
    assert error == "company_work_has_priority" or "다른 업무가 먼저" not in status


def test_a_rolled_back_application_is_a_stopped_case_for_the_owner():
    value = {"state": "pr_open", "application": {"state": "rolled_back"}}
    plain = explain.summary(value, {})
    assert "되돌렸습니다" in plain["status"] and plain["action"] != "없음. 결과를 이 스레드에 알립니다."
    assert explain.group(value) == "멈춤"


async def test_maintainer_status_tool_returns_the_plain_problem_and_owner_action(improvements):  # noqa: F811
    company, credentials = improvements
    company.roles["data"] = company.roles["data"].model_copy(update={"name": "데이터 담당"})
    blocked_review(company)
    box, _ = delivery(company, credentials)
    job_id = await observe_review_case(company, credentials, box)
    with company.db.transaction() as conn:
        case = conn.execute("SELECT * FROM maintenance_cases WHERE request_id=%s", (job_id,)).fetchone()
        target = company._project(conn, case["project_id"])
        [item] = status(conn, company, target)["requests"]
    assert "데이터 담당" in item["summary"]["problem"]
    assert "개발 작업" in item["summary"]["action"]
