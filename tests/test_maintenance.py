import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from quant_company.contracts import AgentDecision, ProviderResponse, Role
from quant_company.maintenance.policy import MaintenanceConfig, Patch, Triage, apply_patch, writable
from quant_company.maintenance.runner import Maintainer
from quant_company.maintenance.store import Deferred, Store
from quant_company.maintenance.workflow import MaintenanceWorkflow

SOURCE = "src/quant_company/tools.py"
ORIGINAL = "def bounded_sum(left, right):\n    return left - right\n"


def config(**updates):
    return MaintenanceConfig(app_id=1, installation_id=2, private_key_file=Path("unused-test-key"),
                             allowed_owners=["UHUMAN"], enabled=True, **updates)


def prepare(company):
    company.roles["engineer"] = Role(id="engineer", name="test maintainer", mission="fixture",
                                    model="fixture-model", instructions="fixture", tools=[], can_delegate_to=[])
    request = company.ingest(event_key="maintenance-test", owner="UHUMAN", text="The sum helper subtracts inputs",
                             channel="CQUANT", thread_ts="1.1")
    with company.db.transaction() as conn:
        conn.execute("UPDATE turns SET status='completed'")
        conn.execute("UPDATE tasks SET status='completed'")
    return request


class ModelFixture:
    def __init__(self):
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        data = json.loads(request.prompt.split("EVIDENCE JSON:\n", 1)[1])
        if request.request_id.endswith("-triage"):
            output = {"reason": "A concrete platform arithmetic defect was reported", "finding": {
                "problem_key": "bounded_sum_subtracts", "title": "Correct arithmetic helper",
                "problem": "The bounded sum helper returns a subtraction result.",
                "reproduction": "Call bounded_sum(3, 2) and observe the fixture result of 1.",
                "expected": "The helper must return the sum of both arguments.",
                "category": "platform_defect", "hypothesis": "The arithmetic operator is incorrect.",
                "evaluation": {"mode": "regression", "success_criterion": "The regression fails on base and passes after repair."},
                "evidence_keys": [data["observations"][0]["key"], data["current_implementation"]["key"]], "paths": [SOURCE],
            }}
        else:
            output = {"summary": "Use addition and add a regression for unequal operands", "edits": [
                {"path": SOURCE, "old": "return left - right", "new": "return left + right"},
                {"path": data["required_new_test_path"], "old": "", "new":
                 "from quant_company.tools import bounded_sum\n\ndef test_sum():\n    assert bounded_sum(3, 2) == 5\n"},
            ]}
        return ProviderResponse(request_id=request.request_id, provider="fixture", decision=AgentDecision(
            say="fixture proposal", status="complete", artifacts=[{
                "title": "maintenance proposal fixture", "content": json.dumps(output), "source_ids": [],
            }]))


class GitHubFixture:
    def __init__(self, ci_state="passed"):
        self.published, self.prs, self.ci_state = 0, 0, ci_state

    def snapshot(self):
        return {"commit": "a" * 40, "tree": "b" * 40, "paths": [SOURCE],
                "entries": {SOURCE: {"sha": "d" * 40, "type": "blob", "mode": "100644"}}}

    def read_repository(self, snapshot):
        return {SOURCE: ORIGINAL}, {"read_files": 1, "omitted_paths": []}

    def current_metadata(self, snapshot):
        return {"repository": "test-only-fixture", "pull_requests": [], "ci": []}

    def read_files(self, snapshot, paths):
        assert paths == [SOURCE]
        return {SOURCE: ORIGINAL}

    def publish(self, job, payload):
        assert "return left + right" in payload["changes"][SOURCE]
        self.published += 1
        return {"branch": "maintenance/" + str(job["id"]), "head": "c" * 40, "base": "a" * 40}

    def ci(self, receipt):
        return {"state": self.ci_state, "url": "https://github.com/example/ci/1"}

    def pull_request(self, job):
        assert job["receipt"]["ci"]["state"] == "passed"
        self.prs += 1
        return {"url": "https://github.com/example/repo/pull/1", "number": 1, "state": "open"}


def make_maintainer(company, *, ci_state="passed"):
    prepare(company)
    runner = Maintainer(company, config(), github=GitHubFixture(ci_state), provider=ModelFixture())
    runner.store.initialize()
    return runner


@pytest.mark.integration
async def test_durable_pipeline_to_pr_and_slack_outbox_once(company):
    runner = make_maintainer(company)
    for _ in range(5):
        assert (await runner.tick())["state"] == "advanced"
    assert runner.github.published == runner.github.prs == 1
    assert len(runner.provider.requests) == 2
    assert await runner.tick() == {"state": "idle"}
    with company.db.transaction() as conn:
        case = conn.execute("SELECT * FROM maintenance_jobs WHERE kind='repair'").fetchone()
        assert case["state"] == "pr_open"
        assert conn.execute("SELECT count(*) AS n FROM outbox WHERE text LIKE '[개선 담당]%%'").fetchone()["n"] == 1
        assert conn.execute("SELECT reserved FROM daily_usage").fetchone()["reserved"] == 2
        conn.execute("UPDATE maintenance_control SET next_observe_at=now()")
    # Own notification must not create another observation/job or another external write.
    assert runner.store.collect() is None
    runner.store.finish_pr(case, case["receipt"])
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox WHERE text LIKE '[개선 담당]%%'").fetchone()["n"] == 1


@pytest.mark.integration
async def test_failed_ci_blocks_pr_and_does_not_loop(company):
    runner = make_maintainer(company, ci_state="failed")
    for _ in range(5):
        await runner.tick()
    assert runner.github.published == 1 and runner.github.prs == 0
    with company.db.transaction() as conn:
        case = conn.execute("SELECT * FROM maintenance_jobs WHERE kind='repair'").fetchone()
    assert case["state"] == "blocked" and case["error"] == "ci_failed_requires_review"


