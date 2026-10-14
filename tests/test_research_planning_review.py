"""Planning critique and decision on real PostgreSQL; fixture employees, no model calls."""

import json

from quant_company.research.planning_controller import PlanningController

from .test_research_conditional import conditional  # noqa: F401
from .test_research_planning_contracts import candidate
from .test_research_planning_contracts import review as critique_review
from .test_research_planning_runs import (  # noqa: F401
    complete,
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
