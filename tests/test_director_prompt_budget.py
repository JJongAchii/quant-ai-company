import json

import pytest
from pydantic import ValidationError

from quant_company.company import now
from quant_company.contracts import ProviderRequest
from quant_company.execution import TurnExecutor

from .conftest import queued_turns


@pytest.mark.parametrize("kind", ["work", "answer"])
def test_busy_director_counts_runtime_in_complete_prompt_budget(company, monkeypatch, kind):
    request = company.ingest(event_key="busy-director", text="Summarize pinned-evidence", owner="user")
    turn_id = queued_turns(company, request["project_id"])[0]
    company.put_source(source_id="pinned-evidence", title="Approved fixture", uri="fixture://evidence",
                       content="Full retained source", available_at=now(), approved=True, synthetic=True)
    original_runtime = company.runtime_context
    runtime = original_runtime()
    runtime["fixture_large_runtime_guidance"] = "Recorded configuration guidance. " * 1300
    monkeypatch.setattr(company, "runtime_context", lambda conn=None: runtime)
    with company.db.transaction() as conn:
        conn.execute("UPDATE tasks SET kind=%s WHERE id=%s", (kind, request["task_id"]))
    for index in range(8):
        with company.db.transaction() as conn:
            project = company._project(conn, request["project_id"])
            company._message(conn, project, request["task_id"], "director", "say",
                             f"discussion-{index}: " + "Historical discussion. " * 900)
    with company.db.transaction() as conn:
        messages_before = conn.execute("SELECT id,text FROM messages WHERE project_id=%s ORDER BY id",
                                       (request["project_id"],)).fetchall()
    try:
        prepared = company.prepare_turn(turn_id)
    except ValidationError:
        prepared = {"state": "input_too_large"}
    assert prepared["state"] == "ready"
    frozen = ProviderRequest.model_validate(prepared["request"])
    assert len(frozen.prompt) <= 90000
    assert frozen.request_id == turn_id
    context = json.loads(frozen.prompt.split("TASK DATA JSON:\n", 1)[1])
    assert context["context_truncated"]
    assert context["task"]["instruction"] == "Summarize pinned-evidence"
    assert "pinned-evidence" in {source["id"] for source in context["approved_sources"]}
    with company.db.transaction() as conn:
        assert conn.execute("SELECT id,text FROM messages WHERE project_id=%s ORDER BY id",
                            (request["project_id"],)).fetchall() == messages_before
        assert conn.execute("SELECT content FROM sources WHERE id='pinned-evidence'").fetchone()["content"] == "Full retained source"
    monkeypatch.setattr(company, "runtime_context", original_runtime)
    assert company.prepare_turn(turn_id)["request"] == prepared["request"]


class UncalledProvider:
    async def run(self, request):
        raise AssertionError("Input rejection must precede a model call")


@pytest.mark.asyncio
async def test_invalid_preparation_is_terminal_without_private_input_or_model_call(company, monkeypatch):
    request = company.ingest(event_key="bad-input", text="Summarize", owner="user")
    turn_id = queued_turns(company, request["project_id"])[0]

    def invalid(turn_id):
        return ProviderRequest(request_id=turn_id, model="fixture", prompt="PRIVATE-INPUT-CANARY" * 5000)

    monkeypatch.setattr(company, "prepare_turn", invalid)
    executor = TurnExecutor(company, UncalledProvider())
    try:
        result = await executor.execute(turn_id)
    except ValidationError:
        result = {"state": "infrastructure_retry"}
    assert result == {"state": "blocked", "reason": "request_preparation_rejected"}
    with company.db.transaction() as conn:
        turn = conn.execute("SELECT status,error,request,response,attempts FROM turns WHERE id=%s", (turn_id,)).fetchone()
        assert turn["status"] == "blocked"
        assert turn["attempts"] == 0
        assert turn["request"] is None and turn["response"] is None
        assert "PRIVATE-INPUT-CANARY" not in turn["error"]
        assert conn.execute("SELECT COALESCE(sum(reserved),0) AS reserved FROM daily_usage").fetchone()["reserved"] == 0


@pytest.mark.asyncio
async def test_irreducible_runtime_blocks_without_repeating_preparation(company, monkeypatch):
    request = company.ingest(event_key="impossible-runtime", text="Summarize", owner="user")
    turn_id = queued_turns(company, request["project_id"])[0]
    monkeypatch.setattr(company, "runtime_context", lambda conn=None: {"configuration": "x" * 90000})
    result = await TurnExecutor(company, UncalledProvider()).execute(turn_id)
    assert result["state"] == "blocked"
    assert result["reason"] == "request_preparation_rejected"
    assert company.prepare_turn(turn_id) == {"state": "done", "status": "blocked"}
    with company.db.transaction() as conn:
        assert conn.execute("SELECT COALESCE(sum(reserved),0) AS reserved FROM daily_usage").fetchone()["reserved"] == 0
