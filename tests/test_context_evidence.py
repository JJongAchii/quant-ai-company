"""Real PostgreSQL context assembly; provider/system payloads are synthetic fixtures."""

import json
from datetime import timedelta

import pytest

from quant_company.company import now
from quant_company.contracts import AgentDecision, ProviderResponse

from .conftest import queued_turns


def context(company, project_id):
    turn = queued_turns(company, project_id)[0]
    prompt = company.prepare_turn(turn)["request"]["prompt"]
    return turn, json.loads(prompt.split("TASK DATA JSON:\n")[1])


def read_evidence(company):
    request = company.ingest(event_key="context-evidence", owner="UHUMAN", text="Read registered evidence",
                             channel="CQUANT", thread_ts="context")
    company.put_source(source_id="read-evidence", title="Read evidence", uri="fixture://read-evidence",
                       content="Retain the complete receipt. " * 260, project_id=request["project_id"],
                       available_at=now() - timedelta(days=2), approved=True, synthetic=True)
    turn, _ = context(company, request["project_id"])
    company.commit_turn(turn, ProviderResponse(request_id=turn, provider="fixture", decision=AgentDecision(
        say="Read the evidence", status="continue",
        tools=[{"name": "read_source", "arguments": {"source_id": "read-evidence"}}])))
    return request


@pytest.mark.parametrize("recent", [True, False])
def test_successful_read_remains_citable_after_newer_sources_and_context_pressure(company, monkeypatch, recent):
    monkeypatch.setattr("quant_company.system_state.current_system",
                        lambda *args: {"fixture_facts": "s" * 53000})
    request = read_evidence(company)
    if recent:
        with company.db.transaction() as conn:
            conn.execute("UPDATE sources SET available_at=now() WHERE id='read-evidence'")
    for number in range(55):
        company.put_source(source_id=f"newer-{number:02}", title="Newer fixture", uri="fixture://newer",
                           content="fixture", available_at=now() - timedelta(hours=1),
                           project_id=request["project_id"], approved=True, synthetic=True)
    with company.db.transaction() as conn:
        before = conn.execute("SELECT text FROM messages WHERE task_id=%s AND kind='tool'",
                              (request["task_id"],)).fetchone()["text"]
    turn, data = context(company, request["project_id"])
    assert data["context_truncated"] and len(json.dumps(data, ensure_ascii=False)) <= 68000
    ids = {row["id"] for row in data["approved_sources"]}
    assert "read-evidence" in ids
    assert data["system"]["source_id"] in ids
    assert any(row["text"] == before for row in data["messages"])
    company.commit_turn(turn, ProviderResponse(request_id=turn, provider="fixture", decision=AgentDecision(
        say="Complete with the evidence already read", status="complete",
        artifacts=[{"title": "Fixture report", "content": "Verified fixture", "source_ids": ["read-evidence"]}])))
    state = company.project_state(request["project_id"])
    assert next(t for t in state["tasks"] if str(t["id"]) == request["task_id"])["status"] == "completed"
    assert next(m["text"] for m in state["messages"] if m["kind"] == "tool") == before


@pytest.mark.parametrize("change", ["revoked", "future", "other_project"])
def test_old_successful_receipt_does_not_restore_current_source_authority(company, change):
    request = read_evidence(company)
    with company.db.transaction() as conn:
        if change == "revoked":
            conn.execute("UPDATE sources SET approved=false WHERE id='read-evidence'")
        elif change == "future":
            conn.execute("UPDATE sources SET available_at=now()+interval '1 day' WHERE id='read-evidence'")
        else:
            other = company.ingest(event_key="other", owner="UNAUTHORIZED", text="Other project")
            conn.execute("UPDATE sources SET project_id=%s WHERE id='read-evidence'", (other["project_id"],))
    _, data = context(company, request["project_id"])
    assert "read-evidence" not in {row["id"] for row in data["approved_sources"]}


def test_oversized_system_is_a_readable_reference_not_an_unbounded_prompt(company, monkeypatch):
    monkeypatch.setattr("quant_company.system_state.current_system",
                        lambda *args: {"fixture_facts": "s" * 100000,
                                       "repository": {"state": "stale", "commit": "a" * 40},
                                       "runtime": {"code_commit": "b" * 40}})
    request = company.ingest(event_key="large-system", owner="UHUMAN", text="Current status",
                             channel="CQUANT", thread_ts="large-system")
    _, data = context(company, request["project_id"])
    assert len(json.dumps(data, ensure_ascii=False)) <= 68000
    assert data["system"]["excerpted"]
    assert data["system"]["repository"]["state"] == "stale"
    assert data["system"]["runtime"]["code_commit"] == "b" * 40
    assert data["system"]["source_id"] in {s["id"] for s in data["approved_sources"]}
    with company.db.transaction() as conn:
        stored = conn.execute("SELECT content FROM sources WHERE id=%s", (data["system"]["source_id"],)).fetchone()
    assert len(json.loads(stored["content"])["fixture_facts"]) == 100000
