import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from quant_company.company import Company, load_roles
from quant_company.contracts import AgentDecision, ProviderResponse, ToolRequest
from quant_company.maintenance.policy import writable
from quant_company.staff.cases import FAMILIES, grade, make_case
from quant_company.staff.packs import STAFF, coaching, pack
from quant_company.staff.runner import StaffRunner
from quant_company.staff.store import StaffStore, maintenance_observations, status


@pytest.fixture
def staff_company(company):
    company.roles = load_roles(company.settings)
    company.settings.company_staff_development_enabled = True
    return company


def reply(request, *, answer=None, decision=None):
    decision = decision or AgentDecision(status="complete", say="합성 직무 사례 풀이",
                                         artifacts=[{"title": "답안", "content": json.dumps(answer), "source_ids": []}])
    return ProviderResponse(request_id=request["request_id"], decision=decision, provider="fixture")


def oracle_answer(company, run_id):
    # Test double only: a real employee/provider never receives or queries the key.
    with company.db.transaction() as conn:
        key = conn.execute("SELECT answer_key FROM staff_runs WHERE id=%s", (run_id,)).fetchone()["answer_key"]
    return {"metrics": key["metrics"], "reject_ids": key["reject_ids"], "explanation": "독립 테스트 더블용 정답이며 실제 모델 역량 증거가 아닙니다."}


@pytest.mark.parametrize("employee", STAFF)
def test_family_factory_is_reproducible_fresh_and_rejects_false_confidence(employee):
    for variant in (0, 1):
        public, key = make_case(employee, "seed-unique-123", variant)
        assert (public, key) == make_case(employee, "seed-unique-123", variant)
        assert public != make_case(employee, "seed-unique-456", variant)[0]
        assert public["family"] == FAMILIES[employee][variant]
        assert "answer_key" not in json.dumps(public)
        good = {"metrics": key["metrics"], "reject_ids": key["reject_ids"], "explanation": "테스트 목적의 충분히 긴 설명입니다."}
        assert grade(good, key)["objective_passed"]
        assert not grade({**good, "reject_ids": key["reject_ids"]+["fabricated-record"]}, key)["objective_passed"]
        if key["reject_ids"]:
            assert not grade({**good, "reject_ids": []}, key)["objective_passed"]
        assert not grade({**good, "metrics": {k: True for k in key["metrics"]}}, key)["objective_passed"]
        assert not grade({**good, "metrics": {k: float("nan") for k in key["metrics"]}}, key)["objective_passed"]


def test_pack_loaded_into_real_employee_request_and_tools_enforced(staff_company):
    company = staff_company
    task = company.ingest(event_key="staff-integration", text="데이터 품질을 검사해줘", owner="UHUMAN", agent="data")
    turns = company.pending_starts()
    prepared = company.prepare_turn(turns[0]["id"])
    assert pack("data")["digest"] in prepared["request"]["prompt"]
    assert pack("risk")["procedure"] not in prepared["request"]["prompt"]
    with company.db.transaction() as conn:
        job = conn.execute("SELECT * FROM tasks WHERE project_id=%s", (task["project_id"],)).fetchone()
        project = company._project(conn, task["project_id"])
        with pytest.raises(ValueError, match="Unauthorized tool"):
            company.validate_decision(conn, project, job, AgentDecision(status="continue", say="",
                tools=[ToolRequest(name="staff_status", arguments={})]))
        assert company._tool(conn, task["project_id"], ToolRequest(name="data_quality", arguments={
            "rows": [{"id": "a"}], "key_fields": ["id"], "required_fields": []}), task=job)["result"]["passed"]


