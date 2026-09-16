import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from quant_company.company import Company, PolicyError, now
from quant_company.contracts import AgentDecision, ProviderFault, ProviderResponse
from quant_company.execution import FixtureProvider, TurnExecutor
from quant_company.tools import calculate

from .conftest import queued_turns


def completed(turn_id, text="Verified fixture completion", **kwargs):
    return ProviderResponse(request_id=turn_id, decision=AgentDecision(say=text, status="complete", **kwargs),
                            provider="fixture")


def test_numeric_tool_rejects_code_and_resource_exhaustion():
    assert calculate("100/(1+0.05)**2")["result"].startswith("90.7029")
    for value in ["__import__('os').system('id')", "2**99999999", "float('inf')", "[1]*999999", "1/0"]:
        with pytest.raises((ValueError, SyntaxError)):
            calculate(value)


def test_concurrent_ingress_is_one_task_and_reused_id_cannot_change(company):
    def submit(_):
        return company.ingest(event_key="same", text="One mandate", owner="user")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(16)))
    assert len({row["task_id"] for row in results}) == 1
    assert sum(not row["duplicate"] for row in results) == 1
    state = company.project_state(results[0]["project_id"])
    assert len(state["tasks"]) == len(state["turns"]) == 1
    with pytest.raises(PolicyError, match="different content"):
        company.ingest(event_key="same", text="Altered mandate", owner="user")


async def test_four_employee_direct_collaboration_and_durable_context(company):
    request = company.ingest(event_key="collaboration", text="Check fixture", owner="user")
    executor = TurnExecutor(company, FixtureProvider())
    for _ in range(15):
        turns = queued_turns(company, request["project_id"])
        if not turns:
            break
        for turn in turns:
            await executor.execute(turn)
        # All company objects can be recreated between turns without losing colleagues' requests.
        executor.company = Company(company.settings, company.roles)
    state = company.project_state(request["project_id"])
    assert len(state["tasks"]) == 4
    assert {task["status"] for task in state["tasks"]} == {"completed"}
    assert len(state["artifacts"]) == 4
    assert any(m["author"] == "researcher_kr" and m["recipient"] == "data" and m["kind"] == "delegation"
               for m in state["messages"])
    assert any('"result": "105.00"' in m["text"] for m in state["messages"] if m["kind"] == "tool")


def test_revision_fences_old_output_and_committed_output_is_idempotent(company):
    req = company.ingest(event_key="original", text="Old instruction", owner="user",
                          channel="CQUANT", thread_ts="1.0")
    old_turn = queued_turns(company, req["project_id"])[0]
    assert company.prepare_turn(old_turn)["state"] == "ready"
    new_req = company.ingest(event_key="revision", text="New instruction", owner="user",
                              project_id=req["project_id"], revise=True)
    result = company.commit_turn(old_turn, completed(old_turn, artifacts=[{"title": "obsolete", "content": "old"}]))
    assert result["state"] == "stale"
    state = company.project_state(req["project_id"])
    assert not state["artifacts"]
    new_turn = queued_turns(company, new_req["project_id"])[0]
    company.prepare_turn(new_turn)
    response = completed(new_turn)
    company.commit_turn(new_turn, response)
    assert company.commit_turn(new_turn, response)["duplicate"]
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 1
    with pytest.raises(PolicyError, match="cannot be replaced"):
        company.commit_turn(new_turn, completed(new_turn, "different"))


async def test_live_revision_explicitly_cancels_known_model_request(company):
    started = asyncio.Event()

    class SlowProvider:
        cancelled = None

        async def run(self, request):
            started.set()
            await asyncio.sleep(30)
            return completed(request.request_id)

        async def cancel(self, request_id):
            self.cancelled = request_id

    req = company.ingest(event_key="slow", text="Work", owner="user")
    turn_id = queued_turns(company, req["project_id"])[0]
    provider = SlowProvider()
    task = asyncio.create_task(TurnExecutor(company, provider).execute(turn_id))
    await started.wait()
    company.ingest(event_key="steer", text="Changed", owner="user", project_id=req["project_id"], revise=True)
    assert (await task)["state"] == "stale"
    assert provider.cancelled == turn_id