@pytest.mark.integration
async def test_ci_timeout_starts_after_a_multiday_evaluation_not_at_case_creation(company):
    runner = make_maintainer(company, ci_state="pending")
    for _ in range(2):
        await runner.tick()
    with company.db.transaction() as conn:
        conn.execute("UPDATE maintenance_jobs SET created_at=now()-interval '2 days' WHERE kind='repair'")
    await runner.tick()  # Publish after the long budget wait.
    await runner.tick()
    case = runner.store.next_job()
    assert case["state"] == "ci" and case["error"] is None
    case["receipt"]["ci_started_at"] = (datetime.now(UTC)-timedelta(hours=25)).isoformat()
    runner.store.save(case["id"], "ci", receipt=case["receipt"])
    await runner.tick()
    with company.db.transaction() as conn:
        case = conn.execute("SELECT * FROM maintenance_jobs WHERE kind='repair'").fetchone()
    assert case["state"] == "blocked" and case["error"] == "ci_timeout_requires_review"


@pytest.mark.integration
def test_late_transaction_is_observed_without_reprocessing(company):
    request = prepare(company)
    store = Store(company, config())
    store.initialize()
    first = store.collect()
    job = store.next_job()
    assert str(job["id"]) == first
    store.finish_triage(job, Triage(reason="No platform defect"))
    late_id = str(uuid4())
    with company.db.transaction() as late:
        late.execute("""INSERT INTO messages(id,project_id,revision,author,kind,text,created_at)
            VALUES (%s,%s,1,'UHUMAN','human','Late transaction',now()-interval '1 day')""",
                     (late_id, request["project_id"]))
        with company.db.transaction() as conn:
            conn.execute("UPDATE maintenance_control SET next_observe_at=now()")
        assert store.collect() is None  # uncommitted row is invisible
    with company.db.transaction() as conn:
        conn.execute("UPDATE maintenance_control SET next_observe_at=now()")
    assert store.collect() is not None
    assert store.next_job()["payload"]["observations"][0]["key"] == "message:" + late_id


@pytest.mark.integration
async def test_restart_preserves_request_and_budget_and_yields_to_human(company):
    runner = make_maintainer(company)
    runner.store.collect()
    job = runner.store.next_job()
    original = runner.store.prepare_call(job, "triage", "original input")
    again = runner.store.prepare_call(job, "triage", "changed input is not a new inference")
    assert original["request"] == again["request"]
    company.ingest(event_key="human-priority", owner="UHUMAN", text="New human request")
    with pytest.raises(Deferred, match="company_work_has_priority"):
        runner.store.prepare_call(job, "triage", "resume while human waits")
    with company.db.transaction() as conn:
        assert conn.execute("SELECT reserved FROM daily_usage").fetchone()["reserved"] == 1


@pytest.mark.integration
def test_owner_filter_daily_budget_and_secret_omission(company):
    prepare(company)
    store = Store(company, config(max_daily_calls=1))
    store.initialize()
    with company.db.transaction() as conn:
        conn.execute("UPDATE messages SET text=%s", ("Bearer " + "a" * 30,))
    store.collect()
    job = store.next_job()
    assert job["payload"]["observations"][0].get("omitted")
    store.prepare_call(job, "triage", "safe input")
    with pytest.raises(Deferred, match="daily_model_budget"):
        store.prepare_call(job, "patch", "safe input")


@pytest.mark.parametrize("path", [
    "/src/quant_company/tools.py", "../quant-data/api.py", "src/../config.py", ".github/workflows/ci.yml",
    "src/quant_company/config.py", "src/quant_company/maintenance/runner.py", "deploy/compose.yaml",
    "tests/test_company.py", "src/quant_company/../tools.py", "src/quant_company//tools.py",
])
def test_protected_paths(path):
    assert not writable(path)
    assert not writable(path, new=True)


def test_patch_requires_exact_context_and_independent_regression():
    with pytest.raises(ValueError, match="match_exactly_once"):
        apply_patch(Patch(summary="Fix the fixture function", edits=[
            {"path": SOURCE, "old": "missing", "new": "anything"}]), {SOURCE: ORIGINAL}, "case")
    with pytest.raises(ValueError, match="requires_new_regression_test"):
        apply_patch(Patch(summary="Fix the fixture function", edits=[
            {"path": SOURCE, "old": "left - right", "new": "left + right"}]), {SOURCE: ORIGINAL}, "case")


@pytest.mark.integration
async def test_temporal_maintenance_workflow_history_replays(company):
    runner = make_maintainer(company)
    # Reuse only the executable, as the company workflow tests do; each server/history remains isolated.
    cache = Path(".local/temporal")
    cache.mkdir(parents=True, exist_ok=True)
    async with await WorkflowEnvironment.start_local(download_dest_dir=str(cache.resolve()), ui=False) as env:
        queue = "maintenance-test-" + uuid4().hex
        async with Worker(env.client, task_queue=queue, workflows=[MaintenanceWorkflow], activities=[runner.tick]):
            handle = await env.client.start_workflow(MaintenanceWorkflow.run, 0.05, id=queue, task_queue=queue)
            async with asyncio.timeout(30):
                while runner.github.prs != 1:
                    await asyncio.sleep(0.1)
            history = await handle.fetch_history()
            await Replayer(workflows=[MaintenanceWorkflow]).replay_workflow(history)
            await handle.cancel()
    assert runner.github.prs == 1
