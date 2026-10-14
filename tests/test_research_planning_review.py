"""Planning critique and decision on real PostgreSQL; fixture employees, no model calls."""

import json

import pytest

from quant_company.company import PolicyError
from quant_company.research.planning_controller import PlanningController

from .test_research_conditional import conditional  # noqa: F401
from .test_research_planning_contracts import candidate
from .test_research_planning_contracts import decision as decision_value
from .test_research_planning_contracts import review as critique_review
from .test_research_planning_runs import (  # noqa: F401
    complete,
    counts,
    planning,
    queued_turn,
    read,
    read_all,
    slate,
)
from .test_research_programs import program  # noqa: F401


def test_decision_table_migrates_twice(company):
    company.db.migrate()
    with company.db.transaction() as conn:
        assert conn.execute("SELECT to_regclass('research_planning_decisions') AS name").fetchone()["name"]


def stage_named(h, name):
    with h.company.db.transaction() as conn:
        return conn.execute("SELECT * FROM research_mission_stages WHERE planning_run_id IS NOT NULL AND stage=%s",
                            (name,)).fetchone()


def to_critique(h, *candidates):
    PlanningController(h.company).tick()
    stage = stage_named(h, "planning_slate")
    complete(h, read_all(h, stage, queued_turn(h, stage["task_id"])),
             slate(*(candidates or (candidate(), candidate(title="Second", execution_timing="same_day_close_fill")))))
    assert PlanningController(h.company).tick()["state"] == "running"
    return stage_named(h, "planning_critique")


def read_files(h, stage, turn, paths):
    for path in paths:
        offset = 0
        while offset is not None:
            turn = read(h, turn, path, offset)
            with h.company.db.transaction() as conn:
                offset = conn.execute("""SELECT next_offset FROM research_stage_reads WHERE stage_id=%s AND path=%s
                    ORDER BY character_offset DESC LIMIT 1""", (stage["id"], path)).fetchone()["next_offset"]
    return turn


def test_slate_moves_to_critique_with_triage_in_the_slate_file(planning):  # noqa: F811
    h = planning
    stage = to_critique(h)
    context = stage["context"]
    assert (stage["actor"], context["candidate_ordinals"], context["required_planning_reads"]) == (
        "financial_strategist", [1, 2], ["planning/slate.json"])
    with open(context["_private_files"]["planning/slate.json"]["path"]) as stream:
        document = json.load(stream)
    assert [item["triage"]["outcome"] for item in document] == ["eligible", "needs_evaluator"]
    with h.company.db.transaction() as conn:
        assert conn.execute("SELECT state FROM research_questions WHERE id=%s", (h.question["id"],)).fetchone()["state"] == "open"
        assert conn.execute("SELECT state FROM research_planning_runs").fetchone()["state"] == "critique"


def test_critique_reviews_every_candidate_once(planning):  # noqa: F811
    h = planning
    stage = to_critique(h)
    turn = read_files(h, stage, queued_turn(h, stage["task_id"]), ["planning/slate.json"])
    assert complete(h, turn, {"reviews": [critique_review(1)]})["planning_output_rejected"]


def test_cited_counter_evidence_must_be_read_completely(planning):  # noqa: F811
    h = planning
    stage = to_critique(h)
    turn = read_files(h, stage, queued_turn(h, stage["task_id"]), ["planning/slate.json"])
    counter = [{"source_id": "fixture:baseline", "location": "char:0", "quote": "Synthetic original: point-in-time"}]
    value = {"reviews": [critique_review(1, counter_evidence=counter), critique_review(2)]}
    assert complete(h, turn, value)["incomplete_source"]
    turn = read_files(h, stage, queued_turn(h, stage["task_id"]),
                      [item["file"] for item in stage["context"]["evidence_sources"]])
    complete(h, turn, value)
    assert stage_named(h, "planning_critique")["state"] == "received"


def to_decision(h, *candidates):
    stage = to_critique(h, *candidates)
    turn = read_files(h, stage, queued_turn(h, stage["task_id"]), ["planning/slate.json"])
    reviews = [critique_review(n) for n in stage["context"]["candidate_ordinals"]]
    complete(h, turn, {"reviews": reviews})
    assert PlanningController(h.company).tick()["state"] == "running"
    return stage_named(h, "planning_decision")


def decide(h, stage, value):
    turn = read_files(h, stage, queued_turn(h, stage["task_id"]), stage["context"]["required_planning_reads"])
    return complete(h, turn, value)


