"""Real PG/Git response-scope regressions; scripted staff, no scientific execution."""

import json

import pytest

from quant_company.company import PolicyError
from quant_company.research.controller import MissionController
from quant_company.research.feedback import resolve_challenges

from .test_research_programs import create_task, program  # noqa: F401


def revised_selection(h, count=1):
    create_task(h)
    previous = h.add()
    old_challenge = h.challenge(previous)
    with h.company.db.transaction() as conn:
        resolve_challenges(h.company, conn, h.snapshot(), previous.id, {
            "decision": "revise", "rationale": "Synthetic design needs revision", "responses": [{
                "challenge_id": str(old_challenge.id), "disposition": "revise",
                "rationale": "Preserve the earlier negative judgment", "source_ids": ["fixture:baseline"]}]},
            "director")
    h.invoke("reject_proposal", h.mission_id, previous.id, actor="director",
             challenge_ids=[old_challenge.id], rationale="Preserve the earlier negative judgment")
    current = h.add(supersedes_proposal_id=previous.id)
    challenges = [h.challenge(current) for _ in range(count)]
    controller = MissionController(h.company)
    assert controller.tick()["state"] == "running"
    with h.company.db.transaction() as conn:
        row = conn.execute("SELECT * FROM research_mission_stages WHERE mission_id=%s", (h.mission_id,)).fetchone()
        turn = conn.execute("SELECT id FROM turns WHERE task_id=%s AND status='queued'", (row["task_id"],)).fetchone()
    request = h.company.prepare_turn(str(turn["id"]))["request"]
    context = json.loads(request["prompt"].split("MISSION DATA JSON:\n", 1)[1])
    return current, old_challenge, challenges, request, context


@pytest.mark.parametrize("count", [1, 4])
def test_selection_schema_targets_all_current_challenges_and_excludes_history(program, count):  # noqa: F811
    h = program
    current, historical, challenges, request, context = revised_selection(h, count)
    expected = [str(challenge.id) for challenge in challenges]
    contract = context["selection_contract"]
    assert contract["proposal_id"] == str(current.id)
    assert contract["current_challenge_ids"] == expected
    assert str(historical.id) not in expected
    schema = context["output_schema"]
    assert schema["properties"]["responses"]["minItems"] == count
    assert schema["properties"]["responses"]["maxItems"] == count
    assert schema["$defs"]["ChallengeResponse"]["properties"]["challenge_id"]["enum"] == expected
    assert "Historical challenges are evidence" in request["prompt"]
    # Current and inherited objections remain available; navigation does not erase history.
    assert any(row["id"] == str(historical.id) for row in h.snapshot()["challenges"])
    assert h.snapshot()["rejections"][0]["proposal_id"] == str(historical.proposal_id)
    assert h.snapshot()["cumulative_trials"] == 0
    with h.company.db.transaction() as conn:
        assert not conn.execute("SELECT 1 FROM research_jobs").fetchone()


def test_selection_navigation_does_not_waive_revision_or_allow_historical_dispositions(program):  # noqa: F811
    h = program
    current, historical, challenges, _, context = revised_selection(h)
    capability = context["stage_capabilities"]
    assert capability["available_actions"] == ["read_stage_file", "complete_stage_artifact"]
    assert "No candidate build is created before selection" in capability["frozen_code_role"]
    assert "qualification before development evaluation" in capability["execution_sequence"]
    assert "does not certify a passed test" in capability["test_disposition"]
    assert "blocks selection and engineer implementation" in capability["revise_disposition"]
    answer = {"decision": "execute", "rationale": "Synthetic request", "responses": [{
        "challenge_id": str(challenges[0].id), "disposition": "revise",
        "rationale": "Required scientific revision", "source_ids": ["fixture:baseline"]}]}
    with pytest.raises(PolicyError, match="Required revision prevents execution"):
        with h.company.db.transaction() as conn:
            resolve_challenges(h.company, conn, h.snapshot(), current.id, answer, "director")
    answer["responses"][0]["disposition"] = "test"
    answer["responses"][0]["test_plan"] = "Verify actual fixture evidence after implementation"
    answer["responses"].append({**answer["responses"][0], "challenge_id": str(historical.id)})
    with pytest.raises(PolicyError, match="Every independent challenge needs exactly one disposition"):
        with h.company.db.transaction() as conn:
            resolve_challenges(h.company, conn, h.snapshot(), current.id, answer, "director")
    assert not h.snapshot()["trials"]
