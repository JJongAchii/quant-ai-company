"""Planning runs on real PostgreSQL; synthetic employees through fixture responses, no model calls."""

import pytest

from quant_company.config import Settings

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
