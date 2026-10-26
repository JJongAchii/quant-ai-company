"""Real PostgreSQL mission debate through the turn loop; scripted employees, no scientific execution."""

import json
from uuid import uuid4

import pytest

from quant_company.company import Company
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.research.controller import MissionController

from .test_research_controller import FixtureBackend, active, mission  # noqa: F401
from .test_research_loop import finish_turn, turn_loop


class DebateFixture:
    def __init__(self, harness):
        self.harness = harness

    async def run(self, request):
        context = json.loads(request.prompt.split("MISSION DATA JSON:\n", 1)[1])
        snapshot = context["mission"]
        stage = snapshot["stage"]["stage"]
        if stage == "proposal":
            previous = snapshot["proposals"]
            value = self.harness.proposal(
                hypothesis="Synthetic revised hypothesis" if previous else "Synthetic first hypothesis",
                supersedes_proposal_id=previous[-1]["id"] if previous else None,
            ).model_dump(mode="json")
        elif stage == "challenge":
            value = {"id": str(uuid4()), "proposal_id": snapshot["stage"]["proposal_id"],
                     "reviewer": "financial_strategist", "concern": "Test the consumer boundary independently",
                     "test": "Revise the first fixture before implementation", "source_ids": ["fixture:baseline"]}
        else:
            assert stage == "selection"
            value = {"decision": "revise" if len(snapshot["proposals"]) == 1 else "execute",
                     "rationale": "Decision cites the challenge delivered in this request",
                     "challenge_ids": [snapshot["challenges"][-1]["id"]]}
        return ProviderResponse(request_id=request.request_id, provider="fixture", decision=AgentDecision(
            say="", status="complete", artifacts=[{"title": "Synthetic debate", "content": json.dumps(value)}]))


@pytest.mark.integration
async def test_mission_debate_continues_from_the_same_database_due_time_after_restart(mission):  # noqa: F811
    company = mission.company
    controller = MissionController(company, backend=FixtureBackend(company))
    provider = DebateFixture(mission)
    turns = turn_loop(company, provider)

    async def finish_stage():
        assert controller.tick()["state"] == "running"
        _, identity = active(mission)
        await finish_turn(turns, company, identity)
        assert controller.tick()["state"] == "completed"

    await finish_stage()
    assert controller.tick()["state"] == "running"
    _, deferred = active(mission)
    with company.db.transaction() as conn:
        conn.execute("UPDATE turns SET due_at=now()+interval '2 seconds' WHERE id=%s", (deferred,))
    assert await turns.tick() == {"state": "idle"}  # the wait lives in PostgreSQL, not in the worker

    # A fresh runtime object reads PostgreSQL and runs the deferred turn once it is due.
    company = Company(company.settings, company.roles)
    mission.company = company
    controller = MissionController(company, backend=FixtureBackend(company))
    turns = turn_loop(company, provider)
    await finish_turn(turns, company, deferred)
    assert controller.tick()["state"] == "completed"
    for _ in range(4):
        await finish_stage()
    state = mission.snapshot()
    assert state["stage"]["stage"] == "implementation"
    assert len(state["proposals"]) == 2 and len(state["rejections"]) == 1
    assert state["cumulative_trials"] == 0
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM research_stage_attempts").fetchone()["n"] == 6
        assert conn.execute("SELECT count(*) AS n FROM outbox WHERE text LIKE '[%%'").fetchone()["n"] == 6
