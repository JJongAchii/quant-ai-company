"""Research questions on real PostgreSQL; synthetic owner events only, no model calls."""

import psycopg
import pytest

from quant_company.company import PolicyError
from quant_company.research.approvals import targets
from quant_company.research.planning_store import QuestionApprovalAdapter, QuestionStore, planning_tool

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


def spec(**changes):
    return {"question": "Does momentum survive costs in Korean ETFs?",
            "decision_to_inform": "Whether to register a cost-aware momentum task",
            "market": "kr_etf", "search_terms_en": ["momentum"], "search_terms_ko": ["모멘텀"], **changes}


@pytest.fixture
def planning(company):
    company.settings.research_planning_enabled = True
    return Harness(company)


def draft(h, **changes):
    with h.company.db.transaction() as conn:
        project = h.company._project(conn, h.project["project_id"])
        return QuestionStore(h.company).draft(conn, project, spec(**changes), actor="director")


def approve(h, question, *, bound=True):
    event = h.owner_event("approve", bound=bound, target={"kind": "research_question", "target_id": question["id"],
                                                         "revision": 1, "manifest_digest": question["digest"]})
    with h.company.db.transaction() as conn:
        return QuestionStore(h.company).owner_command(conn, question["id"], owner="UHUMAN", revision=1,
            manifest_digest=question["digest"], event_key=event, action="approve")


def director_task(h, conn):
    return conn.execute("SELECT * FROM tasks WHERE id=%s", (h.project["task_id"],)).fetchone()


def test_disabled_planning_changes_nothing(company):
    h = Harness(company)
    assert "research_planning" not in company.runtime_context()["capabilities"]
    with company.db.transaction() as conn:
        project = company._project(conn, h.project["project_id"])
        assert QuestionApprovalAdapter(company).targets(conn, project) == []
        with pytest.raises(PolicyError, match="disabled"):
            planning_tool(company, conn, project, director_task(h, conn), {"action": "planning_status"})


def test_draft_is_idempotent_and_waits_for_the_owner(planning):
    first, second = draft(planning), draft(planning)
    assert first["id"] == second["id"]
    assert first["state"] == "proposed" and first["activated_event_id"] is None
    assert first["spec"]["mandate"] == "unknown"


def test_owner_approval_opens_question_with_inbound_event(planning):
    opened = approve(planning, draft(planning))
    assert opened["state"] == "open" and opened["activated_event_id"].startswith("fixture:authenticated:")


def test_unbound_approval_record_cannot_open_question(planning):
    with pytest.raises(PolicyError, match="bound authenticated approval record"):
        approve(planning, draft(planning), bound=False)


def test_cancel_closes_a_proposed_question(planning):
    question = draft(planning)
    event = planning.owner_event("cancel", target={"kind": "research_question", "target_id": question["id"],
                                                   "revision": 1, "manifest_digest": question["digest"]})
    with planning.company.db.transaction() as conn:
        closed = QuestionStore(planning.company).owner_command(conn, question["id"], owner="UHUMAN", revision=1,
            manifest_digest=question["digest"], event_key=event, action="cancel")
    assert closed["state"] == "closed"


def test_open_question_limit_keeps_extra_questions_proposed(planning):
    for n in range(3):
        approve(planning, draft(planning, question=f"Fixture question number {n} for limits"))
    fourth = draft(planning, question="Fixture question number 3 for limits")
    with pytest.raises(PolicyError, match="limit"):
        approve(planning, fourth)
    with planning.company.db.transaction() as conn:
        assert QuestionStore(planning.company).snapshot(conn, fourth["id"])["state"] == "proposed"


def test_question_sources_must_belong_to_the_project(planning):
    with pytest.raises(PolicyError, match="Unavailable or unapproved source"):
        draft(planning, source_refs=["other-project:source"])


def test_tool_drafts_once_and_publishes_one_approval(planning):
    company = planning.company
    for _ in range(2):
        with company.db.transaction() as conn:
            project = company._project(conn, planning.project["project_id"])
            snapshot = planning_tool(company, conn, project, director_task(planning, conn),
                                     {"action": "planning_question_draft", "spec": spec()})
    with company.db.transaction() as conn:
        bindings = conn.execute("""SELECT count(*) AS n FROM research_approval_bindings
            WHERE target_kind='research_question' AND target_id=%s""", (snapshot["id"],)).fetchone()["n"]
        status = planning_tool(company, conn, company._project(conn, planning.project["project_id"]),
                               director_task(planning, conn), {"action": "planning_status"})
    assert bindings == 1
    assert [q["id"] for q in status["questions"]] == [snapshot["id"]]
    assert "planning_question_draft" in company.runtime_context()["capabilities"]["research_planning"]["usage"]


def test_tool_rejects_owner_authority_actions(planning):
    with planning.company.db.transaction() as conn:
        project = planning.company._project(conn, planning.project["project_id"])
        with pytest.raises(PolicyError, match="owner activates"):
            planning_tool(planning.company, conn, project, director_task(planning, conn),
                          {"action": "planning_question_approve", "question_id": "x"})


def test_pending_questions_are_separate_targets(planning):
    first = draft(planning)
    second = draft(planning, question="A second fixture question for targets")
    with planning.company.db.transaction() as conn:
        project = planning.company._project(conn, planning.project["project_id"])
        pending = [t for t in targets(planning.company, conn, project) if t.state == "pending_approval"]
    # apply_owner_command explains instead of choosing when more than one target is pending.
    assert {str(t.target_id) for t in pending} == {first["id"], second["id"]}
