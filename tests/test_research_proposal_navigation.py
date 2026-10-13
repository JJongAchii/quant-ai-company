"""Real PostgreSQL producer/consumer regressions; synthetic staff, no market run."""

import json
from uuid import uuid4

import pytest

from quant_company.company import PolicyError, now
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.research.controller import MissionController

from .test_research_controller import FixtureBackend, active, mission  # noqa: F401


def proposal_prompt(h, controller):
    assert controller.tick()["state"] == "running"
    row, turn_id = active(h)
    request = h.company.prepare_turn(turn_id)["request"]
    context = json.loads(request["prompt"].split("MISSION DATA JSON:\n", 1)[1])
    return row, turn_id, context


def test_first_proposal_has_no_predecessor_or_supersedes_target(mission):  # noqa: F811
    h = mission
    _, _, context = proposal_prompt(h, MissionController(h.company, backend=FixtureBackend(h.company)))
    contract = context["proposal_contract"]
    assert contract["allowed_predecessor_trial_ids"] == contract["allowed_supersedes_proposal_ids"] == []
    assert contract["required_latest_predecessor_trial_id"] is None
    schema = context["output_schema"]["properties"]
    assert schema["predecessor_trial_ids"]["maxItems"] == 0
    assert schema["supersedes_proposal_id"] == {"type": "null", "default": None}
    assert h.snapshot()["cumulative_trials"] == 0


def test_closed_unmeasured_candidate_is_evidence_and_never_an_eligible_relationship(mission):  # noqa: F811
    h = mission
    first, _ = h.finish()
    rejected = h.add()
    challenge = h.challenge(rejected)
    h.invoke("reject_proposal", h.mission_id, rejected.id, actor="director",
             challenge_ids=[challenge.id], rationale="Synthetic independent rejection")
    failed, plan = h.prepare()
    outcome = h.outcome(plan, technical=True)
    h.invoke("record_outcome", h.mission_id, failed, outcome)
    # Simulate the already-completed director disposition, not a scientific result.
    with h.company.db.transaction() as conn:
        conn.execute("UPDATE research_mission_trials SET technical_closed_at=%s WHERE id=%s", (now(), failed))
    controller = MissionController(h.company, backend=FixtureBackend(h.company))
    _, _, context = proposal_prompt(h, controller)
    contract = context["proposal_contract"]
    assert contract["allowed_predecessor_trial_ids"] == [first]
    assert contract["required_latest_predecessor_trial_id"] == first
    assert contract["allowed_supersedes_proposal_ids"] == [str(rejected.id)]
    schema = context["output_schema"]["properties"]
    assert schema["predecessor_trial_ids"]["items"]["enum"] == [first]
    assert schema["predecessor_trial_ids"]["contains"] == {"const": first}
    assert schema["supersedes_proposal_id"]["anyOf"][0]["enum"] == [str(rejected.id)]
    failed_proposal = str(plan.proposal_id)
    with pytest.raises(PolicyError, match="Predecessor must be an interpreted"):
        h.add(predecessor_trial_ids=[first, failed])
    with pytest.raises(PolicyError, match="Superseded proposal must be rejected"):
        h.add(supersedes_proposal_id=failed_proposal)
    valid = h.add(predecessor_trial_ids=[first], supersedes_proposal_id=None)
    h.challenge(valid)
    state = h.snapshot()
    assert state["cumulative_trials"] == 1
    assert len(state["trials"]) == 2
    assert any(t["id"] == failed and t["result_id"] is None for t in state["trials"])
    assert state["stage"]["stage"] == "selection"


def test_new_proposal_attempt_preserves_exact_previous_consumer_error(mission):  # noqa: F811
    h = mission
    controller = MissionController(h.company, backend=FixtureBackend(h.company))
    first, turn_id, _ = proposal_prompt(h, controller)
    value = h.proposal(predecessor_trial_ids=[str(uuid4())]).model_dump(mode="json")
    response = ProviderResponse(request_id=turn_id, provider="fixture", decision=AgentDecision(
        say="", status="complete", artifacts=[{"title": "Synthetic invalid relationship", "content": json.dumps(value)}]))
    h.company.commit_turn(turn_id, response)
    assert controller.tick()["state"] == "waiting"
    with h.company.db.transaction() as conn:
        previous = conn.execute("SELECT error FROM research_mission_stages WHERE id=%s", (first["id"],)).fetchone()["error"]
        conn.execute("UPDATE research_mission_stages SET retry_at=now() WHERE id=%s", (first["id"],))
    second, _, context = proposal_prompt(h, controller)
    assert second["id"] == first["id"] and second["attempt"] == 2
    assert "Predecessor must be an interpreted" in previous
    assert context["previous_contract_error"] == previous
    assert context["last_error"] is None
    assert context["proposal_contract"]["allowed_predecessor_trial_ids"] == []
    with h.company.db.transaction() as conn:
        attempts = conn.execute("SELECT attempt,error,response FROM research_stage_attempts ORDER BY attempt").fetchall()
    assert attempts[0]["error"] == previous and attempts[0]["response"] is not None
    assert not h.snapshot()["trials"]
