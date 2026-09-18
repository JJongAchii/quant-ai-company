"""Private-key test doubles verify control flow, not real model quality."""

import json

import pytest
from psycopg.types.json import Jsonb

from quant_company.company import load_roles
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.maintenance.policy import writable
from quant_company.maintenance.runner import Maintainer
from quant_company.staff.comparisons import PREFIX, freeze, request_prompt, verify_receipt
from quant_company.staff.packs import pack, render_pack
from quant_company.staff.store import StaffStore

from .test_maintenance import GitHubFixture, config
from .test_staff_development import reply

PATH = PREFIX + "data.md"


class StaffGitHub(GitHubFixture):
    def snapshot(self):
        return {"commit": "a"*40, "tree": "b"*40, "paths": [PATH],
                "entries": {PATH: {"sha": "d"*40, "type": "blob", "mode": "100644"}}}

    def read_repository(self, snapshot):
        return {PATH: pack("data")["procedure"]}, {"read_files": 1, "omitted_paths": []}

    def read_files(self, snapshot, paths):
        assert paths == [PATH]
        return {PATH: pack("data")["procedure"]}

    def publish(self, job, payload):
        assert set(payload["changes"]) == {PATH}
        assert "STAFF_REPAIR_HINT" in payload["changes"][PATH]
        self.published += 1
        return {"branch": "maintenance/" + str(job["id"]), "head": "c"*40, "base": "a"*40}


class StaffModel:
    def __init__(self, company, *, regression=False, already_correct=False):
        self.company, self.regression, self.already_correct = company, regression, already_correct
        self.requests = []

    async def run(self, request):
        expected = "high" if "EXERCISE JSON:\n" in request.prompt else "max"
        assert request.reasoning_effort == expected
        self.requests.append(request)
        if "-staff-" in request.request_id:
            context = json.loads(request.prompt.split("EXERCISE JSON:\n", 1)[1])
            assert "answer_key" not in request.prompt and "relative_tolerance" not in request.prompt
            # Test double deliberately accesses server-only keys; production providers cannot.
            with self.company.db.transaction() as conn:
                record = conn.execute("SELECT material FROM staff_comparisons").fetchone()["material"]
            case = next(c for c in record["cases"] if c["public"] == context["case"])
            candidate = "STAFF_REPAIR_HINT" in context["specialist_procedure"]["procedure"]
            if request.request_id.endswith("-1"):
                decision = AgentDecision(status="continue", say="Fixture tool call", tools=[{
                    "name": "calculate", "arguments": {"expression": "2+2"}}])
            else:
                assert context["previous_tool_receipts"]
                key = case["key"]
                answer = {"metrics": key["metrics"], "reject_ids": key["reject_ids"],
                          "explanation": "Synthetic test oracle response, not actual professional quality evidence."}
                if ((case["purpose"] == "target" and not candidate and not self.already_correct)
                        or (case["purpose"] == "control" and candidate and self.regression)):
                    answer = {}
                decision = AgentDecision(status="complete", say="Fixture answer", artifacts=[{
                    "title": "Answer", "content": json.dumps(answer), "source_ids": []}])
        else:
            context = json.loads(request.prompt.split("EVIDENCE JSON:\n", 1)[1])
            assert "relative_tolerance" not in request.prompt
            if request.request_id.endswith("-triage"):
                observation = next(o for o in context["observations"] if o["kind"] == "staff_assessment")
                value = {"reason": "Recorded objective failure supports a bounded procedure comparison.", "finding": {
                    "problem_key": "staff_data_response_consistency", "title": "Verify structured data identifiers",
                    "category": "bot_behavior", "problem": "The staff answer fails the recorded objective check.",
                    "hypothesis": "The procedure needs a final structured identifier consistency check.",
                    "reproduction": "Compare the original and revised procedure on frozen staff cases.",
                    "expected": "Repair the released error and retain correct responses on fresh cases.",
                    "evaluation": {"mode": "staff_replay", "staff_run_id": observation["run_id"],
                                   "success_criterion": "Repair the target with fresh transfer and control checks."},
                    "evidence_keys": [observation["key"], context["current_implementation"]["key"]], "paths": [PATH]}}
            else:
                with self.company.db.transaction() as conn:
                    material = conn.execute("SELECT material FROM staff_comparisons").fetchone()["material"]
                for case in material["cases"][1:]:
                    assert all(row["id"] not in request.prompt for row in case["public"]["records"])
                original = context["source_files"][PATH]
                value = {"summary": "Add a final consistency check to the employee procedure.", "edits": [
                    {"path": PATH, "old": original, "new": original+"\nSTAFF_REPAIR_HINT\n"}]}
            decision = AgentDecision(status="complete", say="Fixture proposal", artifacts=[{
                "title": "Proposal", "content": json.dumps(value), "source_ids": []}])
        return ProviderResponse(request_id=request.request_id, provider="fixture", decision=decision)