def test_decision_cannot_override_triage(planning):  # noqa: F811
    h = planning
    stage = to_decision(h)
    assert stage["context"]["service_order"] == [1, 2] and stage["context"]["eligible_ordinals"] == [1]
    routes = [{"candidate_ordinal": 1, "route": "defer", "rationale": "Hold"},
              {"candidate_ordinal": 2, "route": "experiment", "rationale": "Owner prefers close fill"}]
    result = decide(h, stage, decision_value(order=[2, 1], selected_ordinal=2, routes=routes,
                                             override_reason="Prefers the second"))
    assert result["planning_output_rejected"]


def test_order_change_needs_override_reason(planning):  # noqa: F811
    h = planning
    stage = to_decision(h)
    routes = [{"candidate_ordinal": 1, "route": "experiment", "rationale": "Eligible"},
              {"candidate_ordinal": 2, "route": "defer", "rationale": "Evaluator"}]
    assert decide(h, stage, decision_value(order=[2, 1], routes=routes))["planning_output_rejected"]


def test_follow_up_refs_are_checked_before_receipt(planning):  # noqa: F811
    h = planning
    stage = to_decision(h)
    follow = {"question": "Does the close-fill variant need a new evaluator?", "decision_to_inform": "Program revision",
              "market": "kr_etf", "search_terms_en": ["close fill"], "source_refs": ["not:in-project"]}
    assert decide(h, stage, decision_value(follow_up_questions=[follow]))["planning_output_rejected"]


def test_decision_completes_the_shadow_run_with_a_proposed_follow_up(planning):  # noqa: F811
    h = planning
    before = counts(h)
    stage = to_decision(h)
    follow = {"question": "Does the close-fill variant need a new evaluator?", "decision_to_inform": "Program revision",
              "market": "kr_etf", "search_terms_en": ["close fill"]}
    decide(h, stage, decision_value(follow_up_questions=[follow]))
    assert PlanningController(h.company).tick() == {"state": "completed", "run_state": "completed",
                                                    "outcome": "shadow_recorded"}
    with h.company.db.transaction() as conn:
        routes = [r["route"] for r in conn.execute(
            "SELECT route FROM research_planning_candidates ORDER BY ordinal")]
        record = conn.execute("SELECT override,service_order FROM research_planning_decisions").fetchone()
        follow_up = conn.execute("""SELECT state,origin,parent_question_id FROM research_questions
            WHERE origin='follow_up'""").fetchone()
        question = conn.execute("SELECT state FROM research_questions WHERE id=%s", (h.question["id"],)).fetchone()
    assert routes == ["experiment", "defer"] and record["override"] is False and record["service_order"] == [1, 2]
    assert (follow_up["state"], str(follow_up["parent_question_id"])) == ("proposed", h.question["id"])
    assert question["state"] == "waiting"
    assert counts(h) == before


def test_single_candidate_run_completes(planning):  # noqa: F811
    h = planning
    stage = to_decision(h, candidate())
    routes = [{"candidate_ordinal": 1, "route": "experiment", "rationale": "Only eligible candidate"}]
    decide(h, stage, decision_value(order=[1], routes=routes))
    assert PlanningController(h.company).tick()["outcome"] == "shadow_recorded"


def test_run_report_traces_a_completed_run(planning):  # noqa: F811
    from quant_company.research.planning_commands import command

    h = planning
    stage = to_decision(h)
    decide(h, stage, decision_value())
    PlanningController(h.company).tick()
    report = command(h.company.settings, "run-report", run=str(stage["planning_run_id"]), company=h.company)
    assert report["run"]["outcome"] == "shadow_recorded" and report["decision"]["service_order"] == [1, 2]
    assert [c["ordinal"] for c in report["candidates"]] == [1, 2]
    assert {r["kind"] for r in report["reviews"]} == {"triage", "critique"}
    assert report["usage"]["requests"] > 0 and report["stages"][0]["stage"] == "planning_slate"
    assert all(isinstance(value, int) for value in report["usage"].values())


def test_run_report_for_a_run_in_progress(planning):  # noqa: F811
    from quant_company.research.planning_commands import command

    h = planning
    stage = to_critique(h)
    report = command(h.company.settings, "run-report", run=str(stage["planning_run_id"]), company=h.company)
    assert report["decision"] is None and report["run"]["state"] == "critique"


def test_run_report_rejects_a_malformed_or_unknown_run(company):
    from quant_company.research.planning_commands import command

    for run in ("not-a-run-id", "00000000-0000-0000-0000-000000000000"):
        with pytest.raises(PolicyError, match="planning run"):
            command(company.settings, "run-report", run=run, company=company)
