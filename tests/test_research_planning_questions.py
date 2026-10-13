"""Research questions on real PostgreSQL; synthetic owner events only, no model calls."""

import psycopg
import pytest

from .test_research_missions import Harness

PLANNING_TABLES = ("research_questions", "research_question_revisions", "research_evidence_sets", "research_gaps")


def test_planning_schema_is_additive_and_migrates_twice(company):
    company.db.migrate()
    with company.db.transaction() as conn:
        for table in PLANNING_TABLES:
            assert conn.execute("SELECT to_regclass(%s) AS name", (table,)).fetchone()["name"]


def test_open_question_requires_owner_activation_event(company):
    h = Harness(company)
    with pytest.raises(psycopg.errors.CheckViolation):
        with company.db.transaction() as conn:
            conn.execute("""INSERT INTO research_questions(id,project_id,owner_user,project_revision,digest,title,
                state,origin,created_by) VALUES(gen_random_uuid(),%s,'UHUMAN',1,%s,'q','open','owner_request','director')""",
                (h.project["project_id"], "0" * 64))
