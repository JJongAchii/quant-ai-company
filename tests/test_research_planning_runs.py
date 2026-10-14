"""Planning runs on real PostgreSQL; synthetic employees through fixture responses, no model calls."""

import json

import psycopg
import pytest

from quant_company.company import Company
from quant_company.config import Settings
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.research.planning_controller import PlanningController
from quant_company.research.planning_store import QuestionStore
from quant_company.research.runner import ResearchRunner

from .test_research_conditional import conditional  # noqa: F401
from .test_research_missions import Harness
from .test_research_planning_contracts import candidate
from .test_research_planning_questions import approve, spec
from .test_research_programs import program  # noqa: F401

PLANNING_RUN_TABLES = ("research_planning_runs", "research_planning_candidates", "research_planning_reviews")


def test_planning_run_schema_migrates_twice(company):
    company.db.migrate()
    with company.db.transaction() as conn:
        for table in PLANNING_RUN_TABLES:
            assert conn.execute("SELECT to_regclass(%s) AS name", (table,)).fetchone()["name"]
        columns = {row["column_name"] for row in conn.execute("""SELECT column_name FROM information_schema.columns
            WHERE table_name IN ('research_mission_stages','research_gaps')""")}
    assert {"planning_run_id", "run_id", "candidate_id"} <= columns


def test_stage_flag_requires_planning_and_research():
    base = dict(database_url="postgresql://localhost/x", operator_token="test-token-with-more-than-24-characters",
                model_provider="fixture", fixture_mode=True)
    with pytest.raises(ValueError, match="Planning stages require"):
        Settings(**base, research_planning_stages_enabled=True)
    Settings(**base, research_planning_stages_enabled=True, research_planning_enabled=True,
             company_research_enabled=True)


@pytest.fixture
def planning(conditional):  # noqa: F811
    h = conditional
    h.company.settings.research_planning_enabled = True
    h.company.settings.research_planning_stages_enabled = True
    with h.company.db.transaction() as conn:
        project = h.company._project(conn, h.project["project_id"])
        h.question = QuestionStore(h.company).draft(conn, project, spec(), actor="director", program_id=h.program_id)
    approve(h, h.question)
    return h


def planning_stage(h):
    with h.company.db.transaction() as conn:
        return conn.execute("SELECT * FROM research_mission_stages WHERE planning_run_id IS NOT NULL").fetchone()


def queued_turn(h, task_id):
    with h.company.db.transaction() as conn:
        row = conn.execute("SELECT id FROM turns WHERE task_id=%s AND status='queued'", (task_id,)).fetchone()
    return str(row["id"]) if row else None


def test_run_starts_once_and_stage_is_reused(planning):
    h = planning
    assert PlanningController(h.company).tick()["state"] == "running"
    stage = planning_stage(h)
    assert (stage["stage"], stage["actor"], stage["attempt"]) == ("planning_slate", "researcher_kr", 1)
    assert stage["program_id"] is None and stage["mission_id"] is None
    context = stage["context"]
    assert context["allowed_source_ids"] == ["fixture:baseline"]
    assert [e["name"] for e in context["allowed_envelopes"]] == ["etf"]
    assert context["allowed_envelopes"][0]["engine_rule"]["id"] == "kr-research-python-v2"
    assert context["required_planning_reads"][:2] == ["planning/question.json", "planning/cards.json"]
    assert PlanningController(h.company).tick()["state"] == "waiting"
    with h.company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM research_planning_runs").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM research_stage_attempts WHERE stage_id=%s",
                            (stage["id"],)).fetchone()["n"] == 1
    with pytest.raises(psycopg.errors.CheckViolation):
        with h.company.db.transaction() as conn:
            conn.execute("UPDATE research_mission_stages SET program_id=%s WHERE id=%s", (h.program_id, stage["id"]))


def test_disabled_stages_start_nothing(planning):
    planning.company.settings.research_planning_stages_enabled = False
    assert PlanningController(planning.company).tick() == {"state": "disabled"}
    assert planning_stage(planning) is None


