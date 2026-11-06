"""Simulated Responses transport + real isolated fake-CLI process; no paid/live calls."""

import asyncio
import json
import os
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from quant_company.contracts import ProviderFault, ProviderRequest, ProviderSession
from quant_company.providers.codex_release import SUPPORTED_CLI_VERSION
from quant_company.providers.codex_runner import CodexRunner, RunnerConfig, atomic_json, request_digest
from quant_company.providers.codex_runtime import create_app, runner_from_environment
from quant_company.providers.openai_credits import CreditGrant, MonthlyApiBudget, cost_ceiling, request_body
from tests.test_codex_runtime import FAKE_CODEX


@pytest.fixture
def fake_subscription(tmp_path):
    binary = tmp_path / "fake-codex"
    binary.write_text(f"#!{sys.executable}\n{FAKE_CODEX}")
    binary.chmod(0o700)
    control = tmp_path / "control.json"

    def configure(**values):
        control.write_text(json.dumps({"version": SUPPORTED_CLI_VERSION, **values}))

    def calls():
        path = tmp_path / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    configure()
    auth = tmp_path / "auth"
    auth.mkdir()
    return RunnerConfig(codex_home=auth, jobs_dir=tmp_path / "jobs", codex_bin=str(binary),
                        timeout_seconds=5), configure, calls


def payload(**changes):
    data = {"id": "resp_test", "model": "gpt-6.1-sol", "status": "completed",
            "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text",
                "text": json.dumps({"decision_json": json.dumps({"say": "검토 완료", "status": "complete"})})}]}],
            "usage": {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 200},
                      "output_tokens": 100, "output_tokens_details": {"reasoning_tokens": 30}}}
    return {**data, **changes}


@pytest.fixture
def credit_runtime(fake_subscription, tmp_path):
    config, configure, cli_calls = fake_subscription
    moment = time.time()
    directory = tmp_path / "credit-config"
    directory.mkdir()
    key = directory / "api_key"
    key.write_text("sk-test-THIS_IS_A_SYNTHETIC_KEY_ONLY")
    grant = directory / "grant.json"
    grant.write_text(json.dumps({"version": 1, "grant_id": "test-cycle", "budget_usd": 5,
        "starts_at": datetime.fromtimestamp(moment-3600, UTC).isoformat(),
        "expires_at": datetime.fromtimestamp(moment+86400, UTC).isoformat(), "free_only_confirmed": True}))
    config = replace(config, api_enabled=True, api_key_file=key, api_grant_file=grant)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload())

    def runner(transport=None, enabled=True):
        return CodexRunner(replace(config, api_enabled=enabled),
            environment={"PATH": os.defpath, "HOME": str(tmp_path)},
            api_transport=transport or httpx.MockTransport(handler))

    def update(**changes):
        grant.write_text(json.dumps({**json.loads(grant.read_text()), **changes}))

    request = ProviderRequest(request_id=str(uuid4()), model="gpt-6.1-sol", reasoning_effort="high",
                              prompt="Synthetic staff decision; no actual external effects.")
    return runner, request, calls, cli_calls, update, config, configure


async def test_api_first_preserves_contract_model_account_and_cached_id(credit_runtime):
    runner, request, calls, cli_calls, _, config, _ = credit_runtime
    result = await runner().run(request, revision=2)
    assert result.decision.say == "검토 완료"
    assert result.usage["billing_mode"] == "api_credit"
    assert result.usage["backend"] == "responses_api"
    assert result.usage["input_tokens"] == 1000 and result.usage["cached_input_tokens"] == 200
    assert not cli_calls() and len(calls) == 1
    body = json.loads(calls[0].content)
    assert calls[0].url == "https://api.openai.com/v1/responses"
    assert body["model"] == request.model and body["reasoning"] == {"effort": "high"}
    assert body["text"]["format"]["strict"] and body["store"] is False
    assert "tools" not in body and "previous_response_id" not in body
    receipt = json.loads((config.jobs_dir / (request.request_id + ".json")).read_text())
    assert receipt["account"] == {"profile": "primary", "revision": 2}
    # API can be disabled after the call: fetching its receipt still does not dispatch CLI inference.
    assert await runner(enabled=False).run(request) == result
    assert len(calls) == 1 and not cli_calls()
    with pytest.raises(ProviderFault, match="different input"):
        await runner().run(request.model_copy(update={"prompt": "Changed request"}))
    assert "SYNTHETIC_KEY" not in json.dumps(receipt)