def setup(company, **options):
    company.roles = load_roles(company.settings)
    store = StaffStore(company)
    identity = store.enqueue("data", "UHUMAN")
    request = store.prepare()
    store.commit(identity, reply(request["request"], answer={}))
    runner = Maintainer(company, config(), github=StaffGitHub(), provider=StaffModel(company, **options))
    runner.store.initialize()
    return runner, identity


def repair(company):
    with company.db.transaction() as conn:
        return conn.execute("SELECT * FROM maintenance_jobs WHERE kind='repair'").fetchone()


async def test_staff_failure_reaches_paired_comparison_and_pr_after_restart(company):
    runner, identity = setup(company)
    for _ in range(5):
        result = await runner.tick()
        assert result["state"] == "advanced", result
    assert repair(company)["state"] == "evaluate"
    runner = Maintainer(company, runner.config, github=runner.github, provider=runner.provider)
    for _ in range(14):
        await runner.tick()
    job = repair(company)
    assert job["state"] == "pr_open", job.get("error")
    assert runner.github.published == runner.github.prs == 1
    assert job["payload"]["evaluation"]["state"] == "passed"
    assert job["payload"]["evaluation"]["transfer_advantage"] is False
    assert len(runner.provider.requests) == 14  # Triage + patch + 3 pairs with a tool call each.
    assert len({r.request_id for r in runner.provider.requests}) == 14
    verify_receipt(company, job)
    job["payload"]["evaluation"]["transfer_advantage"] = True
    with pytest.raises(ValueError, match="receipt_changed"):
        verify_receipt(company, job)
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM projects").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0
        assert conn.execute("SELECT run_id::text FROM staff_comparisons").fetchone()["run_id"] == identity


@pytest.mark.parametrize("options,expected", [({"regression": True}, "failed"),
                                             ({"already_correct": True}, "inconclusive")])
async def test_regressions_and_unreproduced_failures_do_not_publish(company, options, expected):
    runner, _ = setup(company, **options)
    for _ in range(18):
        await runner.tick()
    job = repair(company)
    assert job["state"] == "blocked" and job["payload"]["evaluation"]["state"] == expected
    assert runner.github.published == runner.github.prs == 0


async def test_disputed_source_stops_comparison_before_another_call(company):
    runner, identity = setup(company)
    for _ in range(2):
        await runner.tick()
    StaffStore(company).review(identity, "disputed", "The problem's assumptions require independent review.")
    count = len(runner.provider.requests)
    await runner.tick()
    assert len(runner.provider.requests) == count
    assert repair(company)["state"] == "blocked"


