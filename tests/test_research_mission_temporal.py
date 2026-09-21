"""Real PostgreSQL/Temporal mission debate; scripted employees, no scientific execution."""

import asyncio
import json
from uuid import uuid4

import pytest
from temporalio.worker import Replayer

from quant_company.company import Company
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.execution import TurnExecutor
from quant_company.research.controller import MissionController
from quant_company.runtime import dispatch_once, make_worker
from quant_company.workflow import CompanyTurnWorkflow

from .test_research_controller import FixtureBackend, active, mission  # noqa: F401
from .test_temporal import temporal_environment  # noqa: F401


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
async def test_mission_debate_continues_from_same_temporal_timer_after_restart(mission, temporal_environment):  # noqa: F811
    company = mission.company
    company.settings.temporal_task_queue = "mission-restart-" + uuid4().hex
    client = temporal_environment.client
    controller = MissionController(company, backend=FixtureBackend(company))
    provider = DebateFixture(mission)
    histories = []

    async def finish_stage():
        assert controller.tick()["state"] == "running"
        _, identity = active(mission)
        await dispatch_once(client, company)
        handle = client.get_workflow_handle("company-turn-" + identity)
        assert (await asyncio.wait_for(handle.result(), timeout=20))["state"] == "completed"
        histories.append(await handle.fetch_history())
        assert controller.tick()["state"] == "completed"

    async with make_worker(client, company, TurnExecutor(company, provider)):
        await finish_stage()
        assert controller.tick()["state"] == "running"
        _, deferred = active(mission)
        with company.db.transaction() as conn:
            conn.execute("UPDATE turns SET due_at=now()+interval '3 seconds' WHERE id=%s", (deferred,))
        await dispatch_once(client, company)
        handle = client.get_workflow_handle("company-turn-" + deferred)
        async with asyncio.timeout(10):
            while True:
                history = await handle.fetch_history()
                if any(item.HasField("timer_started_event_attributes") for item in history.events):
                    break
                await asyncio.sleep(0.05)

    # A fresh runtime object reads PostgreSQL; the workflow timer survives the old worker.
    company = Company(company.settings, company.roles)
    mission.company = company
    controller = MissionController(company, backend=FixtureBackend(company))
    async with make_worker(client, company, TurnExecutor(company, provider)):
        with company.db.transaction() as conn:
            conn.execute("UPDATE turns SET workflow_started=false WHERE id=%s", (deferred,))
        await dispatch_once(client, company)
        assert (await asyncio.wait_for(handle.result(), timeout=20))["state"] == "completed"
        histories.append(await handle.fetch_history())
        assert controller.tick()["state"] == "completed"
        for _ in range(4):
            await finish_stage()
        for history in histories:
            await Replayer(workflows=[CompanyTurnWorkflow]).replay_workflow(history)
    state = mission.snapshot()
    assert state["stage"]["stage"] == "implementation"
    assert len(state["proposals"]) == 2 and len(state["rejections"]) == 1
    assert state["cumulative_trials"] == 0
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM research_stage_attempts").fetchone()["n"] == 6
        assert conn.execute("SELECT count(*) AS n FROM outbox WHERE text LIKE '[%%'").fetchone()["n"] == 6
