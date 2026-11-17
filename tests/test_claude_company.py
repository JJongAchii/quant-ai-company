"""Real PostgreSQL, subprocess and HTTP boundaries; model outputs and Slack are synthetic."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from quant_company.api import create_app
from quant_company.company import PolicyError, load_roles
from quant_company.contracts import AgentDecision, ProviderFault, ProviderRequest, ProviderSession
from quant_company.execution import TurnExecutor
from quant_company.providers.claude_runner import ClaudeRunner
from quant_company.providers.client import RuntimeClient
from quant_company.providers.codex_runtime import create_app as runtime_app
from quant_company.providers.routing import CLAUDE_MODEL, CompanyProvider
from quant_company.staff_models import PRESERVED, TARGETS, StaffAllocation, allocate

from .conftest import queued_turns
from .test_claude_runtime import fake_claude as fake_claude
from .test_quant_feed import bound_brief, critique, original
from .test_quant_feed import quant as quant
from .test_research_audit_delivery import setup_audit
from .test_research_controller import active
from .test_research_controller import mission as mission


def configure_company(configure, decision=None, **extra):
    configure(model=CLAUDE_MODEL, used_model=CLAUDE_MODEL,
              review={"decision_json": (decision or AgentDecision(status="complete", say="합성 응답")).model_dump_json()},
              **extra)


def client(config):
    runner = ClaudeRunner(config, source_environment={"PATH": "/usr/bin:/bin"})
    return RuntimeClient("http://claude", "private", expected_provider="claude",
                         transport=httpx.ASGITransport(app=runtime_app(runner=runner, token="private")))


async def test_company_decision_preserves_typed_tools_and_cached_http_receipt(fake_claude):
    config, configure, calls = fake_claude
    decision = AgentDecision(status="continue", say="계산 확인", tools=[
        {"name": "calculate", "arguments": {"expression": "2+2"}}])
    configure_company(configure, decision)
    request = ProviderRequest(request_id=str(uuid4()), model=CLAUDE_MODEL, reasoning_effort="max",
                              prompt="Synthetic company task")
    response = await client(config).run(request)
    assert response.provider == "claude" and response.decision == decision
    assert response.usage["billing_mode"] == "subscription"
    assert await client(config).run(request) == response
    assert len(calls()) == 1
    assert calls()[0]["args"][calls()[0]["args"].index("--tools") + 1] == ""


async def test_company_quota_never_spends_existing_claude_api_budget(fake_claude, tmp_path):
    config, configure, calls = fake_claude
    configure_company(configure, error="rate_limit")
    key = tmp_path / "api-key"
    key.write_text("synthetic-api-key")
    requests = []

    def api(request):
        requests.append(request)
        raise AssertionError("Employee work must not use the paid lane")

    runner = ClaudeRunner(replace(config, api_enabled=True, api_cap_usd=190, api_key_file=key),
                          source_environment={"PATH": "/usr/bin:/bin"}, api_transport=httpx.MockTransport(api))
    with pytest.raises(ProviderFault) as fault:
        await runner.run(ProviderRequest(request_id=str(uuid4()), model=CLAUDE_MODEL,
                                        reasoning_effort="high", prompt="Synthetic task"))
    assert fault.value.code == "quota"
    assert len(calls()) == 1 and requests == []


async def test_native_search_requires_matched_tool_completion_and_no_other_tools(fake_claude):
    config, configure, calls = fake_claude
    decision = AgentDecision(status="complete", say="검색 후보", artifacts=[
        {"title": "Search", "content": '{"results":[]}'}])
    configure_company(configure, decision, tools=["StructuredOutput", "WebSearch"], searches=["synthetic query"])
    request = ProviderRequest(request_id="web-" + str(uuid4()), model=CLAUDE_MODEL, reasoning_effort="high",
                              prompt="Find source candidates", web_search=True)
    response = await client(config).run(request)
    assert response.web_searches[0].query == "synthetic query"
    assert response.web_searches[0].action["type"] == "search"
    assert calls()[0]["args"][calls()[0]["args"].index("--tools") + 1] == "WebSearch"
    configure(search_error=True)
    with pytest.raises(ProviderFault):
        await client(config).run(request.model_copy(update={"request_id": "web-" + str(uuid4())}))
    configure(search_error=False, action="Bash")
    with pytest.raises(ProviderFault) as forbidden:
        await client(config).run(request.model_copy(update={"request_id": "web-" + str(uuid4())}))
    assert forbidden.value.code == "uncertain"


async def test_session_restart_preserves_context_and_refuses_branches(fake_claude):
    config, configure, calls = fake_claude
    configure_company(configure)
    first = ProviderRequest(request_id=str(uuid4()), model=CLAUDE_MODEL, reasoning_effort="max",
                            prompt="prior-token-evidence", session=ProviderSession(id="audit-1"))
    await client(config).run(first)
    second = first.model_copy(update={"request_id": str(uuid4()), "prompt": "Continue from prior evidence",
                                      "session": ProviderSession(id="audit-1", previous_request_id=first.request_id)})
    await client(config).run(second)
    assert "prior-token-evidence" in calls()[1]["stdin"]
    assert "Continue from prior evidence" in calls()[1]["stdin"]
    with pytest.raises(ProviderFault) as branch:
        await client(config).run(second.model_copy(update={"request_id": str(uuid4())}))
    assert branch.value.code == "uncertain" and len(calls()) == 2
    assert (await client(config).run(second)).provider == "claude" and len(calls()) == 2


def allocation_office(company):
    settings = company.settings
    settings.model_accounts_enabled = settings.model_assignments_enabled = True
    settings.model_accounts_owner_user = "UHUMAN"
    settings.company_improvements_enabled = True
    company.roles = load_roles(settings)
    bindings = {target: {"model": "gpt-6-astra" if target not in {"data", "engineer", "operations",
                 "maintainer", "quant_scout", "news_screening", "news_search"} else "gpt-6.1-sol",
                 "reasoning_effort": "max" if target == "director" else "high"}
                for target in (*TARGETS, *PRESERVED)}
    with company.db.transaction() as conn:
        conn.execute("UPDATE model_assignment_policy SET revision=2,bindings=%s WHERE id=1", (Jsonb(bindings),))
        conn.execute("INSERT INTO model_assignment_revisions(revision,bindings) VALUES(2,%s)", (Jsonb(bindings),))
    return bindings


def test_preview_apply_are_atomic_idempotent_preserve_exclusions_and_frozen_requests(company, credentials):
    original_bindings = allocation_office(company)
    before = company.ingest(event_key="before", text="Synthetic task", owner="UHUMAN")
    old_id = queued_turns(company, before["project_id"])[0]
    old_request = company.prepare_turn(old_id)["request"]
    old_active = {key: role.active for key, role in company.roles.items()}
    command = StaffAllocation(operation_id="owner-test-1", expected_revision=2)
    preview = allocate(company, command)
    with company.db.transaction() as conn:
        assert conn.execute("SELECT revision FROM model_assignment_policy").fetchone()["revision"] == 2
    applied = allocate(company, command.model_copy(update={"dry_run": False, "plan_digest": preview["plan_digest"]}))
    assert applied["revision"] == 3
    assert {key: role.active for key, role in company.roles.items()} == old_active
    for target in PRESERVED:
        assert applied["after"][target] == original_bindings[target]
    for target in TARGETS:
        assert applied["after"][target]["model"] == CLAUDE_MODEL
        assert applied["after"][target]["reasoning_effort"] == original_bindings[target]["reasoning_effort"]
    assert company.prepare_turn(old_id)["request"] == old_request
    after = company.ingest(event_key="after", text="Synthetic task", owner="UHUMAN")
    assert company.prepare_turn(queued_turns(company, after["project_id"])[0])["request"]["model"] == CLAUDE_MODEL
    assert allocate(company, command.model_copy(update={"dry_run": False,
                    "plan_digest": preview["plan_digest"]}))["cached"]
    with pytest.raises(PolicyError, match="revision"):
        allocate(company, command.model_copy(update={"operation_id": "different"}))
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) n FROM events WHERE kind='staff_claude_allocation'").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) n FROM outbox").fetchone()["n"] == 0
    api = TestClient(create_app(company.settings, company, credentials))
    assert api.post("/v1/model-assignments/claude-staff", json=command.model_dump()).status_code == 401


def test_allocation_rejects_missing_preview_digest_and_owner(company):
    allocation_office(company)
    with pytest.raises(PolicyError, match="digest"):
        allocate(company, StaffAllocation(operation_id="test", expected_revision=2, dry_run=False))
    company.settings.model_accounts_owner_user = "different-owner"
    with pytest.raises(PolicyError, match="owner"):
        allocate(company, StaffAllocation(operation_id="test", expected_revision=2))


def test_concurrent_operator_retries_commit_one_revision_and_one_receipt(company):
    allocation_office(company)
    command = StaffAllocation(operation_id="concurrent-owner", expected_revision=2)
    preview = allocate(company, command)
    apply = command.model_copy(update={"dry_run": False, "plan_digest": preview["plan_digest"]})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: allocate(company, apply), range(2)))
    assert sorted(result["cached"] for result in results) == [False, True]
    assert {result["revision"] for result in results} == {3}
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) n FROM model_assignment_revisions WHERE revision=3").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) n FROM events WHERE kind='staff_claude_allocation'").fetchone()["n"] == 1


async def test_postgres_turn_producer_to_claude_http_consumer(company, fake_claude):
    config, configure, calls = fake_claude
    configure_company(configure)
    allocation_office(company)
    cmd = StaffAllocation(operation_id="e2e", expected_revision=2)
    preview = allocate(company, cmd)
    allocate(company, cmd.model_copy(update={"dry_run": False, "plan_digest": preview["plan_digest"]}))
    company.settings.model_provider = "codex"
    assert company.runtime_context()["model_provider"] == "mixed"
    result = company.ingest(event_key="live-path-fixture", text="Synthetic task", owner="UHUMAN")
    turn_id = queued_turns(company, result["project_id"])[0]

    class NoCodex:
        async def run(self, request):
            raise AssertionError("Claude selection must never dispatch to Codex")

    routed = CompanyProvider(company, NoCodex(), client(config))
    assert (await TurnExecutor(company, routed).execute(turn_id))["state"] == "completed"
    assert (await TurnExecutor(company, routed).execute(turn_id))["state"] != "ready"
    assert len(calls()) == 1
    with company.db.transaction() as conn:
        turn = conn.execute("SELECT response FROM turns WHERE id=%s", (turn_id,)).fetchone()
        assert turn["response"]["provider"] == "claude"
        assert turn["response"]["usage"]["actual_model"] == CLAUDE_MODEL


async def test_quant_producer_and_domain_consumer_accept_claude_without_bypassing_checks(quant, fake_claude):
    config, configure, calls = fake_claude
    company = quant.company
    company.settings.model_provider = "codex"
    company.settings.model_assignments_enabled = True
    with company.db.transaction() as conn:
        conn.execute("UPDATE model_assignment_policy SET bindings=%s WHERE id=1", (Jsonb({
            "quant_scout": {"model": CLAUDE_MODEL, "reasoning_effort": "high"}}),))
    original(quant)
    first = quant.prepare()
    configure(model=CLAUDE_MODEL, used_model=CLAUDE_MODEL, review=bound_brief())
    assert first["request"]["model"] == CLAUDE_MODEL
    result = await client(config).run(ProviderRequest.model_validate(first["request"]))
    with pytest.raises(ValueError, match="wrong_provider"):
        quant.commit(result.model_copy(update={"provider": "codex"}))
    assert quant.commit(result)["state"] == "completed"
    second = quant.prepare()
    configure(review=critique())
    result = await client(config).run(ProviderRequest.model_validate(second["request"]))
    assert quant.commit(result)["state"] == "completed"
    assert len(calls()) == 2
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) n FROM quant_feed_calls WHERE response->>'provider'='claude'").fetchone()["n"] == 2


async def test_restart_cancellation_uses_durable_binding_and_never_falls_back(company):
    class Recorder:
        def __init__(self):
            self.ids = []

        async def cancel(self, request_id):
            self.ids.append(request_id)
            return "cancelled"

        async def run(self, request):
            raise ProviderFault("quota", "Synthetic limit", 60)

    codex, claude = Recorder(), Recorder()
    with company.db.transaction() as conn:
        conn.execute("INSERT INTO model_execution_bindings(request_id,target,selection) VALUES('frozen','data',%s)",
                     (Jsonb({"model": CLAUDE_MODEL, "reasoning_effort": "high"}),))
    routed = CompanyProvider(company, codex, claude)
    assert await routed.cancel("frozen") == "cancelled"
    assert claude.ids == ["frozen"] and codex.ids == []
    with pytest.raises(ProviderFault):
        await routed.run(ProviderRequest(request_id="fault", model=CLAUDE_MODEL, prompt="Fixture"))
    assert codex.ids == []


async def test_batched_audit_verifies_reconstructed_context_against_postgres(mission, fake_claude):
    from quant_company.research.audit_delivery import check_delivery, packet_data

    config, configure, calls = fake_claude
    company = mission.company
    _, row, identity = setup_audit(mission, {"audit-evidence.txt": "Synthetic evidence. " * 3500})
    company.settings.model_assignments_enabled = True
    with company.db.transaction() as conn:
        conn.execute("UPDATE model_assignment_policy SET bindings=%s WHERE id=1", (Jsonb({
            "validator": {"model": CLAUDE_MODEL, "reasoning_effort": "max"}}),))
    while True:
        request = ProviderRequest.model_validate(company.prepare_turn(identity)["request"])
        data = packet_data(request.prompt)
        value = ({"markdown": "Synthetic final only; no research judgment."} if data["phase"] == "final" else
                 {"packet_digest": data["packet_digest"], "notes": "Synthetic source-bound notes."})
        configure_company(configure, AgentDecision(status="complete", say="", artifacts=[
            {"title": "Synthetic audit packet", "content": json.dumps(value)}]))
        result = await client(config).run(request)
        assert result.thread_id is None
        assert result.usage["company_session"]["mode"] == "reconstructed_context"
        company.commit_turn(identity, result)
        if data["phase"] == "final":
            break
        _, identity = active(mission)
    assert len(calls()) >= 3
    with company.db.transaction() as conn:
        stage = conn.execute("SELECT * FROM research_mission_stages WHERE id=%s", (row["id"],)).fetchone()
        turns = conn.execute("SELECT * FROM turns WHERE task_id=%s ORDER BY sequence", (row["task_id"],)).fetchall()
        check_delivery(conn, stage, turns)
        turns[-1]["response"]["usage"]["company_session"]["history_digest"] = "0" * 64
        with pytest.raises(PolicyError, match="claude_context_mismatch"):
            check_delivery(conn, stage, turns)


async def test_video_priority_defers_only_shared_slot_work_and_never_codex(company, monkeypatch):
    from quant_company.newsletter.store import NewsletterStore

    monkeypatch.setattr(NewsletterStore, "video_needs_model", lambda _: True)

    class Recorder:
        def __init__(self):
            self.calls = []

        async def run(self, request):
            self.calls.append(request.request_id)
            return "synthetic-response"

    codex, claude = Recorder(), Recorder()
    routed = CompanyProvider(company, codex, claude)
    # Staff have their own Claude slot since 2026-11-17 (claude_runner.LOCKS 'company'): they no longer wait.
    assert await routed.run(ProviderRequest(request_id="staff", model=CLAUDE_MODEL, prompt="Fixture")) == "synthetic-response"
    with pytest.raises(ProviderFault) as busy:
        await routed.run(ProviderRequest(request_id="review-shared", model=CLAUDE_MODEL, prompt="Fixture"))
    assert busy.value.code == "busy" and claude.calls == ["staff"] and routed.routes == {}
    assert await routed.run(ProviderRequest(request_id="news", model="gpt-6-astra", prompt="Fixture")) == "synthetic-response"
    assert codex.calls == ["news"]
