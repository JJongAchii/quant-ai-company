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
    print(control.get('version', '2.1.275') + ' (Claude Code)')
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
    assert cached.value.code == "uncertain" and len(calls()) == 1
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
    with pytest.raises(ProviderFault) as busy:
        await runner(config).run(request())
    assert busy.value.code == "busy"
    assert await active.cancel("review-001") == "uncertain"
    assert task.cancelled()
    with pytest.raises(ProviderFault) as orphan:
        await runner(config).run(request())
    assert orphan.value.code == "uncertain" and len(calls()) == 1
    assert not list(Path(config.jobs_dir).glob(".review-*"))
