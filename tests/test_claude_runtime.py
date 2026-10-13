"""Real subprocess/HTTP boundaries with a fake CLI, never a subscription call."""

import asyncio
import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from quant_company.contracts import ProviderFault, ProviderRequest
from quant_company.providers.claude_runner import (
    CLI_VERSION,
    FORBIDDEN_AUTH,
    ClaudeConfig,
    ClaudeRunner,
)
from quant_company.providers.client import RuntimeClient
from quant_company.providers.codex_runner import atomic_json, request_digest
from quant_company.providers.codex_runtime import create_app
from quant_company.staff.review_contract import REVIEW_MODEL


def review_fixture():
    dimension = {"assessment": "supported", "explanation": "The supplied calculation supports this conclusion.",
                 "evidence_quotes": ["2+2=4"]}
    return {**{key: dimension.copy() for key in ("grounding", "reasoning", "assumptions", "limitations")},
            "conclusion": "A synthetic fixture only, not evidence of reviewer accuracy."}


@pytest.fixture
def fake_claude(tmp_path):
    control = tmp_path / "control.json"
    log = tmp_path / "calls.jsonl"
    binary = tmp_path / "claude"
    control.write_text(json.dumps({"review": review_fixture()}))
    binary.write_text(f"#!{sys.executable}\n" + r'''
import json, os, sys, time
from pathlib import Path
root = Path(__file__).parent
control = json.loads((root/'control.json').read_text())
if sys.argv[1:] == ['--version']:
    print(control.get('version', '2.1.280') + ' (Claude Code)')
    sys.exit(0)
if sys.argv[1:] == ['auth', 'status']:
    print(json.dumps(control.get('auth', {'loggedIn': True, 'authMethod': 'claude.ai', 'subscriptionType': 'pro'})))
    sys.exit(0)
with (root/'calls.jsonl').open('a') as f:
    f.write(json.dumps({'args': sys.argv[1:], 'env': dict(os.environ), 'stdin': sys.stdin.read()})+'\n')
time.sleep(control.get('delay', 0))
if control.get('crash'):
    print('SECRET-ERROR', file=sys.stderr)
    sys.exit(7)
start = {'type': 'system', 'subtype': 'init', 'model': control.get('model', 'claude-opus-5'),
         'tools': control.get('tools', ['StructuredOutput']), 'mcp_servers': []}
print(json.dumps(start))
if control.get('error'):
    print(json.dumps({'type': 'assistant', 'error': control['error'], 'message': {'usage': {'output_tokens': 0}}}))
    sys.exit(1)
if control.get('action'):
    print(json.dumps({'type': 'assistant', 'message': {'content': [{'type':'tool_use','name':control['action']}]}}))
print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False,
    'modelUsage': {control.get('used_model', 'claude-opus-5'): {'costUSD': 100}},
    'usage': {'input_tokens': 10, 'output_tokens': 20}, 'total_cost_usd': 100,
    'structured_output': control['review']}))
''')
    binary.chmod(0o700)
    config = ClaudeConfig(tmp_path / "auth", tmp_path / "jobs", str(binary),
                          usage_credits_disabled_confirmed=True)

    def configure(**changes):
        control.write_text(json.dumps({**json.loads(control.read_text()), **changes}))

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return config, configure, calls


def request():
    return ProviderRequest(request_id="review-001", model=REVIEW_MODEL, prompt="Case: 2+2=4")


def runner(config):
    return ClaudeRunner(config, source_environment={"PATH": os.environ["PATH"]})


@pytest.mark.parametrize("effort,expected", [(None, "high"), ("max", "max")])
async def test_review_effort_is_explicit_and_cannot_change_during_retry(fake_claude, effort, expected):
    config, _, calls = fake_claude
    frozen = request().model_copy(update={"reasoning_effort": effort})
    result = await runner(config).run(frozen)
    args = calls()[0]["args"]
    assert args[args.index("--effort") + 1] == expected
    assert result.usage["effort"] == expected
    assert result.usage["effort_evidence"] == "requested_cli_argument_not_provider_attested"
    assert await runner(config).run(frozen) == result
    changed = frozen.model_copy(update={"reasoning_effort": "low"})
    with pytest.raises(ProviderFault):
        await runner(config).run(changed)
    assert len(calls()) == 1