def respond(h, turn_id, **decision):
    h.company.prepare_turn(turn_id)
    return h.company.commit_turn(turn_id, ProviderResponse(request_id=turn_id, provider="fixture",
                                                           decision=AgentDecision(say="", **decision)))


def read(h, turn_id, path, offset=0):
    respond(h, turn_id, status="continue", tools=[{"name": "research_control", "arguments": {
        "action": "read_stage_file", "path": path, "offset": offset}}])
    with h.company.db.transaction() as conn:
        task = conn.execute("SELECT task_id FROM turns WHERE id=%s", (turn_id,)).fetchone()["task_id"]
    return queued_turn(h, task)


def read_all(h, stage, turn_id):
    paths = [*stage["context"]["required_planning_reads"], *[s["file"] for s in stage["context"]["evidence_sources"]]]
    for path in paths:
        offset = 0
        while offset is not None:
            turn_id = read(h, turn_id, path, offset)
            with h.company.db.transaction() as conn:
                offset = conn.execute("""SELECT next_offset FROM research_stage_reads WHERE stage_id=%s AND path=%s
                    ORDER BY character_offset DESC LIMIT 1""", (stage["id"], path)).fetchone()["next_offset"]
    return turn_id


def slate(*candidates, reason=None):
    return {"candidates": list(candidates), "no_candidate_reason": reason, "reading_notes": "Read question, cards, original"}


def complete(h, turn_id, value):
    return respond(h, turn_id, status="complete", artifacts=[{"title": "slate", "content": json.dumps(value)}])


def test_slate_prompt_is_native_and_scoped(planning):
    h = planning
    PlanningController(h.company).tick()
    stage = planning_stage(h)
    prepared = h.company.prepare_turn(queued_turn(h, stage["task_id"]))
    assert prepared["request"]["output_contract"] == "research_stage_v1"
    prompt = prepared["request"]["prompt"]
    assert "STAGE: planning_slate" in prompt and "action=read" in prompt
    data = json.loads(prompt.split("MISSION DATA JSON:\n", 1)[1])
    assert data["allowed_source_ids"] == ["fixture:baseline"] and "PlanningCandidate" in json.dumps(data["output_schema"])


def test_slate_needs_planning_reads_and_complete_cited_originals(planning):
    h = planning
    PlanningController(h.company).tick()
    stage = planning_stage(h)
    turn = queued_turn(h, stage["task_id"])
    assert complete(h, turn, slate(candidate()))["incomplete_planning_read"]
    turn = read_all(h, stage, queued_turn(h, stage["task_id"]))
    complete(h, turn, slate(candidate()))
    assert planning_stage(h)["state"] == "received"


def test_invalid_slate_corrects_in_attempt_then_holds(planning):
    h = planning
    PlanningController(h.company).tick()
    stage = planning_stage(h)
    turn = read_all(h, stage, queued_turn(h, stage["task_id"]))
    outside = candidate(envelope="not-approved")
    for _ in range(2):
        assert complete(h, turn, slate(outside))["planning_output_rejected"]
        turn = queued_turn(h, stage["task_id"])
    assert complete(h, turn, slate(outside))["planning_held"]
    with h.company.db.transaction() as conn:
        held = conn.execute("SELECT state,context FROM research_mission_stages WHERE id=%s", (stage["id"],)).fetchone()
        gap = conn.execute("SELECT kind,resolver FROM research_gaps WHERE run_id=%s", (stage["planning_run_id"],)).fetchone()
        run = conn.execute("SELECT state,hold_reason FROM research_planning_runs").fetchone()
    assert held["state"] == "waiting" and held["context"]["_planning_hold"]["reason"] == "planning_output_contract"
    assert (gap["kind"], gap["resolver"]) == ("output_contract", "operator")
    assert (run["state"], run["hold_reason"]) == ("held", "planning_output_contract")


