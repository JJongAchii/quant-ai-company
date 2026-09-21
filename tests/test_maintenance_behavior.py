"""Real PostgreSQL, scripted model/GitHub: behavioral workflow tests, not live model quality evidence."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from psycopg.types.json import Jsonb

from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.maintenance.evaluation import ROLE_PATH, score, validate_candidate, verify_plan
from quant_company.maintenance.observation import review_snapshot
from quant_company.maintenance.policy import Expectations, Triage, digest
from quant_company.maintenance.runner import Maintainer
from quant_company.maintenance.store import Store

from .test_maintenance import GitHubFixture, config, prepare


class RoleGitHub(GitHubFixture):
    def __init__(self, company):
        super().__init__()
        self.original = json.dumps([role.model_dump() for role in company.roles.values()], indent=2)
        self.changes = None

    def snapshot(self):
        return {"commit": "a" * 40, "tree": "b" * 40, "paths": [ROLE_PATH],
                "entries": {ROLE_PATH: {"sha": "d" * 40, "type": "blob", "mode": "100644"}}}

    def read_repository(self, snapshot, previous=None):
        return {ROLE_PATH: self.original}, {"read_files": 1, "omitted_paths": []}

    def read_files(self, snapshot, paths):
        assert paths == [ROLE_PATH]
        return {ROLE_PATH: self.original}

    def publish(self, job, payload):
        self.published += 1
        self.changes = payload["changes"]
        return {"branch": "maintenance/" + str(job["id"]), "head": "c" * 40, "base": "a" * 40}


class BehaviorModel:
    def __init__(self, *, degraded=False, already_correct=False, design=False):
        self.requests = []
        self.degraded, self.already_correct, self.design = degraded, already_correct, design

    async def run(self, request):
        self.requests.append(request)
        if "-replay-" in request.request_id:
            candidate = request.request_id.endswith("-candidate")
            assert ("REPLAY_REPAIR_HINT" in request.prompt) == candidate
            if "-target-" in request.request_id:
                decision = (AgentDecision(say="Answer directly", status="complete")
                            if candidate or self.already_correct else AgentDecision(
                                say="Unnecessary delegation", status="wait",
                                delegations=[{"agent": "data", "instruction": "Unnecessary fixture work"}]))
            else:
                decision = (AgentDecision(say="Regressed: tool was skipped", status="complete")
                            if candidate and self.degraded else AgentDecision(
                                say="Calculate before answering", status="continue",
                                tools=[{"name": "calculate", "arguments": {"expression": "1+2"}}]))
        else:
            data = json.loads(request.prompt.split("EVIDENCE JSON:\n", 1)[1])
            if request.request_id.endswith("-triage"):
                inputs = [row for row in data["history"]["evidence"] if row.get("kind") == "replay_input"]
                cases = [{"purpose": purpose,
                          "request_key": next(row["key"] for row in inputs if purpose in row["text"]),
                          "expected": {"status": "complete", "delegates": [], "tools": []} if purpose == "target"
                          else {"status": "continue", "delegates": [], "tools": ["calculate"]}}
                         for purpose in ("target", "control")] if not self.design else []
                output = {"reason": "Recorded requests support a bounded collaboration hypothesis", "finding": {
                    "problem_key": "unnecessary_director_handoff", "title": "Reduce unnecessary handoffs",
                    "problem": "The director delegates a request that has an available direct answer.",
                    "reproduction": "Replay the recorded direct-answer and calculation requests.",
                    "expected": "Answer the target directly and preserve calculation tool use on the control.",
                    "category": "organization" if self.design else "collaboration",
                    "hypothesis": "The role instructions do not distinguish direct answers from tool-dependent work.",
                    "evaluation": {"mode": "design_only" if self.design else "prompt_replay", "cases": cases,
                                   "success_criterion": "Target passes after the change and the known-good control stays passing."},
                    "paths": [] if self.design else [ROLE_PATH],
                    "blocking_decisions": ["Owner must choose the new role activation and model budget."] if self.design else [],
                    "evidence_keys": ([case["request_key"] for case in cases] if cases else [data["observations"][0]["key"]])
                                     + [data["current_implementation"]["key"]],
                }}
            else:
                assert request.request_id.endswith("-patch")
                original = data["source_files"][ROLE_PATH]
                roles = json.loads(original)
                next(role for role in roles if role["id"] == "director")["instructions"] += " REPLAY_REPAIR_HINT"
                output = {"summary": "Clarify when direct completion is appropriate while preserving tool requests.",
                          "edits": [{"path": ROLE_PATH, "old": original, "new": json.dumps(roles, indent=2)}]}
            decision = AgentDecision(say="fixture proposal", status="complete", artifacts=[{
                "title": "fixture behavioral proposal", "content": json.dumps(output), "source_ids": [],
            }])
        return ProviderResponse(request_id=request.request_id, decision=decision, provider="behavior-fixture")


def runner_with_recorded_requests(company, **model_options):
    prepare(company)
    for purpose in ("target", "control"):
        project = company.ingest(event_key="recorded-" + purpose, text="fixture " + purpose + " request",
                                 owner="UHUMAN", channel="CQUANT", thread_ts=purpose)
        with company.db.transaction() as conn:
            turn = conn.execute("SELECT id FROM turns WHERE task_id=%s", (project["task_id"],)).fetchone()
        company.prepare_turn(str(turn["id"]))  # The real runtime builds and persists the original prompt.
        company.commit_turn(str(turn["id"]), ProviderResponse(request_id=str(turn["id"]), provider="fixture",
                            decision=AgentDecision(say="Recorded fixture answer", status="complete")))
    runner = Maintainer(company, config(), github=RoleGitHub(company), provider=BehaviorModel(**model_options))
    runner.store.initialize()
    return runner


def repair(company):
    with company.db.transaction() as conn:
        return conn.execute("SELECT * FROM maintenance_jobs WHERE kind='repair'").fetchone()


@pytest.mark.integration
async def test_recorded_prompt_pairs_survive_restart_and_have_no_company_side_effects(company):
    runner = runner_with_recorded_requests(company)
    with company.db.transaction() as conn:
        before = {table: conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
                  for table in ("tasks", "turns", "artifacts", "memories")}
    for _ in range(3):
        assert (await runner.tick())["state"] == "advanced"
    first = repair(company)
    assert first["state"] == "evaluate" and len(runner.provider.requests) == 3
    # Reload the runner; persisted model responses and frozen inputs are reused.
    runner = Maintainer(company, config(), github=runner.github, provider=runner.provider)
    for _ in range(9):
        await runner.tick()
    job = repair(company)
    assert job["state"] == "pr_open" and job["payload"]["evaluation"]["state"] == "passed"
    assert runner.github.prs == runner.github.published == 1
    assert set(runner.github.changes) == {ROLE_PATH}
    assert len(runner.provider.requests) == len({request.request_id for request in runner.provider.requests}) == 6
    for purpose in ("target", "control"):
        pair = [request for request in runner.provider.requests if f"-replay-{purpose}-" in request.request_id]
        assert len(pair) == 2 and pair[0].model == pair[1].model == company.roles["director"].model
        assert pair[0].prompt == pair[1].prompt.replace(" REPLAY_REPAIR_HINT", "")
    with company.db.transaction() as conn:
        after = {table: conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] for table in before}
        assert before == after  # Dry replay never executes proposed delegation/tool/memory effects.
        assert conn.execute("SELECT count(*) AS n FROM maintenance_calls").fetchone()["n"] == 6


@pytest.mark.integration
@pytest.mark.parametrize("options,state", [({"degraded": True}, "failed"), ({"already_correct": True}, "inconclusive")])
async def test_regressed_control_or_nonreproduced_target_never_publishes(company, options, state):
    runner = runner_with_recorded_requests(company, **options)
    for _ in range(12):
        await runner.tick()
    job = repair(company)
    assert job["state"] == "blocked" and job["error"] == "behavior_evaluation_" + state
    assert runner.github.published == runner.github.prs == 0
    assert len(runner.provider.requests) == 6


@pytest.mark.integration
async def test_behavior_evaluation_yields_to_humans_and_uses_shared_daily_budget(company):
    company.settings.company_max_daily_turns = 100
    runner = runner_with_recorded_requests(company)
    for _ in range(2):
        await runner.tick()
    company.ingest(event_key="human-during-evaluation", owner="UHUMAN", text="Priority request")
    assert await runner.tick() == {"state": "deferred", "reason": "company_work_has_priority"}
    assert len(runner.provider.requests) == 2
    with company.db.transaction() as conn:
        conn.execute("UPDATE turns SET status='completed'")
        conn.execute("UPDATE daily_usage SET reserved=%s", (company.settings.company_max_daily_turns,))
    assert await runner.tick() == {"state": "deferred", "reason": "daily_model_budget"}
    assert repair(company)["state"] == "evaluate" and runner.github.published == 0


@pytest.mark.integration
async def test_frozen_criteria_cannot_be_changed_after_patch(company):
    runner = runner_with_recorded_requests(company)
    for _ in range(2):
        await runner.tick()
    job = repair(company)
    job["payload"]["finding"]["evaluation"]["cases"][0]["expected"]["delegates"] = ["data"]
    runner.store.save(job["id"], "evaluate", payload=job["payload"])
    assert await runner.tick() == {"state": "blocked", "reason": "evaluation_plan_changed"}
    assert len(runner.provider.requests) == 2 and runner.github.published == 0


@pytest.mark.integration
async def test_organizational_design_gets_a_document_pr_without_claiming_behavior_improvement(company):
    runner = runner_with_recorded_requests(company, design=True)
    for _ in range(5):
        await runner.tick()
    job = repair(company)
    assert job["state"] == "pr_open" and len(runner.provider.requests) == 1
    assert job["payload"]["evaluation"]["state"] == "design_review_required"
    assert list(runner.github.changes) == [f"docs/improvements/{job['id']}.md"]
    assert "효과 미검증" in next(iter(runner.github.changes.values()))
    with company.db.transaction() as conn:
        notices = conn.execute("SELECT text FROM messages WHERE kind='maintenance'").fetchall()
    assert notices and all("효과는 아직 미검증" in row["text"] for row in notices)


@pytest.mark.integration
def test_history_uses_creation_cohorts_filters_owners_and_marks_secrets(company):
    original = prepare(company)
    other = company.ingest(event_key="other-owner", owner="UNAUTHORIZED", text="PRIVATE_OTHER_OWNER")
    at = datetime.now(UTC)
    with company.db.transaction() as conn:
        conn.execute("UPDATE tasks SET created_at=%s WHERE id=%s", (at-timedelta(days=10), original["task_id"]))
        conn.execute("UPDATE turns SET created_at=%s,attempts=3 WHERE task_id=%s",
                     (at-timedelta(days=10), original["task_id"]))
        conn.execute("UPDATE messages SET text=%s WHERE project_id=%s", ("Bearer " + "z"*30, original["project_id"]))
        review, inputs, _ = review_snapshot(conn, company, ["UHUMAN"], at)
    assert sum(row["turns"] for row in review["periods"][0]["turns"]) == 1
    assert review["periods"][0]["turns"][0]["retried_turns"] == 1
    assert review["periods"][1]["turns"] == []
    serialized = json.dumps(review)
    assert other["project_id"] not in serialized and "PRIVATE_OTHER_OWNER" not in serialized
    assert "z"*30 not in serialized and any(row.get("omitted") for row in review["evidence"])
    assert inputs == {}


@pytest.mark.integration
def test_invented_replay_inputs_are_not_accepted(company):
    runner = runner_with_recorded_requests(company)
    store = Store(company, config())
    store.collect()
    job = store.next_job()
    job["payload"]["snapshot"] = runner.github.snapshot()
    keys = list(job["payload"]["replay_inputs"])
    assert len(keys) == 2
    finding = {"problem_key": "invented_recorded_request", "title": "Invalid replay references",
               "problem": "Fixture platform problem", "reproduction": "Fixture platform reproduction",
               "expected": "Fixture platform expectation", "category": "bot_behavior",
               "hypothesis": "Fixture behavioral hypothesis", "paths": [ROLE_PATH], "evidence_keys": [keys[0]],
               "evaluation": {"mode": "prompt_replay", "success_criterion": "Both recorded cases must meet their checks.",
                              "cases": [{"purpose": purpose, "request_key": key, "expected": {"status": "complete"}}
                                        for purpose, key in zip(("target", "control"), keys, strict=True)]}}
    with pytest.raises(ValueError, match="requires_cited"):
        store.finish_triage(job, Triage(finding=finding, reason="Both requests must be cited"))


@pytest.mark.integration
async def test_observer_history_is_not_mistaken_for_employee_input_and_impossible_plan_stops_before_patch(company):
    runner = runner_with_recorded_requests(company)
    # A later thread is visible to the observer but was never in the target employee's recorded input.
    later = company.ingest(event_key="later-observer-only", owner="UHUMAN", text="LATER_OBSERVER_ONLY",
                           channel="CQUANT", thread_ts="later")
    with company.db.transaction() as conn:
        conn.execute("UPDATE turns SET status='completed' WHERE task_id=%s", (later["task_id"],))
        review, inputs, _ = review_snapshot(conn, company, ["UHUMAN"], datetime.now(UTC))
    target = next(row for row in review["evidence"] if row.get("kind") == "replay_input" and "target" in row["text"])
    assert "LATER_OBSERVER_ONLY" in json.dumps(review)
    assert "LATER_OBSERVER_ONLY" not in json.dumps(target["employee_context"])
    assert target["employee_context"]["message_count"] == 1
    assert all(s["title"] == "Current system evidence" for s in target["employee_context"]["approved_sources"])
    control = next(row for row in review["evidence"] if row.get("kind") == "replay_input" and "control" in row["text"])
    runner.store.collect()
    job = runner.store.next_job()
    job["payload"]["snapshot"] = runner.github.snapshot()
    finding = {
        "problem_key": "director_history_evidence_ignored", "title": "Impossible historical context repair",
        "problem": "The employee allegedly ignored evidence that only the observer has.",
        "reproduction": "Replay the saved employee request with its actual context.",
        "expected": "Require an observer message ID as if it were an approved employee source.",
        "category": "bot_behavior", "hypothesis": "Fixture deliberately reproduces the production misdiagnosis.",
        "paths": [ROLE_PATH], "evidence_keys": [target["key"], control["key"]],
        "evaluation": {"mode": "prompt_replay", "success_criterion": "Fixture impossible output must be rejected early.",
                       "cases": [{"purpose": "target", "request_key": target["key"],
                                  "expected": {"status": "complete", "source_ids": ["message:observer-only"]}},
                                 {"purpose": "control", "request_key": control["key"], "expected": {"status": "complete"}}]},
    }
    with pytest.raises(ValueError, match="replay_expectation_requires_unknown_source"):
        runner.store.finish_triage(job, Triage(finding=finding, reason="Fixture of the actual invalid criterion"))
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM maintenance_jobs WHERE kind='repair'").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM maintenance_calls").fetchone()["n"] == 0
    assert not runner.provider.requests and inputs


def test_evaluator_rejects_unauthorized_actions_even_when_primary_expectation_passes(test_roles):
    response = AgentDecision(say="Unauthorized delegation", status="wait",
                             delegations=[{"agent": "outsider", "instruction": "Do the work"}])
    result = score(response, Expectations(status="wait"), test_roles["director"],
                   {"employees": [role.model_dump() for role in test_roles.values()]},
                   {"approved_sources": [], "task": {"requester": None}})
    assert result["checks"]["status"] and not result["passed"]


@pytest.mark.integration
async def test_changed_saved_replay_inputs_are_detected(company):
    runner = runner_with_recorded_requests(company)
    await runner.tick()
    job = repair(company)
    first = next(iter(job["payload"]["replay_inputs"].values()))
    first["request"]["prompt"] += "changed after criteria were frozen"
    with pytest.raises(ValueError, match="replay_inputs_changed"):
        verify_plan(job["payload"])
    with company.db.transaction() as conn:
        conn.execute("UPDATE maintenance_jobs SET payload=%s WHERE id=%s", (Jsonb(job["payload"]), job["id"]))
    assert (await runner.tick())["reason"] == "replay_inputs_changed"


@pytest.mark.integration
async def test_crash_after_recording_replay_response_does_not_call_model_twice(company, monkeypatch):
    runner = runner_with_recorded_requests(company)
    for _ in range(2):
        await runner.tick()
    saved = runner.store.save

    def crash_before_checkpoint(job_id, state, **kwargs):
        if state == "evaluate" and kwargs.get("payload", {}).get("replay_results"):
            raise RuntimeError("Simulated crash after response was durably recorded")
        return saved(job_id, state, **kwargs)

    monkeypatch.setattr(runner.store, "save", crash_before_checkpoint)
    with pytest.raises(RuntimeError, match="Simulated crash"):
        await runner.tick()
    assert len(runner.provider.requests) == 3
    resumed = Maintainer(company, config(), github=runner.github, provider=runner.provider)
    await resumed.tick()
    assert len(resumed.provider.requests) == 3
    for _ in range(9):
        await resumed.tick()
    assert repair(company)["state"] == "pr_open"
    assert len(resumed.provider.requests) == 6


@pytest.mark.integration
async def test_recorded_permission_drift_cannot_be_evaluated_as_the_current_base(company):
    runner = runner_with_recorded_requests(company)
    roles = json.loads(runner.github.original)
    next(role for role in roles if role["id"] == "director")["version"] = "changed-after-recording"
    runner.github.original = json.dumps(roles, indent=2)
    await runner.tick()
    result = await runner.tick()
    assert result == {"state": "blocked", "reason": "recorded_permissions_do_not_match_base_role"}
    assert runner.github.published == 0 and len(runner.provider.requests) == 2


@pytest.mark.integration
async def test_revoked_owner_stops_a_saved_evaluation_before_another_model_call(company):
    runner = runner_with_recorded_requests(company)
    for _ in range(2):
        await runner.tick()
    company.settings.slack_allowed_users = []
    assert await runner.tick() == {"state": "blocked", "reason": "review_owner_authorization_changed"}
    assert len(runner.provider.requests) == 2 and runner.github.published == 0


@pytest.mark.integration
async def test_documentation_checks_cannot_be_used_to_bypass_prompt_evaluation(company):
    runner = runner_with_recorded_requests(company)
    await runner.tick()
    payload = repair(company)["payload"]
    plan = {"mode": "documentation", "success_criterion": "Correct a documented behavior description.", "cases": []}
    payload["finding"]["evaluation"] = plan
    payload["evaluation_plan_digest"] = digest(plan)
    payload["changes"] = {"docs/roles.md": "Corrected documentation"}
    validate_candidate(payload)
    payload["changes"] = {ROLE_PATH: "Must require actual prompt replay"}
    with pytest.raises(ValueError, match="documentation_cannot_validate"):
        validate_candidate(payload)