@pytest.mark.parametrize("condition", ["disabled", "no_key", "expired", "near_expiry", "future", "unconfirmed",
                                       "small_budget", "invalid_grant", "unknown_model", "legacy_effort", "web", "session"])
async def test_ineligible_api_uses_subscription_without_exposing_key(credit_runtime, condition):
    runner, request, calls, cli_calls, update, config, configure = credit_runtime
    enabled = condition != "disabled"
    if condition == "no_key":
        config.api_key_file.unlink()
    elif condition in {"expired", "near_expiry", "future"}:
        now = time.time()
        if condition == "future":
            update(starts_at=datetime.fromtimestamp(now+100, UTC).isoformat())
        else:
            update(expires_at=datetime.fromtimestamp(now-100 if condition == "expired" else now+1, UTC).isoformat())
    elif condition == "unconfirmed":
        update(free_only_confirmed=False)
    elif condition == "small_budget":
        update(budget_usd=.01)
    elif condition == "invalid_grant":
        config.api_grant_file.write_text("[]")
    elif condition == "unknown_model":
        request = request.model_copy(update={"model": "an-unpriced-model"})
    elif condition == "legacy_effort":
        request = request.model_copy(update={"reasoning_effort": None})
    elif condition == "web":
        request = request.model_copy(update={"web_search": True})
        configure(tool_type="web_search")
    elif condition == "session":
        request = request.model_copy(update={"session": ProviderSession(id="existing-audit")})
    response = await runner(enabled=enabled).run(request)
    assert not calls and len(cli_calls()) == 1
    assert response.usage["input_tokens"] == 12
    invocation = json.dumps(cli_calls()[0])
    assert "SYNTHETIC_KEY" not in invocation and "OPENAI_API_KEY" not in invocation
    assert 'forced_login_method="chatgpt"' in cli_calls()[0]["args"]


@pytest.mark.parametrize("status,code", [(429, "credit_balance_exhausted"), (429, "rate_limit_exceeded"),
                                       (429, "project_spend_limit_exceeded"), (401, "invalid_api_key"),
                                       (403, "permission_denied"), (400, "invalid_request_error")])
async def test_explicit_nonexecuting_refusal_falls_back_once_and_pauses_api(credit_runtime, status, code):
    runner, request, calls, cli_calls, _, config, _ = credit_runtime

    def denied(req):
        calls.append(req)
        return httpx.Response(status, json={"error": {"code": code, "message": "SECRET-ERROR"}})

    runtime = runner(httpx.MockTransport(denied))
    result = await runtime.run(request)
    assert result.usage["billing_mode"] == "subscription" and len(calls) == 1 and len(cli_calls()) == 1
    assert await runner().run(request) == result
    assert len(calls) == 1 and len(cli_calls()) == 1
    ledger = next((config.jobs_dir / "openai-api").glob("[0-9a-f]*.json"))
    entry = json.loads(ledger.read_text())["entries"][request.request_id]
    assert entry["settled"] == 0
    assert "SECRET-ERROR" not in ledger.read_text()


@pytest.mark.parametrize("failure", ["timeout", "server", "bad_json", "bad_usage", "incomplete", "wrong_model",
                                    "tool_action", "invalid_decision"])
