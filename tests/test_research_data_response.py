"""Real PostgreSQL/file gates; native event fixtures, no live model or scientific run."""

import json

from psycopg.types.json import Jsonb

from quant_company.contracts import ProviderRequest
from quant_company.providers.codex_runner import ProcessResult, parse_result, request_digest
from quant_company.research.program_controller import ProgramController

from .test_research_programs import program, ready, task_proposal  # noqa: F401


def native_response(request, payload):
    events = [{"type": "thread.started", "thread_id": "synthetic-data-thread"},
              {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(payload)}},
              {"type": "turn.completed", "usage": {}}]
    process = ProcessResult(0, "\n".join(json.dumps(item) for item in events).encode(), b"")
    return parse_result(ProviderRequest.model_validate(request), process, 900)


def pending_turn(company, task_id):
    with company.db.transaction() as conn:
        return str(conn.execute("SELECT id FROM turns WHERE task_id=%s AND status='queued' ORDER BY sequence LIMIT 1",
                                (task_id,)).fetchone()["id"])


def data_stage(h):
    with h.company.db.transaction() as conn:
        task_id = h.program_store.propose(conn, h.program_id, task_proposal(), actor="researcher_kr")
    assert ProgramController(h.company).tick()["state"] == "running"
    with h.company.db.transaction() as conn:
        stage = conn.execute("SELECT * FROM research_mission_stages WHERE program_id=%s AND stage='program_data'",
                             (h.program_id,)).fetchone()
    return task_id, stage


def test_data_request_freezes_new_contract_and_preserves_legacy_request(program):  # noqa: F811
    h = program
    _, stage = data_stage(h)
    turn_id = pending_turn(h.company, stage["task_id"])
    prepared = h.company.prepare_turn(turn_id)["request"]
    assert prepared["output_contract"] == "research_data_v1"
    assert prepared["model"] == h.company.roles["data"].model
    # Represent an already-frozen pre-cutover request, without the optional field.
    legacy = prepared | {"output_contract": "agent_decision"}
    legacy.pop("output_contract")
    original_digest = request_digest(ProviderRequest.model_validate(legacy))
    with h.company.db.transaction() as conn:
        conn.execute("UPDATE turns SET request=%s WHERE id=%s", (Jsonb(legacy), turn_id))
    frozen = h.company.prepare_turn(turn_id)["request"]
    assert frozen["output_contract"] == "agent_decision"
    assert request_digest(ProviderRequest.model_validate(frozen)) == original_digest


def test_native_data_artifact_still_requires_evidence_and_independent_gate(program):  # noqa: F811
    h = program
    task_id, stage = data_stage(h)
    turn_id = pending_turn(h.company, stage["task_id"])
    prepared = h.company.prepare_turn(turn_id)["request"]
    response = native_response(prepared, {"kind": "complete", "path": "", "offset": 0,
                                          "result_json": json.dumps(ready())})
    result = h.company.commit_turn(turn_id, response)
    assert result["incomplete_source"]
    with h.company.db.transaction() as conn:
        assert conn.execute("SELECT state FROM research_program_tasks WHERE id=%s", (task_id,)).fetchone()["state"] == "proposed"
        assert conn.execute("SELECT count(*) AS n FROM research_program_reservations").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM research_jobs").fetchone()["n"] == 0
    path = stage["context"]["evidence_sources"][0]["file"]
    turn_id = pending_turn(h.company, stage["task_id"])
    prepared = h.company.prepare_turn(turn_id)["request"]
    h.company.commit_turn(turn_id, native_response(prepared, {
        "kind": "read", "path": path, "offset": 0, "result_json": ""}))
    assessment = ready(decision="blocked", rationale="Synthetic evidence is not market readiness",
                       point_in_time=False, coverage=False, executable_prices=False)
    turn_id = pending_turn(h.company, stage["task_id"])
    prepared = h.company.prepare_turn(turn_id)["request"]
    h.company.commit_turn(turn_id, native_response(prepared, {
        "kind": "complete", "path": "", "offset": 0, "result_json": json.dumps(assessment)}))
    assert ProgramController(h.company).tick()["state"] == "completed"
    with h.company.db.transaction() as conn:
        actual = conn.execute("SELECT data_assessment,state FROM research_program_tasks WHERE id=%s", (task_id,)).fetchone()
        assert actual["state"] == "assessed" and actual["data_assessment"]["decision"] == "blocked"
        assert conn.execute("SELECT count(*) AS n FROM research_missions").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM research_program_reservations").fetchone()["n"] == 0