def test_turn_cap_holds_the_stage_with_a_gap(planning, monkeypatch):
    import quant_company.research.planning_controller as controller

    h = planning
    monkeypatch.setitem(controller.STAGE_TURNS, "planning_slate", 2)
    PlanningController(h.company).tick()
    stage = planning_stage(h)
    turn = read(h, queued_turn(h, stage["task_id"]), "planning/question.json")
    # The same chunk again is answered without a new read; the cap still counts the turn.
    respond(h, turn, status="continue", tools=[{"name": "research_control", "arguments": {
        "action": "read_stage_file", "path": "planning/question.json", "offset": 0}}])
    assert queued_turn(h, stage["task_id"]) is None
    with h.company.db.transaction() as conn:
        held = conn.execute("SELECT context FROM research_mission_stages WHERE id=%s", (stage["id"],)).fetchone()
        gap = conn.execute("SELECT kind FROM research_gaps WHERE run_id=%s", (stage["planning_run_id"],)).fetchone()
    assert held["context"]["_planning_hold"]["reason"] == "planning_read_budget_exhausted"
    assert gap["kind"] == "read_budget"


def counts(h):
    with h.company.db.transaction() as conn:
        return {table: conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
                for table in ("outbox", "research_program_tasks", "research_jobs")}


def test_received_slate_records_candidates_and_triage_without_program_effects(planning):
    h = planning
    before = counts(h)
    PlanningController(h.company).tick()
    stage = planning_stage(h)
    complete(h, read_all(h, stage, queued_turn(h, stage["task_id"])), slate(candidate()))
    assert PlanningController(h.company).tick()["state"] == "running"
    with h.company.db.transaction() as conn:
        row = conn.execute("SELECT * FROM research_planning_candidates").fetchone()
        review = conn.execute("SELECT payload FROM research_planning_reviews WHERE candidate_id=%s AND kind='triage'",
                              (row["id"],)).fetchone()
        question = conn.execute("SELECT state FROM research_questions WHERE id=%s", (h.question["id"],)).fetchone()
    assert row["ordinal"] == 1 and row["task_digest"] and review["payload"]["outcome"] == "eligible"
    assert question["state"] == "open"
    assert counts(h) == before


def test_empty_slate_completes_without_candidates(planning):
    h = planning
    PlanningController(h.company).tick()
    stage = planning_stage(h)
    complete(h, read_all(h, stage, queued_turn(h, stage["task_id"])), slate(reason="No candidate is justified"))
    assert PlanningController(h.company).tick()["run_state"] == "completed"
    with h.company.db.transaction() as conn:
        run = conn.execute("SELECT state,outcome FROM research_planning_runs").fetchone()
    assert (run["state"], run["outcome"]) == ("completed", "no_candidate")


def test_failed_triage_opens_a_linked_gap(planning):
    h = planning
    PlanningController(h.company).tick()
    stage = planning_stage(h)
    complete(h, read_all(h, stage, queued_turn(h, stage["task_id"])),
             slate(candidate(execution_timing="same_day_close_fill")))
    PlanningController(h.company).tick()
    with h.company.db.transaction() as conn:
        gap = conn.execute("SELECT kind,resolver,candidate_id FROM research_gaps WHERE run_id IS NOT NULL").fetchone()
    assert (gap["kind"], gap["resolver"]) == ("execution_timing", "owner") and gap["candidate_id"]


def test_run_and_daily_request_limits(planning, monkeypatch):
    import quant_company.research.planning_controller as controller

    h = planning
    PlanningController(h.company).tick()
    stage = planning_stage(h)
    turn = read(h, queued_turn(h, stage["task_id"]), "planning/question.json")
    monkeypatch.setitem(controller.PLANNING_LIMITS, "owner_day_requests", 1)
    assert h.company.prepare_turn(turn) == {"state": "defer", "seconds": 3600, "reason": "planning_daily_request_limit"}
    monkeypatch.setitem(controller.PLANNING_LIMITS, "owner_day_requests", 120)
    monkeypatch.setitem(controller.PLANNING_LIMITS, "run_requests", 1)
    assert h.company.prepare_turn(turn)["reason"] == "planning_run_request_limit"
    assert planning_stage(h)["context"]["_planning_hold"]["reason"] == "planning_run_request_limit"


