"""Real PostgreSQL program original reads and selection through the turn loop; scripted employees."""

import json

import pytest

from quant_company.company import Company
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.research.program_controller import ProgramController

from .test_research_loop import finish_turn, oldest_queued, turn_loop
from .test_research_programs import program, ready, task_proposal  # noqa: F401


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
async def test_program_original_reads_survive_worker_restart(program):  # noqa: F811
    company = program.company
    provider = ProgramFixture()
    controller = ProgramController(company)
    turns = turn_loop(company, provider)

    async def finish():
        await finish_turn(turns, company, oldest_queued(company))

    assert controller.tick()["state"] == "running"
    await finish()  # Durable original read before the worker restarts.
    company = Company(company.settings, company.roles)
    controller = ProgramController(company)
    turns = turn_loop(company, provider)
    await finish()
    assert controller.tick()["state"] == "completed"
    for _ in range(2):
        assert controller.tick()["state"] == "running"
        await finish()
        await finish()
        assert controller.tick()["state"] == "completed"
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM research_stage_reads").fetchone()["n"] == 3
        assert conn.execute("SELECT count(*) AS n FROM research_program_tasks WHERE state='accepted'").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM research_missions WHERE state='active'").fetchone()["n"] == 1
        assert not conn.execute("SELECT 1 FROM research_jobs").fetchone()