async def test_changed_or_forged_comparison_evidence_is_rejected(company):
    runner, _ = setup(company)
    for _ in range(2):
        await runner.tick()
    job = repair(company)
    freeze(company, job)  # Re-entry keeps the same hidden cases.
    with company.db.transaction() as conn:
        material = conn.execute("SELECT material FROM staff_comparisons").fetchone()["material"]
        material["cases"][1]["key"]["metrics"] = {"fabricated": 1}
        conn.execute("UPDATE staff_comparisons SET material=%s", (Jsonb(material),))
    with pytest.raises(ValueError, match="frozen_input_changed"):
        freeze(company, job)
    assert not writable("src/quant_company/staff/comparisons.py")
    assert not writable("src/quant_company/staff/progress.py")
    assert not writable("src/quant_company/maintenance/evaluation.py")


async def test_completed_provider_call_is_reused_after_commit_interruption(company, monkeypatch):
    import quant_company.staff.comparisons as comparisons

    runner, _ = setup(company)
    for _ in range(2):
        await runner.tick()
    check = comparisons.check_response

    def crash(*args):
        raise RuntimeError("Simulated crash after durable provider response")

    monkeypatch.setattr(comparisons, "check_response", crash)
    with pytest.raises(RuntimeError, match="Simulated crash"):
        await runner.tick()
    count = len(runner.provider.requests)
    monkeypatch.setattr(comparisons, "check_response", check)
    await runner.tick()
    assert len(runner.provider.requests) == count
    with company.db.transaction() as conn:
        results = conn.execute("SELECT results FROM staff_comparisons").fetchone()["results"]
    assert len(results["target:base"]["calls"]) == 1


async def test_comparison_respects_existing_model_budget(company):
    runner, _ = setup(company)
    runner.config.max_daily_calls = 2
    for _ in range(2):
        await runner.tick()
    for _ in range(2):
        assert (await runner.tick())["state"] == "deferred"
    assert len(runner.provider.requests) == 2
    assert repair(company)["state"] == "evaluate"


def test_maintainer_candidate_replaces_embedded_procedure_after_jsonb_roundtrip(company):
    before = pack("maintainer")
    with company.db.transaction() as conn:
        saved = conn.execute("SELECT %s::jsonb AS value", (Jsonb(before),)).fetchone()["value"]
    record = {"employee": "maintainer", "material": {"base_pack": saved, "max_calls": 3,
               "context": {"role_instructions": render_pack(before)+"Other frozen instructions."}}}
    payload = {"changes": {PREFIX+"maintainer.md": before["procedure"]+"\nA new general procedure.\n"}}
    prompt = request_prompt(record, payload, {"public": {}}, "candidate", {"calls": [], "tools": []})
    context = json.loads(prompt.split("EXERCISE JSON:\n", 1)[1])
    embedded = json.loads(context["role_instructions"].split("\n")[1])
    assert embedded["digest"] == context["specialist_procedure"]["digest"] != before["digest"]
    assert context["role_instructions"].endswith("Other frozen instructions.")


def test_additive_evaluation_schema_preserves_old_frozen_plan_digest():
    from quant_company.maintenance.evaluation import verify_plan
    from quant_company.maintenance.policy import digest

    plan = {"mode": "regression", "success_criterion": "Preserve the exact previously frozen criterion.", "cases": []}
    finding = {"problem_key": "legacy_frozen_criterion", "title": "An existing pending repair",
               "problem": "An existing reproducible implementation defect.",
               "reproduction": "Run the existing frozen reproduction test.",
               "expected": "The target behavior matches the user's requested outcome.",
               "evidence_keys": ["an-existing-evidence-reference"], "category": "platform_defect",
               "hypothesis": "A concrete implementation defect is present.",
               "evaluation": plan, "paths": ["src/quant_company/tools.py"]}
    payload = {"finding": finding, "evaluation_plan_digest": digest(plan), "replay_inputs_digest": digest({})}
    assert verify_plan(payload).evaluation.mode == "regression"


def test_malformed_model_run_reference_is_rejected_before_postgres():
    from quant_company.maintenance.policy import EvaluationPlan

    with pytest.raises(ValueError):
        EvaluationPlan(mode="staff_replay", staff_run_id="-"*36,
                       success_criterion="Invalid UUID must not reach PostgreSQL and cause repeated activity retries.")