async def test_restart_and_private_http_reuse_completed_receipt(fake_claude):
    config, _, calls = fake_claude
    app = create_app(runner=runner(config), token="private")
    client = RuntimeClient("http://runtime", "private", expected_provider="claude",
                           transport=httpx.ASGITransport(app=app))
    result = await client.run(request())
    assert result.provider == "claude" and result.usage["actual_model"] == REVIEW_MODEL
    assert result.usage["cli_version"] == CLI_VERSION
    assert "cost" not in json.dumps(result.usage).lower()
    assert await runner(config).run(request()) == result
    assert len(calls()) == 1
    with pytest.raises(ProviderFault) as mismatch:
        await runner(config).run(request().model_copy(update={"prompt": "altered"}))
    assert mismatch.value.code == "uncertain" and len(calls()) == 1


async def test_credentials_tools_and_configuration_are_isolated(fake_claude):
    config, _, calls = fake_claude
    source = {"PATH": os.environ["PATH"], "DATABASE_URL": "SECRET", "SLACK_BOT_TOKEN": "SECRET",
              "MODEL_RUNTIME_TOKEN": "SECRET", "NODE_OPTIONS": "SECRET", "HTTP_PROXY": "SECRET"}
    await ClaudeRunner(config, source_environment=source).run(request())
    call = calls()[0]
    assert "SECRET" not in json.dumps(call)
    assert not (set(source) - {"PATH"}) & set(call["env"])
    assert call["env"]["CLAUDE_CONFIG_DIR"] == str(config.auth_dir)
    args = call["args"]
    assert "--safe-mode" in args and "--bare" not in args
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--setting-sources") + 1] == ""
    assert json.loads(args[args.index("--mcp-config") + 1]) == {"mcpServers": {}}
    assert json.loads(args[args.index("--settings") + 1]) == {"fastMode": False, "forceLoginMethod": "claudeai"}
    assert "--fallback-model" not in args and request().prompt not in args


@pytest.mark.parametrize("key", FORBIDDEN_AUTH)
async def test_api_or_alternative_provider_cannot_start_inference(fake_claude, key):
    config, _, calls = fake_claude
    with pytest.raises(ProviderFault) as fault:
        await ClaudeRunner(config, source_environment={key: "SECRET"}).run(request())
    assert fault.value.code == "auth" and not calls()
    assert "SECRET" not in str(fault.value)