async def test_quota_pause_survives_company_restart_without_paid_fallback(company):
    class Quota:
        async def run(self, request):
            raise ProviderFault("quota", "Quota exhausted", 900)

    req = company.ingest(event_key="quota", text="Work", owner="user")
    turn = queued_turns(company, req["project_id"])[0]
    result = await TurnExecutor(company, Quota()).execute(turn)
    assert result == {"state": "defer", "seconds": 900, "reason": "quota"}
    restarted = Company(company.settings, company.roles)
    newer = restarted.ingest(event_key="after-quota", text="Still accept requests", owner="user")
    assert restarted.prepare_turn(queued_turns(restarted, newer["project_id"])[0])["state"] == "defer"


def test_daily_budget_is_atomic_across_projects(company):
    company.settings.company_max_daily_turns = 1
    turns = []
    for number in range(8):
        req = company.ingest(event_key=f"budget-{number}", text="Work", owner="user")
        turns += queued_turns(company, req["project_id"])
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(company.prepare_turn, turns))
    assert sum(row["state"] == "ready" for row in results) == 1
    with company.db.transaction() as conn:
        assert conn.execute("SELECT reserved FROM daily_usage").fetchone()["reserved"] == 1


def test_unavailable_sources_and_unapproved_memories_never_enter_context(company):
    req = company.ingest(event_key="knowledge", text="Review sources", owner="user")
    company.put_source(source_id="future", title="Future", uri="fixture://future", content="not yet public",
                        available_at=now() + timedelta(days=1), approved=True)
    company.put_source(source_id="known", title="Known fixture", uri="fixture://known", content="fixture",
                        available_at=now() - timedelta(days=1), approved=True, synthetic=True)
    turn = queued_turns(company, req["project_id"])[0]
    prepared = company.prepare_turn(turn)
    data = json.loads(prepared["request"]["prompt"].split("TASK DATA JSON:\n")[1])
    assert {row["id"] for row in data["approved_sources"]} == {"known"}
    with pytest.raises(PolicyError, match="Unavailable"):
        company.commit_turn(turn, completed(turn, artifacts=[{"title": "No", "content": "no", "source_ids": ["future"]}]))
    company.commit_turn(turn, completed(turn, memories=[{"text": "Pending human review", "source_ids": ["known"]}]))
    state = company.project_state(req["project_id"])
    assert state["memories"][0]["status"] == "proposed"
    follow = company.ingest(event_key="follow", text="Remember?", owner="user", project_id=req["project_id"])
    next_turn = queued_turns(company, follow["project_id"])[0]
    context = company.prepare_turn(next_turn)["request"]["prompt"]
    assert json.loads(context.split("TASK DATA JSON:\n")[1])["verified_memories"] == []
    company.review_memory(state["memories"][0]["id"], True, "human")


def test_permissions_and_task_budget_rollback_entire_proposal(company):
    req = company.ingest(event_key="atomic-proposal", text="Work", owner="user")
    turn = queued_turns(company, req["project_id"])[0]
    company.prepare_turn(turn)
    company.settings.company_max_project_tasks = 2
    response = ProviderResponse(request_id=turn, decision=AgentDecision(
        say="This message must roll back too", status="wait", delegations=[
            {"agent": "data", "instruction": "first"}, {"agent": "researcher_kr", "instruction": "second"}]))
    with pytest.raises(PolicyError, match="task limit"):
        company.commit_turn(turn, response)
    state = company.project_state(req["project_id"])
    assert len(state["tasks"]) == 1
    assert len(state["messages"]) == 1


def test_database_migration_is_repeatable(company):
    company.db.migrate()
    assert company.db.health()