def test_frozen_request_restart_tool_roundtrip_and_no_answer_leak(staff_company):
    store = StaffStore(staff_company)
    run_id = store.enqueue("financial_strategist", "UHUMAN")
    ready = store.prepare()
    assert ready["request"]["reasoning_effort"] == "max"
    staff_company.roles["financial_strategist"] = staff_company.roles["financial_strategist"].model_copy(
        update={"reasoning_effort": "high"})
    prompt = ready["request"]["prompt"]
    assert "answer_key" not in prompt and "relative_tolerance" not in prompt
    restarted = StaffStore(Company(staff_company.settings))
    assert restarted.prepare()["request"] == ready["request"]
    decision = AgentDecision(status="continue", say="계산합니다", tools=[ToolRequest(name="finance_compute", arguments={
        "operation": "fx_return", "local_return": 0.1, "fx_return": -0.1})])
    response = reply(ready["request"], decision=decision)
    assert store.commit(run_id, response)["state"] == "running"
    assert store.commit(run_id, response)["duplicate"]
    second = store.prepare()
    assert second["request"]["reasoning_effort"] == "max"
    assert second["request"]["request_id"] != ready["request"]["request_id"]
    assert "base_currency_return" in second["request"]["prompt"]
    result = reply(second["request"], answer=oracle_answer(staff_company, run_id))
    assert store.commit(run_id, result)["state"] == "completed"
    with staff_company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM projects").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0
        report = status(conn, staff_company, "UHUMAN")
        assert report["recent_exercises"][0]["grade"]["objective_passed"]
        assert "answer_key" not in json.dumps(report)


def test_scheduler_is_durable_daily_bounded_rotating_and_does_not_activate_roles(staff_company):
    store = StaffStore(staff_company)
    at = datetime(2031, 1, 1, 0, tzinfo=UTC)
    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(lambda _: store.schedule(at), range(4)))
    assert len([i for i in ids if i]) == 1
    ready = store.prepare()
    store.commit(ready["run_id"], reply(ready["request"], answer=oracle_answer(staff_company, ready["run_id"])))
    assert store.schedule(at)
    ready = store.prepare()
    store.commit(ready["run_id"], reply(ready["request"], answer=oracle_answer(staff_company, ready["run_id"])))
    assert store.schedule(at) is None
    assert StaffStore(Company(staff_company.settings)).schedule(at) is None
    assert store.schedule(at+timedelta(days=1))
    assert not staff_company.roles["validator"].active
    assert store.enqueue("validator", "UHUMAN")
    assert not staff_company.roles["validator"].active


def test_faults_do_not_create_replacement_calls_and_authorization_is_rechecked(staff_company):
    store = StaffStore(staff_company)
    identity = str(uuid4())
    assert store.enqueue("data", "UHUMAN", identity=identity) == identity
    assert store.enqueue("data", "UHUMAN", identity=identity) == identity
    ready = store.prepare()
    store.fault(identity, "quota", 3600)
    assert store.prepare()["state"] == "idle"
    with staff_company.db.transaction() as conn:
        conn.execute("UPDATE staff_runs SET next_at=now()")
    assert store.prepare()["request"] == ready["request"]
    staff_company.settings.slack_allowed_users = []
    assert store.prepare()["state"] == "blocked"
    with staff_company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM staff_calls").fetchone()["n"] == 1


def test_ordinary_work_wins_and_disabled_background_stays_paused(staff_company):
    store = StaffStore(staff_company)
    store.enqueue("data", "UHUMAN")
    staff_company.ingest(event_key="urgent", owner="UHUMAN", text="실제 사용자 요청")
    assert store.prepare()["state"] == "defer"


async def test_disabled_runner_does_not_infer(staff_company):
    staff_company.settings.company_staff_development_enabled = False
    class NoCalls:
        async def run(self, request):
            pytest.fail("disabled periodic training must not call provider")
    assert await StaffRunner(staff_company, NoCalls()).tick() == {"state": "paused"}


def test_failure_feedback_is_scoped_attributed_disputable_and_reaches_maintainer(staff_company):
    store = StaffStore(staff_company)
    identity = store.enqueue("financial_strategist", "UHUMAN")
    ready = store.prepare()
    answer = oracle_answer(staff_company, identity)
    answer["metrics"] = {k: -987654 for k in answer["metrics"]}
    store.commit(identity, reply(ready["request"], answer=answer))
    with staff_company.db.transaction() as conn:
        feedback = coaching(conn, "UHUMAN", "financial_strategist")
        assert feedback and feedback[0]["run_id"] == identity
        assert coaching(conn, "another-owner", "financial_strategist") == []
        assert coaching(conn, "UHUMAN", "data") == []
        observations = maintenance_observations(conn, ["UHUMAN"])
        assert observations[0]["key"].startswith("staff:")
        assert "answer_key" not in json.dumps(observations)
        assert maintenance_observations(conn, ["other"]) == []
    store.review(identity, "disputed", "문제의 가정과 정답키를 추가 독립 검토해야 합니다.")
    with staff_company.db.transaction() as conn:
        assert coaching(conn, "UHUMAN", "financial_strategist") == []
        assert maintenance_observations(conn, ["UHUMAN"]) == []
        assert conn.execute("SELECT grade FROM staff_runs WHERE id=%s", (identity,)).fetchone()["grade"]