@pytest.mark.parametrize("change,code", [
    ({"auth": {"loggedIn": True, "authMethod": "api-key", "subscriptionType": "pro"}}, "auth"),
    ({"auth": {"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "free"}}, "auth"),
    ({"version": "unqualified"}, "unavailable"),
])
async def test_preflight_refuses_unqualified_cli_or_login(fake_claude, change, code):
    config, configure, calls = fake_claude
    configure(**change)
    with pytest.raises(ProviderFault) as fault:
        await runner(config).run(request())
    assert fault.value.code == code and not calls()
    assert not (config.jobs_dir / "review-001.json").exists()


async def test_owner_billing_confirmation_is_required(fake_claude):
    config, _, calls = fake_claude
    with pytest.raises(ProviderFault) as fault:
        await runner(replace(config, usage_credits_disabled_confirmed=False)).run(request())
    assert fault.value.code == "auth" and not calls()


@pytest.mark.parametrize("change,code", [
    ({"model": "claude-fable-5"}, "uncertain"), ({"used_model": "claude-sonnet-5"}, "uncertain"),
    ({"tools": ["Bash"]}, "uncertain"), ({"action": "WebSearch"}, "uncertain"),
    ({"review": {"conclusion": "incomplete"}}, "invalid_output"), ({"crash": True}, "uncertain"),
])
async def test_bad_results_never_repeat_inference(fake_claude, change, code):
    config, configure, calls = fake_claude
    configure(**change)
    with pytest.raises(ProviderFault) as fault:
        await runner(config).run(request())
    assert fault.value.code == code
    with pytest.raises(ProviderFault) as cached:
        await runner(config).run(request())
    # A definitive failure is reported again with its own code; anything ambiguous stays uncertain. No second run.
    assert cached.value.code == (code if code in {"invalid_output", "timeout"} else "uncertain") and len(calls()) == 1
    assert "SECRET" not in (config.jobs_dir / "review-001.json").read_text()


async def test_quota_waits_and_retries_only_the_same_bound_input(fake_claude):
    config, configure, calls = fake_claude
    configure(error="rate_limit")
    for _ in range(2):
        with pytest.raises(ProviderFault) as fault:
            await runner(config).run(request())
        assert fault.value.code == "quota"
    assert len(calls()) == 1
    path = config.jobs_dir / "review-001.json"
    receipt = json.loads(path.read_text())
    atomic_json(path, {**receipt, "retry_at": time.time() - 1})
    configure(error=None)
    assert (await runner(config).run(request())).provider == "claude"
    assert len(calls()) == 2


async def test_failed_cli_preserves_execution_cause_without_output_leak_or_replay(fake_claude):
    config, configure, calls = fake_claude
    configure(crash=True)
    with pytest.raises(ProviderFault):
        await runner(config).run(request())
    path = config.jobs_dir / "review-001.json"
    receipt = json.loads(path.read_text())
    evidence = receipt.get("execution_evidence", {})
    assert evidence.get("returncode") == 7, "The original CLI exit must be retained to diagnose repeated uncertain outcomes"
    assert evidence["stderr_bytes"] > 0 and len(evidence["stderr_sha256"]) == 64
    assert receipt["fault_detail"] == "Claude did not confirm a complete review; reconcile its original ID."
    assert "SECRET" not in path.read_text() and path.stat().st_mode & 0o777 == 0o600
    before = path.read_bytes()
    with pytest.raises(ProviderFault):
        await runner(config).run(request())
    assert len(calls()) == 1 and path.read_bytes() == before


async def test_orphan_receipt_and_early_cancellation_prevent_execution(fake_claude):
    config, _, calls = fake_claude
    config.jobs_dir.mkdir()
    atomic_json(config.jobs_dir / "review-001.json", {"provider": "claude", "request_id": "review-001",
        "input_digest": request_digest(request()), "state": "running"})
    with pytest.raises(ProviderFault) as fault:
        await runner(config).run(request())
    assert fault.value.code == "uncertain" and not calls()
    assert await runner(config).cancel("early") == "cancelled"
    with pytest.raises(ProviderFault):
        await runner(config).run(request().model_copy(update={"request_id": "early"}))
    with pytest.raises(ProviderFault):
        await runner(config).cancel("../escape")
    assert not calls()


async def test_single_flight_and_cancellation_preserve_ambiguous_receipt(fake_claude):
    config, configure, calls = fake_claude
    configure(delay=10)
    active = runner(config)
    task = asyncio.create_task(active.run(request()))
    for _ in range(300):
        if calls():
            break
        await asyncio.sleep(0.01)
    assert calls()
    with pytest.raises(ProviderFault) as pending:
        await runner(config).run(request())
    assert pending.value.code == "pending"  # the same request is still running: ask again by its ID
    with pytest.raises(ProviderFault) as busy:
        await runner(config).run(request().model_copy(update={"request_id": "review-002"}))
    assert busy.value.code == "busy"
    assert await active.cancel("review-001") == "uncertain"
    assert task.cancelled()
    with pytest.raises(ProviderFault) as orphan:
        await runner(config).run(request())
    assert orphan.value.code == "uncertain" and len(calls()) == 1
    assert not list(Path(config.jobs_dir).glob(".review-*"))


async def test_video_contracts_get_a_longer_process_limit_than_reviews(fake_claude):
    config, configure, _ = fake_claude
    config = replace(config, timeout_seconds=1, video_timeout_seconds=10)
    configure(delay=2)
    with pytest.raises(ProviderFault) as fault:
        await runner(config).run(request())
    assert fault.value.code == "timeout"
    configure(review={"facts": True, "numbers": True, "conditions": True, "coverage": True, "readability": True,
                      "concerns": []})
    video = ProviderRequest(request_id="video-001-review", model=REVIEW_MODEL, reasoning_effort="high",
                            prompt="Plan: fixture", output_contract="video_review_v1")
    result = await runner(config).run(video)
    assert result.decision.artifacts[0].title == "Video adaptation review"
    with pytest.raises(ValueError):
        replace(config, video_timeout_seconds=10801)


async def test_rejected_video_output_records_rule_not_text(fake_claude):
    config, configure, calls = fake_claude
    configure(review={"facts": True, "numbers": "SECRET-TEXT", "conditions": True, "coverage": True,
                      "readability": True, "concerns": []})
    video = ProviderRequest(request_id="video-002-review", model=REVIEW_MODEL, reasoning_effort="high",
                            prompt="Plan: fixture", output_contract="video_review_v1")
    with pytest.raises(ProviderFault) as fault:
        await runner(config).run(video)
    assert fault.value.code == "invalid_output"
    receipt = json.loads((config.jobs_dir / "video-002-review.json").read_text())
    assert receipt["state"] == "failed" and receipt["execution_evidence"]["returncode"] == 0
    assert "numbers" in receipt["fault_detail"] and "SECRET-TEXT" not in receipt["fault_detail"]
    with pytest.raises(ProviderFault):
        await runner(config).run(video)
    assert len(calls()) == 1


async def test_video_contracts_may_use_opus_5_5_but_reviews_may_not(fake_claude):
    config, configure, calls = fake_claude
    configure(model="claude-opus-5-5", used_model="claude-opus-5-5",
              review={"facts": True, "numbers": True, "conditions": True, "coverage": True, "readability": True,
                      "concerns": []})
    video = ProviderRequest(request_id="video-003-review", model="claude-opus-5-5", reasoning_effort="max",
                            prompt="Plan: fixture", output_contract="video_review_v1")
    result = await runner(config).run(video)
    args = calls()[0]["args"]
    assert args[args.index("--model") + 1] == "claude-opus-5-5" and args[args.index("--effort") + 1] == "max"
    assert result.usage["actual_model"] == "claude-opus-5-5"
    with pytest.raises(ProviderFault) as fault:
        await runner(config).run(request().model_copy(update={"model": "claude-opus-5-5"}))
    assert fault.value.code == "invalid_output" and len(calls()) == 1


async def test_claude_client_reports_a_slow_turn_as_pending_and_keeps_contract_detail():
    def slow(request):
        raise httpx.ReadTimeout("still running", request=request)

    video = ProviderRequest(request_id="video-004-plan", model="claude-opus-5-5", prompt="p", output_contract="video_plan_v1")
    client = RuntimeClient("http://claude-runtime:8080", "token", transport=httpx.MockTransport(slow), expected_provider="claude")
    with pytest.raises(ProviderFault) as pending:
        await client.run(video)
    assert pending.value.code == "pending"
    codex = RuntimeClient("http://codex-runtime:8080", "token", transport=httpx.MockTransport(slow))
    with pytest.raises(ProviderFault) as lost:
        await codex.run(request())
    assert lost.value.code == "uncertain"

    def rejected(request):
        return httpx.Response(422, json={"code": "invalid_output", "retry_after_seconds": 0,
                                         "message": "Video output failed its contract: upload.hashtags: Value error"})
    client = RuntimeClient("http://claude-runtime:8080", "token", transport=httpx.MockTransport(rejected),
                           expected_provider="claude")
    with pytest.raises(ProviderFault) as fault:
        await client.run(video)
    assert fault.value.code == "invalid_output" and "upload.hashtags" in fault.value.message


async def test_video_turns_may_stream_more_than_a_review(fake_claude):
    from quant_company.providers import claude_runner

    config, configure, calls = fake_claude
    configure(review={"facts": True, "numbers": True, "conditions": True, "coverage": True, "readability": True,
                      "concerns": ["x" * 200] * 12})
    video = ProviderRequest(request_id="video-005-review", model=REVIEW_MODEL, reasoning_effort="max",
                            prompt="Plan: fixture", output_contract="video_review_v1")
    original = claude_runner.MAX_OUTPUT
    claude_runner.MAX_OUTPUT = 512  # far below this stream: only staff reviews keep the small cap
    try:
        result = await runner(config).run(video)
        assert result.decision.artifacts[0].title == "Video adaptation review"
        with pytest.raises(ProviderFault):
            await runner(config).run(request())
    finally:
        claude_runner.MAX_OUTPUT = original