def test_native_output_failures_retry_then_hold(planning):
    h = planning
    PlanningController(h.company).tick()
    for _ in range(3):
        stage = planning_stage(h)
        turn = queued_turn(h, stage["task_id"])
        h.company.prepare_turn(turn)
        h.company.block_turn(turn, "invalid_output")
        with h.company.db.transaction() as conn:
            conn.execute("UPDATE research_mission_stages SET retry_at=now() WHERE id=%s AND retry_at IS NOT NULL",
                         (stage["id"],))
        PlanningController(h.company).tick()
    stage = planning_stage(h)
    assert stage["attempt"] == 3 and stage["context"]["_planning_hold"]["reason"] == "planning_output_contract"


def test_restart_reuses_the_running_attempt(planning):
    h = planning
    PlanningController(h.company).tick()
    first = planning_stage(h)
    h.company = Company(h.company.settings, h.company.roles)
    assert PlanningController(h.company).tick()["state"] == "waiting"
    again = planning_stage(h)
    assert (again["attempt"], again["task_id"]) == (first["attempt"], first["task_id"])


def test_planning_runs_without_autonomous_research(planning):
    h = planning
    h.company.settings.company_autonomous_research_enabled = False
    assert ResearchRunner(h.company).tick()["state"] == "running"
    stage = planning_stage(h)
    assert h.company.prepare_turn(queued_turn(h, stage["task_id"]))["state"] == "ready"


def test_maintenance_intake_reports_planning_output_hold(planning):
    from datetime import timedelta
    from importlib.resources import files

    from quant_company.company import now
    from quant_company.maintenance.problems import failure_observations

    h = planning
    PlanningController(h.company).tick()
    stage = planning_stage(h)
    turn = read_all(h, stage, queued_turn(h, stage["task_id"]))
    for _ in range(3):
        complete(h, turn, slate(candidate(envelope="not-approved")))
        turn = queued_turn(h, stage["task_id"]) or turn
    with h.company.db.transaction() as conn:
        # The maintenance service applies its own schema (maintenance.store.Store.initialize).
        conn.execute(files("quant_company.maintenance").joinpath("schema.sql").read_text())
        rows = failure_observations(conn, h.company, "UHUMAN", now() - timedelta(hours=1))
    found = [row for row in rows if row["detail"].get("planning_run_id") == str(stage["planning_run_id"])]
    assert found and found[0]["detail"]["error"] == "planning_output_contract"


@pytest.fixture
def empty_planning(research):
    research.settings.research_planning_enabled = True
    research.settings.research_planning_stages_enabled = True
    h = Harness(research)
    with research.db.transaction() as conn:
        project = research._project(conn, h.project["project_id"])
        h.question = QuestionStore(research).draft(conn, project, spec(), actor="director")
    approve(h, h.question)
    with research.db.transaction() as conn:
        # Same as the program fixtures: owner turns would otherwise outrank the planning stage.
        conn.execute("UPDATE turns SET status='stale'")
        conn.execute("UPDATE tasks SET status='completed'")
    return h


def test_question_without_sources_runs_and_completes_empty(empty_planning):
    # A project without literature or references is a likely first question.
    h = empty_planning
    assert PlanningController(h.company).tick()["state"] == "running"
    stage = planning_stage(h)
    assert stage["context"]["allowed_source_ids"] == [] and stage["context"]["evidence_sources"] == []
    complete(h, read_all(h, stage, queued_turn(h, stage["task_id"])), slate(reason="The evidence set has no sources"))
    assert PlanningController(h.company).tick()["run_state"] == "completed"