def test_budget_and_forbidden_action_do_not_spill_into_normal_work(staff_company):
    staff_company.settings.staff_max_calls_per_exercise = 1
    store = StaffStore(staff_company)
    identity = store.enqueue("director", "UHUMAN")
    ready = store.prepare()
    action = AgentDecision(status="wait", say="", delegations=[{"agent": "data", "instruction": "외부 업무"}])
    assert store.commit(identity, reply(ready["request"], decision=action))["state"] == "blocked"
    identity = store.enqueue("data", "UHUMAN")
    ready = store.prepare()
    action = AgentDecision(status="continue", say="", tools=[{"name": "calculate", "arguments": {"expression": "2+2"}}])
    store.commit(identity, reply(ready["request"], decision=action))
    assert store.prepare()["state"] == "blocked"
    with staff_company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM tasks").fetchone()["n"] == 0


def test_maintainer_can_improve_procedures_but_cannot_edit_its_examination():
    assert writable("src/quant_company/staff/playbooks/financial_strategist.md")
    for name in ["cases.py", "store.py", "runner.py", "workflow.py", "schema.sql"]:
        assert not writable("src/quant_company/staff/" + name)
    assert not writable("src/quant_company/staff/playbooks/../../cases.py")


def test_failed_exercise_is_collected_once_without_a_slack_project(staff_company):
    from quant_company.maintenance.store import Store

    from .test_maintenance import config

    store = StaffStore(staff_company)
    identity = store.enqueue("data", "UHUMAN")
    ready = store.prepare()
    store.commit(identity, reply(ready["request"], answer={}))
    maintainer = Store(staff_company, config())
    maintainer.initialize()
    job = maintainer.collect()
    assert job
    with staff_company.db.transaction() as conn:
        item = conn.execute("SELECT payload FROM maintenance_jobs WHERE id=%s", (job,)).fetchone()["payload"]
        assert len(item["observations"]) == 1
        assert item["observations"][0]["run_id"] == identity
        conn.execute("UPDATE maintenance_jobs SET state='done'")
        conn.execute("UPDATE maintenance_control SET next_observe_at=now()")
        assert conn.execute("SELECT count(*) AS n FROM projects").fetchone()["n"] == 0
    assert maintainer.collect() is None


def test_specialist_procedure_replay_binds_original_digest_and_changes_only_candidate(company):
    from quant_company.maintenance.evaluation import PACK_PREFIX, replay_pack

    task = company.ingest(event_key='replay-pack', owner='UHUMAN', text='합성 질문', agent='data')
    request = company.prepare_turn(company.pending_starts()[0]['id'])['request']
    path = PACK_PREFIX + 'data.md'
    before = pack('data')['procedure']
    payload = {'originals': {path: before}, 'changes': {path: before + '\n추가 수치 검증 절차.\n'},
               'replay_inputs': {'saved': {'request': request, 'agent': 'data'}}}
    from types import SimpleNamespace

    base = replay_pack(payload, SimpleNamespace(request_key='saved'), 'base', 'data')[0]
    candidate = replay_pack(payload, SimpleNamespace(request_key='saved'), 'candidate', 'data')[0]
    assert base == request['prompt']
    assert '추가 수치 검증 절차.' not in base and '추가 수치 검증 절차.' in candidate
    runtime_text = candidate.split('RUNTIME CONFIG JSON:\n', 1)[1].split('\nTASK DATA JSON:\n', 1)[0]
    runtime = json.loads(runtime_text)
    assert next(r for r in runtime['employees'] if r['id'] == 'data')['specialist_pack_digest'] != pack('data')['digest']
    payload['originals'][path] = before + '\nincorrect base\n'
    with pytest.raises(ValueError, match='recorded_specialist_pack'):
        replay_pack(payload, SimpleNamespace(request_key='saved'), 'candidate', 'data')
    assert task['project_id']
