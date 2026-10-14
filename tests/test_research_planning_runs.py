"""Planning runs on real PostgreSQL; synthetic employees through fixture responses, no model calls."""

import psycopg
import pytest

from quant_company.config import Settings
from quant_company.research.planning_controller import PlanningController
from quant_company.research.planning_store import QuestionStore

from .test_research_conditional import conditional  # noqa: F401
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
