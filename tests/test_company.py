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


def test_child_can_report_to_requester_without_authority_to_delegate_upwards(company):
    company.roles["data"] = company.roles["data"].model_copy(update={"can_delegate_to": []})
    req = company.ingest(event_key="report-up", text="Verify evidence", owner="user")
    parent = queued_turns(company, req["project_id"])[0]
    company.prepare_turn(parent)
    company.commit_turn(parent, ProviderResponse(request_id=parent, decision=AgentDecision(
        say="Request evidence", status="wait", delegations=[{"agent": "data", "instruction": "Read source"}])))
    child = queued_turns(company, req["project_id"])[0]
    prepared = company.prepare_turn(child)
    assert json.loads(prepared["request"]["prompt"].split("TASK DATA JSON:\n")[1])["task"]["requester"] == "director"
    with pytest.raises(PolicyError, match="Unauthorized peer"):
        company.commit_turn(child, ProviderResponse(request_id=child, decision=AgentDecision(
            say="Delegate upwards", status="wait", delegations=[{"agent": "director", "instruction": "Do my job"}])))
    company.commit_turn(child, completed(child, messages=[{"agent": "director", "text": "Evidence read"}]))
    state = company.project_state(req["project_id"])
    assert any(message["author"] == "data" and message["recipient"] == "director" for message in state["messages"])


def test_status_remains_available_when_project_budget_is_exhausted(company):
    company.settings.company_max_project_tasks = 1
    req = company.ingest(event_key="full", text="Work", owner="user")
    for index in range(3):
        company.ingest(event_key=f"status-{index}", text="상태", owner="user",
                        project_id=req["project_id"], status_only=True)
    state = company.project_state(req["project_id"])
    assert len(state["turns"]) == 1
    assert sum(task["turn_count"] == 0 and task["status"] == "completed" for task in state["tasks"]) == 3
    with pytest.raises(PolicyError, match="task limit"):
        company.ingest(event_key="extra-work", text="More work", owner="user", project_id=req["project_id"])


def test_blocked_retry_preserves_operator_reconciliation_and_prior_receipt(company):
    req = company.ingest(event_key="ambiguous", text="Work", owner="user")
    old = queued_turns(company, req["project_id"])[0]
    company.prepare_turn(old)
    company.block_turn(old, "uncertain")
    with pytest.raises(PolicyError, match="reconciliation"):
        company.retry_task(req["task_id"])
    retried = company.retry_task(req["task_id"], reconciliation_note="Reviewed old runtime receipt; no accepted output")
    assert retried["turn_id"] != old
    state = company.project_state(req["project_id"])
    assert next(turn for turn in state["turns"] if turn["id"] == old)["status"] == "blocked"
    assert any(event["detail"].get("reconciliation_note") for event in state["events"])


async def test_loop_budget_blocks_child_and_resumes_parent_with_failure(company):
    company.settings.company_max_task_turns = 2
    req = company.ingest(event_key="limited-child", text="Research", owner="user")
    parent_turn = queued_turns(company, req["project_id"])[0]
    company.prepare_turn(parent_turn)
    company.commit_turn(parent_turn, ProviderResponse(request_id=parent_turn, decision=AgentDecision(
        say="Request data", status="wait", delegations=[{"agent": "data", "instruction": "Check data"}])))
    for _ in range(2):
        child_turn = queued_turns(company, req["project_id"])[0]
        company.prepare_turn(child_turn)
        company.commit_turn(child_turn, ProviderResponse(request_id=child_turn,
                            decision=AgentDecision(say="Still thinking", status="continue")))
    state = company.project_state(req["project_id"])
    child = next(task for task in state["tasks"] if task["agent"] == "data")
    parent = next(task for task in state["tasks"] if task["agent"] == "director")
    assert child["status"] == "blocked"
    assert parent["status"] == "pending"
    parent_context = company.prepare_turn(queued_turns(company, req["project_id"])[0])["request"]["prompt"]
    assert '"error": "task_turn_limit"' in parent_context


def test_only_human_review_can_share_memory_across_own_projects(company):
    req = company.ingest(event_key="original-memory", text="First", owner="user")
    company.put_source(source_id="public", title="Public fixture", uri="fixture://public", content="test",
                        available_at=now(), approved=True, synthetic=True)
    turn = queued_turns(company, req["project_id"])[0]
    company.prepare_turn(turn)
    company.commit_turn(turn, completed(turn, memories=[{"text": "Reviewed lesson", "source_ids": ["public"]}]))
    memory_id = company.project_state(req["project_id"])["memories"][0]["id"]
    company.review_memory(memory_id, True, "human", share=True)
    for owner, expected in [("user", True), ("different-owner", False)]:
        new = company.ingest(event_key="memory-" + owner, text="Next", owner=owner)
        prompt = company.prepare_turn(queued_turns(company, new["project_id"])[0])["request"]["prompt"]
        assert ("Reviewed lesson" in prompt) == expected


def test_human_work_precedes_background_and_busy_context_is_bounded(company):
    background = company.ingest(event_key="background", text="Background", owner="user")
    background_turn = queued_turns(company, background["project_id"])[0]
    with company.db.transaction() as conn:
        conn.execute("UPDATE tasks SET priority=100 WHERE id=%s", (background["task_id"],))
    human = company.ingest(event_key="human", text="New human request", owner="user")
    assert company.prepare_turn(background_turn)["reason"] == "higher_priority_request"
    for index in range(20):
        company.ingest(event_key=f"long-{index}", text="Source discussion " + "x" * 9000, owner="user",
                        project_id=human["project_id"])
    human_turn = queued_turns(company, human["project_id"])[0]
    prompt = company.prepare_turn(human_turn)["request"]["prompt"]
    assert len(prompt) < 90000
    assert json.loads(prompt.split("TASK DATA JSON:\n")[1])["context_truncated"]
