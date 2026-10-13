"""Real PostgreSQL and Temporal; simulated employees/owner ingress, no research execution."""

import asyncio
import json
from uuid import uuid4

import pytest
from temporalio.worker import Replayer

from quant_company.company import Company
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.execution import TurnExecutor
from quant_company.research.mission_contracts import MissionSpec
from quant_company.research.policy_contracts import scoped_kwargs
from quant_company.research.program_controller import ProgramController
from quant_company.runtime import dispatch_once, make_worker
from quant_company.workflow import CompanyTurnWorkflow

from .test_research_conditional import conditional  # noqa: F401
from .test_research_programs import program, task_proposal  # noqa: F401
from .test_temporal import temporal_environment  # noqa: F401


class ConditionalProgramFixture:
    async def run(self, request):
        context = json.loads(request.prompt.split("MISSION DATA JSON:\n", 1)[1])
        stage = request.prompt.split("\nSTAGE: ", 1)[1].split("\n", 1)[0]
        paths = list(dict.fromkeys([*context["required_data_reads"],
                                  *(item["file"] for item in context["evidence_sources"])]))
        unread = next((path for path in paths if path not in context["file_progress"]
                       or context["file_progress"][path]["next_offset"] is not None), None)
        if unread:
            progress = context["file_progress"].get(unread)
            decision = AgentDecision(say="", status="continue", tools=[{"name": "research_control", "arguments": {
                "action": "read_stage_file", "path": unread, "offset": progress["next_offset"] if progress else 0}}])
        else:
            spec = MissionSpec.model_validate(context["program"]["spec"]["envelopes"][0]["template"])
            assessment = {**scoped_kwargs(spec), "decision": "conditional_ready", "rationale": "Synthetic contract check",
                "source_ids": ["fixture:baseline"], "point_in_time": False, "coverage": True, "executable_prices": False,
                "original_conditions": False, "evaluation_price_contract_verified": True,
                "packet_digest": context["data_evidence_packets"][0]["packet_digest"]}
            assessment["research_scope"] = spec.research_scope.model_dump(mode="json")
            value = {"program_proposal": task_proposal(mode="novel_hypothesis"), "program_data": assessment,
                     "program_selection": {"decision": "accept", "rationale": "Conditional synthetic task only"}}[stage]
            decision = AgentDecision(say="", status="complete", artifacts=[{
                "title": "Synthetic conditional stage", "content": json.dumps(value)}])
        return ProviderResponse(request_id=request.request_id, provider="fixture", decision=decision)


@pytest.mark.integration
async def test_conditional_assessment_authority_and_scope_survive_temporal_restart_and_replay(
        conditional, temporal_environment):  # noqa: F811
    h = conditional
    company, client = h.company, temporal_environment.client
    company.settings.temporal_task_queue = "conditional-restart-" + uuid4().hex
    provider = ConditionalProgramFixture()
    controller = ProgramController(company)
    histories = []

    async def finish_turn():
        with company.db.transaction() as conn:
            identity = str(conn.execute("SELECT id FROM turns WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()["id"])
        await dispatch_once(client, company)
        handle = client.get_workflow_handle("company-turn-" + identity)
        assert (await asyncio.wait_for(handle.result(), 20))["state"] == "completed"
        histories.append(await handle.fetch_history())

    async with make_worker(client, company, TurnExecutor(company, provider)):
        assert controller.tick()["state"] == "running"
        await finish_turn()
    company = Company(company.settings, company.roles)
    controller = ProgramController(company)
    async with make_worker(client, company, TurnExecutor(company, provider)):
        for stage in ("program_proposal", "program_data", "program_selection"):
            for _ in range(24):
                await finish_turn()
                with company.db.transaction() as conn:
                    row = conn.execute("SELECT state FROM research_mission_stages WHERE program_id=%s AND stage=%s",
                                       (h.program_id, stage)).fetchone()
                if row["state"] == "received":
                    break
            else:
                pytest.fail("Synthetic employee did not finish its bounded reads")
            assert controller.tick()["state"] == "completed"
            if stage != "program_selection":
                assert controller.tick()["state"] == "running"
        for history in histories:
            await Replayer(workflows=[CompanyTurnWorkflow]).replay_workflow(history)
    with company.db.transaction() as conn:
        task = conn.execute("SELECT * FROM research_program_tasks WHERE program_id=%s AND state='accepted'",
                            (h.program_id,)).fetchone()
        mission = conn.execute("SELECT * FROM research_missions WHERE id=%s", (task["mission_id"],)).fetchone()
        assert task["data_assessment"]["point_in_time"] is False
        assert task["data_assessment"]["decision"] == "conditional_ready"
        assert task["data_assessment"]["research_scope"]["data_policy_digest"] == h.public.data_policy_digest
        assert mission["spec"]["data_policy"]["availability_status"] == "unverified_historical"
        assert not conn.execute("SELECT 1 FROM research_jobs").fetchone()
        assert not conn.execute("SELECT 1 FROM research_program_reservations").fetchone()
        assert len(histories) == conn.execute("SELECT count(*) AS n FROM turns WHERE status='completed'").fetchone()["n"]
