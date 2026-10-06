"""Real PostgreSQL with synthetic evidence/native events; no model or research execution."""

import json

import pytest
from psycopg.types.json import Jsonb

from quant_company.research.program_contracts import DataAssessment
from quant_company.research.program_controller import ProgramController, data_assessment_output_schema

from .test_research_conditional import conditional, conditional_ready  # noqa: F401
from .test_research_data_response import native_response, pending_turn
from .test_research_programs import program, task_proposal  # noqa: F401


def begin_scoped_data(h):
    with h.company.db.transaction() as conn:
        task_id = h.program_store.propose(conn, h.program_id, task_proposal(mode="novel_hypothesis"),
                                          actor="researcher_kr")
    controller = ProgramController(h.company)
    assert controller.tick()["state"] == "running"
    with h.company.db.transaction() as conn:
        stage = conn.execute("SELECT * FROM research_mission_stages WHERE program_id=%s AND stage='program_data'",
                             (h.program_id,)).fetchone()
    return task_id, stage, controller


def test_scoped_prompt_uses_selected_scope_and_preserves_previously_frozen_request(conditional):  # noqa: F811
    h = conditional
    _, stage, _ = begin_scoped_data(h)
    turn_id = pending_turn(h.company, stage["task_id"])
    request = h.company.prepare_turn(turn_id)["request"]
    prompt = request["prompt"]
    context = json.loads(prompt.split("MISSION DATA JSON:\n", 1)[1])
    schema = context["output_schema"]
    scope = h.program_spec.envelopes[0].template.research_scope.model_dump(mode="json")
    assert schema["properties"]["research_scope"] == {"$ref": "#/$defs/ResearchScope"}
    assert context["research_scopes"]["etf"] == scope
    assert schema["properties"]["schema_version"]["const"] == 2
    assert {"schema_version", "research_scope"} <= set(schema["required"])
    assert schema["properties"]["decision"]["enum"] == ["ready", "conditional_ready", "blocked"]
    assert {"data_policy_digest", "evaluation_prices"}.isdisjoint(schema["properties"])
    assert "Do not include top-level data_policy_digest or evaluation_prices" in prompt
    # A pre-repair request remains byte-for-byte frozen, including its old inner schema.
    context["output_schema"] = DataAssessment.model_json_schema()
    frozen = request | {"prompt": prompt.split("MISSION DATA JSON:\n", 1)[0]
                       + "MISSION DATA JSON:\n" + json.dumps(context, ensure_ascii=False)}
    with h.company.db.transaction() as conn:
        conn.execute("UPDATE turns SET request=%s WHERE id=%s", (Jsonb(frozen), turn_id))
    assert h.company.prepare_turn(turn_id)["request"] == frozen


@pytest.mark.parametrize("legacy_fields", [False, True])
def test_scoped_native_artifact_records_blocked_or_rejects_mixed_version_without_admission(
        conditional, legacy_fields):  # noqa: F811
    h = conditional
    task_id, stage, controller = begin_scoped_data(h)
    paths = stage["context"]["required_data_reads"] + [
        item["file"] for item in stage["context"]["evidence_sources"]]
    for path in dict.fromkeys(paths):
        offset = 0
        while offset is not None:
            turn_id = pending_turn(h.company, stage["task_id"])
            request = h.company.prepare_turn(turn_id)["request"]
            h.company.commit_turn(turn_id, native_response(request, {
                "action": "read", "read_path": path, "read_offset": offset, "artifact_json": None}))
            with h.company.db.transaction() as conn:
                offset = conn.execute("SELECT next_offset FROM research_stage_reads WHERE stage_id=%s AND path=%s "
                    "AND character_offset=%s", (stage["id"], path, offset)).fetchone()["next_offset"]
    assessment = DataAssessment.model_validate(conditional_ready(
        h, decision="blocked", rationale="Synthetic fixture does not prove readiness")).model_dump(mode="json")
    if legacy_fields:
        assessment |= {"data_policy_digest": h.program_spec.envelopes[0].template.research_scope.data_policy_digest,
                       "evaluation_prices": True}
    turn_id = pending_turn(h.company, stage["task_id"])
    request = h.company.prepare_turn(turn_id)["request"]
    h.company.commit_turn(turn_id, native_response(request, {
        "action": "complete", "read_path": None, "read_offset": None, "artifact_json": json.dumps(assessment)}))
    assert controller.tick()["state"] == ("waiting" if legacy_fields else "completed")
    with h.company.db.transaction() as conn:
        task = conn.execute("SELECT * FROM research_program_tasks WHERE id=%s", (task_id,)).fetchone()
        current = conn.execute("SELECT * FROM research_mission_stages WHERE id=%s", (stage["id"],)).fetchone()
        if legacy_fields:
            assert task["data_assessment"] is None and task["state"] == "proposed"
            assert "Scoped assessments cannot carry a legacy" in current["error"]
        else:
            assert task["data_assessment"] == assessment and task["state"] == "assessed"
        assert task["mission_id"] is None
        for table in ("research_program_reservations", "research_jobs", "research_missions"):
            assert conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0


def test_legacy_prompt_keeps_existing_contract_and_policy_guidance():
    schema = data_assessment_output_schema({"task": {"proposal": {"envelope": "legacy"}}})
    assert schema["properties"]["schema_version"]["const"] == 1
    assert {"data_policy_digest", "evaluation_prices"} <= set(schema["properties"])
    assert {"research_scope", "packet_digest", "evaluation_price_contract_verified"}.isdisjoint(schema["properties"])
    assert schema["properties"]["decision"]["enum"] == ["ready", "exploratory_only", "blocked"]