async def test_possible_execution_never_replays_on_subscription_or_after_disable(credit_runtime, failure):
    runner, request, calls, cli_calls, _, config, _ = credit_runtime

    def failed(req):
        calls.append(req)
        if failure == "timeout":
            raise httpx.ReadTimeout("SECRET timeout", request=req)
        if failure == "server":
            return httpx.Response(500, text="SECRET server")
        if failure == "bad_json":
            return httpx.Response(200, text="SECRET invalid JSON")
        changes = {"bad_usage": {"usage": {"input_tokens": True, "output_tokens": 10}},
                   "incomplete": {"status": "incomplete"}, "wrong_model": {"model": "gpt-6-astra"},
                   "tool_action": {"output": [{"type": "function_call", "name": "unsafe"}]},
                   "invalid_decision": {"output": [{"type": "message", "role": "assistant", "content": [
                       {"type": "output_text", "text": "SECRET rejected model text"}]}]}}
        return httpx.Response(200, json=payload(**changes[failure]))

    with pytest.raises(ProviderFault) as error:
        await runner(httpx.MockTransport(failed)).run(request)
    assert "SECRET" not in str(error.value)
    with pytest.raises(ProviderFault):
        await runner(enabled=False).run(request)
    assert len(calls) == 1 and not cli_calls()
    receipt = json.loads((config.jobs_dir / (request.request_id + ".json")).read_text())
    assert receipt["state"] == "failed" and "SECRET" not in json.dumps(receipt)


async def test_budget_is_shared_across_runtime_instances_and_lanes(credit_runtime):
    runner, request, calls, cli_calls, update, config, _ = credit_runtime
    update(budget_usd=1)
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(req):
        calls.append(req)
        entered.set()
        await release.wait()
        return httpx.Response(200, json=payload())

    task = asyncio.create_task(runner(httpx.MockTransport(delayed)).run(request))
    await asyncio.wait_for(entered.wait(), 2)
    other = request.model_copy(update={"request_id": "news-another-lane"})
    result = await runner().run(other)
    assert result.usage["billing_mode"] == "subscription"
    assert len(calls) == 1 and len(cli_calls()) == 1
    release.set()
    assert (await task).usage["billing_mode"] == "api_credit"
    status = json.loads((config.jobs_dir / "openai-api/status.json").read_text())
    assert 0 < status["accounted_usd"] <= 1


async def test_grant_change_cannot_reset_spend_and_cancellation_cannot_replay(credit_runtime):
    runner, request, calls, cli_calls, update, config, _ = credit_runtime
    await runner().run(request)
    update(budget_usd=100)
    with pytest.raises(ProviderFault, match="ledger"):
        await runner().run(request.model_copy(update={"request_id": str(uuid4())}))
    assert len(calls) == 1 and not cli_calls()


async def test_cancel_during_api_keeps_reservation_and_blocks_new_grant_replay(credit_runtime):
    runner, request, calls, cli_calls, update, config, _ = credit_runtime
    entered = asyncio.Event()

    async def delayed(req):
        calls.append(req)
        entered.set()
        await asyncio.Event().wait()

    runtime = runner(httpx.MockTransport(delayed))
    task = asyncio.create_task(runtime.run(request))
    await asyncio.wait_for(entered.wait(), 2)
    assert await runtime.cancel(request.request_id) == "uncertain"
    with pytest.raises(asyncio.CancelledError):
        await task
    update(grant_id="next-cycle")
    with pytest.raises(ProviderFault):
        await runner().run(request)
    assert len(calls) == 1 and not cli_calls()
    ledger = next((config.jobs_dir / "openai-api").glob("[0-9a-f]*.json"))
    assert json.loads(ledger.read_text())["entries"][request.request_id]["settled"] is None


async def test_restart_between_budget_reservation_and_dispatch_does_not_replay(credit_runtime):
    runner, request, calls, cli_calls, _, config, _ = credit_runtime
    config.jobs_dir.mkdir()
    atomic_json(config.jobs_dir / (request.request_id + ".json"), {"version": 1,
        "request_id": request.request_id, "input_digest": request_digest(request),
        "state": "running", "backend": "responses_api"})
    with pytest.raises(ProviderFault, match="prior process"):
        await runner().run(request)
    assert not calls and not cli_calls()


async def test_private_gateway_preserves_company_response_wire_contract(credit_runtime):
    runner, request, calls, cli_calls, _, _, _ = credit_runtime
    app = create_app(runner=runner(), token="test-private-token")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://private") as client:
        response = await client.post("/v1/turns", json=request.model_dump(mode="json"),
            headers={"Authorization": "Bearer test-private-token", "X-Company-Account": "primary",
                     "X-Company-Account-Revision": "2"})
    assert response.status_code == 200
    assert response.json()["provider"] == "codex"
    assert response.json()["usage"]["backend"] == "responses_api"
    assert len(calls) == 1 and not cli_calls()


