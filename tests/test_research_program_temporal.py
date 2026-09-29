"""Real PostgreSQL/Temporal program original reads and selection; scripted employees."""

import asyncio
import json
from uuid import uuid4

import pytest
from temporalio.worker import Replayer

from quant_company.company import Company
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.execution import TurnExecutor
from quant_company.research.program_controller import ProgramController
from quant_company.runtime import dispatch_once, make_worker
from quant_company.workflow import CompanyTurnWorkflow

from .test_research_programs import program, ready, task_proposal  # noqa: F401
from .test_temporal import temporal_environment  # noqa: F401


class ProgramFixture:
    async def run(self, request):
        context = json.loads(request.prompt.split("MISSION DATA JSON:\n", 1)[1])
        stage = request.prompt.split("\nSTAGE: ", 1)[1].split("\n", 1)[0]
        path = context["evidence_sources"][0]["file"]
        if not context["file_progress"]:
            decision = AgentDecision(say="", status="continue", tools=[{"name": "research_control",
                "arguments": {"action": "read_stage_file", "path": path, "offset": 0}}])
        else:
            value = {"program_proposal": task_proposal(), "program_data": ready(),
                     "program_selection": {"decision": "accept", "rationale": "Verified synthetic prerequisites"}}[stage]
            decision = AgentDecision(say="", status="complete",
                                     artifacts=[{"title": "Synthetic program stage", "content": json.dumps(value)}])
        return ProviderResponse(request_id=request.request_id, provider="fixture", decision=decision)


@pytest.mark.integration
async def test_program_original_reads_survive_temporal_restart_and_replay(program, temporal_environment):  # noqa: F811
    company = program.company
    company.settings.temporal_task_queue = "program-restart-" + uuid4().hex
    client = temporal_environment.client
    provider = ProgramFixture()
    controller = ProgramController(company)
    histories = []

    def turn():
        with company.db.transaction() as conn:
            return str(conn.execute("SELECT id FROM turns WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()["id"])

    async def finish_turn():
        identity = turn()
        await dispatch_once(client, company)
        handle = client.get_workflow_handle("company-turn-" + identity)
        assert (await asyncio.wait_for(handle.result(), 20))["state"] == "completed"
        histories.append(await handle.fetch_history())

    async with make_worker(client, company, TurnExecutor(company, provider)):
        assert controller.tick()["state"] == "running"
        await finish_turn()  # Durable original read before the worker restarts.
    company = Company(company.settings, company.roles)
    controller = ProgramController(company)
    async with make_worker(client, company, TurnExecutor(company, provider)):
        await finish_turn()
        assert controller.tick()["state"] == "completed"
        for _ in range(2):
            assert controller.tick()["state"] == "running"
            await finish_turn()
            await finish_turn()
            assert controller.tick()["state"] == "completed"
        for history in histories:
            await Replayer(workflows=[CompanyTurnWorkflow]).replay_workflow(history)
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM research_stage_reads").fetchone()["n"] == 3
        assert conn.execute("SELECT count(*) AS n FROM research_program_tasks WHERE state='accepted'").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM research_missions WHERE state='active'").fetchone()["n"] == 1
        assert not conn.execute("SELECT 1 FROM research_jobs").fetchone()