def test_factory_defaults_off_and_reads_only_key_file_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.delenv("OPENAI_API_ENABLED", raising=False)
    assert runner_from_environment().api_lane is None
    monkeypatch.setenv("OPENAI_API_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY_FILE", str(tmp_path / "api_key"))
    monkeypatch.setenv("OPENAI_API_GRANT_FILE", str(tmp_path / "grant.json"))
    assert runner_from_environment().api_lane.key_file == tmp_path / "api_key"


def test_pricing_counts_cached_and_reasoning_tokens_once():
    usage = {"input_tokens": 1000, "cached_input_tokens": 200, "output_tokens": 100,
             "reasoning_output_tokens": 30}
    assert cost_ceiling("gpt-6.1-sol", usage) == pytest.approx(.00302)
    usage["input_tokens"] = 300000
    assert cost_ceiling("gpt-6.1-sol", usage) == pytest.approx(1.50054)


def test_grant_forbids_unbounded_or_unconfirmed_billing():
    base = {"version": 1, "grant_id": "cycle", "budget_usd": 200, "free_only_confirmed": True,
            "starts_at": "2026-11-01T00:00:00Z", "expires_at": "2026-12-01T00:00:00Z"}
    assert CreditGrant.model_validate(base).budget_usd == 200
    for changes in ({"budget_usd": 201}, {"budget_usd": float("nan")}, {"free_only_confirmed": False}, {"free_only_confirmed": 1},
                    {"expires_at": "2027-01-01T00:00:00Z"}, {"starts_at": "2026-11-01T00:00:00"}):
        with pytest.raises(ValueError):
            CreditGrant.model_validate({**base, **changes})


def test_body_carries_structured_output_without_codex_api_key_mode():
    request = ProviderRequest(request_id=str(uuid4()), model="gpt-6-astra", prompt="한국어 검사")
    body = request_body(request)
    assert body["prompt_cache_options"] == {"mode": "implicit", "ttl": "30m"}
    assert body["service_tier"] == "default" and "한국어 검사" in body["input"]


@pytest.mark.parametrize("verified", [True, False])
async def test_refusal_crash_window_is_verified_and_never_calls_api_again(credit_runtime, verified):
    runner, request, calls, cli_calls, _, config, _ = credit_runtime
    config.jobs_dir.mkdir()
    atomic_json(config.jobs_dir / (request.request_id + ".json"), {"version": 1,
        "request_id": request.request_id, "input_digest": request_digest(request),
        "state": "api_refused", "backend": "responses_api", "not_executed": verified})
    if verified:
        assert (await runner().run(request)).usage["billing_mode"] == "subscription"
        assert len(cli_calls()) == 1
    else:
        with pytest.raises(ProviderFault):
            await runner().run(request)
        assert not cli_calls()
    assert not calls


async def test_api_enabled_subscription_continuation_preserves_numeric_session_usage(credit_runtime):
    runner, request, calls, cli_calls, _, config, configure = credit_runtime
    first = request.model_copy(update={"session": ProviderSession(id="pinned-audit")})
    await runner().run(first)
    configure(usage={"input_tokens": 30, "output_tokens": 10})
    second = request.model_copy(update={"request_id": str(uuid4()),
        "session": ProviderSession(id="pinned-audit", previous_request_id=first.request_id)})
    result = await runner().run(second)
    assert result.usage["input_tokens"] == 18 and result.usage["output_tokens"] == 3
    assert result.usage["billing_mode"] == "subscription"
    assert not calls and len(cli_calls()) == 2
    receipt = json.loads((config.jobs_dir / (second.request_id + ".json")).read_text())
    assert receipt["session_usage"] == {"input_tokens": 30, "output_tokens": 10}


def monthly_policy(cap=120):
    return {"version": 2, "budget_id": "owner-monthly-api", "monthly_cap_usd": cap,
            "api_spend_authorized": True}


@pytest.mark.parametrize("at,start,end", [
    ("2026-11-06T00:00:00Z", "2026-11-01T00:00:00+09:00", "2026-12-01T00:00:00+09:00"),
    ("2026-12-31T15:00:00Z", "2027-01-01T00:00:00+09:00", "2027-02-01T00:00:00+09:00"),
    ("2028-02-28T15:00:00Z", "2028-02-01T00:00:00+09:00", "2028-03-01T00:00:00+09:00"),
])
def test_monthly_budget_uses_kst_calendar_not_assumed_credit_expiry(at, start, end):
    policy = MonthlyApiBudget.model_validate(monthly_policy())
    window = policy.window(datetime.fromisoformat(at).timestamp())
    assert window.starts_at.isoformat() == start and window.expires_at.isoformat() == end
    assert window.budget_usd == 120 and window.budget_mode == "owner_monthly_cap"
    assert "free_only_confirmed" not in window.model_dump()


def test_monthly_budget_requires_authorization_and_cannot_exceed_owner_cap():
    for change in ({"monthly_cap_usd": 120.01}, {"monthly_cap_usd": 0},
                   {"api_spend_authorized": False}, {"api_spend_authorized": 1}):
        with pytest.raises(ValueError):
            MonthlyApiBudget.model_validate({**monthly_policy(), **change})


async def test_monthly_cap_exhaustion_uses_subscription_then_resumes_new_month(credit_runtime):
    runner, request, calls, cli_calls, _, config, _ = credit_runtime
    config.api_grant_file.write_text(json.dumps(monthly_policy()))
    moment = [datetime.fromisoformat("2026-11-06T00:00:00Z").timestamp()]
    runtime = runner()
    runtime.api_lane.clock = lambda: moment[0]
    window = MonthlyApiBudget.model_validate(monthly_policy()).window(moment[0])
    with runtime.api_lane.locked(window) as ledger:
        ledger["entries"]["previous-staff-work"] = {"reserved": 120, "settled": 120}
    result = await runtime.run(request)
    assert result.usage["billing_mode"] == "subscription" and not calls
    moment[0] = datetime.fromisoformat("2026-11-30T15:00:00Z").timestamp()
    next_request = request.model_copy(update={"request_id": str(uuid4())})
    result = await runtime.run(next_request)
    assert result.usage["api_budget_mode"] == "owner_monthly_cap"
    assert result.usage["api_grant_id"].endswith("2026-12")
    assert len(calls) == 1 and len(cli_calls()) == 1
    status = json.loads((config.jobs_dir / "openai-api/status.json").read_text())
    assert status["budget_mode"] == "owner_monthly_cap" and status["budget_usd"] == 120


async def test_monthly_budget_works_without_free_grant_expiry_or_balance_confirmation(credit_runtime):
    runner, request, calls, cli_calls, _, config, _ = credit_runtime
    config.api_grant_file.write_text(json.dumps(monthly_policy()))
    result = await runner().run(request)
    assert result.usage["api_budget_mode"] == "owner_monthly_cap"
    assert len(calls) == 1 and not cli_calls()


async def test_monthly_api_credit_refusal_pauses_until_next_month_without_purchase(credit_runtime):
    runner, request, calls, cli_calls, _, config, _ = credit_runtime
    config.api_grant_file.write_text(json.dumps(monthly_policy()))
    moment = [datetime.fromisoformat("2026-11-06T00:00:00Z").timestamp()]

    def denied(req):
        calls.append(req)
        return httpx.Response(429, json={"error": {"code": "credit_balance_exhausted"}})

    runtime = runner(httpx.MockTransport(denied))
    runtime.api_lane.clock = lambda: moment[0]
    for _ in range(2):
        result = await runtime.run(request.model_copy(update={"request_id": str(uuid4())}))
        assert result.usage["billing_mode"] == "subscription"
    assert len(calls) == 1
    moment[0] = datetime.fromisoformat("2026-11-30T15:00:00Z").timestamp()
    await runtime.run(request.model_copy(update={"request_id": str(uuid4())}))
    assert len(calls) == 2 and len(cli_calls()) == 3
