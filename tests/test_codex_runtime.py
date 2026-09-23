"""The subprocess is real; only the executable's model/network behavior is fake."""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx
import pytest

from quant_company.contracts import ProviderFault, ProviderRequest
from quant_company.providers.client import RuntimeClient
from quant_company.providers.codex_runner import (
    CodexRunner,
    RunnerConfig,
    atomic_json,
    request_digest,
)
from quant_company.providers.codex_runtime import create_app, runner_from_environment

FAKE_CODEX = r'''
import json, os, signal, subprocess, sys, time
from pathlib import Path
base = Path(__file__).parent
control = json.loads((base / 'control.json').read_text())
if '--version' in sys.argv:
    print('codex-cli ' + control.get('version', '0.154.0'))
    sys.exit(0)
if 'login' in sys.argv:
    print(control.get('login', 'Logged in using ChatGPT'), file=sys.stderr)
    sys.exit(control.get('login_exit', 0))
schema = Path(sys.argv[sys.argv.index('--output-schema') + 1])
if not schema.exists():
    assert sys.stdin.read() == ''
    print(control.get('config_error', 'No prompt provided via stdin.'), file=sys.stderr)
    sys.exit(1)
with (base / 'calls.jsonl').open('a') as stream:
    stream.write(json.dumps({'args': sys.argv[1:], 'environment': dict(os.environ),
                            'stdin': sys.stdin.read()}) + '\n')
mode = control.get('mode', 'success')
if mode == 'hang':
    child = subprocess.Popen([sys.executable, '-c',
        'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)'])
    (base / 'pids.json').write_text(json.dumps([os.getpid(), child.pid]))
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(30)
if mode == 'delay':
    time.sleep(control.get('delay_seconds', 0.3))
if mode == 'quota':
    print(json.dumps({'type': 'turn.failed', 'error': {'message': 'usage limit reached SECRET-ERROR'}}))
    sys.exit(1)
if mode == 'auth':
    print(json.dumps({'type': 'turn.failed', 'error': {'message': 'authentication token_expired SECRET'}}))
    sys.exit(1)
if mode == 'crash':
    print('SECRET-STDERR', file=sys.stderr)
    sys.exit(7)
if mode == 'malformed':
    print('{broken SECRET-OUTPUT')
    sys.exit(0)
if mode == 'large':
    print('x' * 50000)
    time.sleep(30)
def event(value):
    print(json.dumps(value), flush=True)
event({'type': 'thread.started', 'thread_id': 'test-thread'})
event({'type': 'turn.started'})
for index, message in enumerate(control.get('diagnostics', [])):
    event({'type': 'item.completed', 'item': {'id': f'diagnostic-{index}', 'type': 'error', 'message': message}})
decision = control.get('decision', {'say': '검토 결과를 저장했습니다.', 'status': 'complete',
    'follow_up': {'at': '2026-09-17T09:00:00+09:00', 'instruction': 'Review the new source.'}})
if mode == 'tool':
    event({'type': 'item.started', 'item': {'type': 'command_execution', 'command': 'unsafe'}})
if control.get('tool_type'):
    event({'type': 'item.completed', 'item': {'id': 'tool-1', 'type': control['tool_type']}})
if mode == 'nonfinite':
    decision = {'say': '', 'status': 'continue', 'tools': [{'name': 'calculate', 'arguments': {'x': float('nan')}}]}
event({'type': 'item.completed', 'item': {'id': 'i1', 'type': 'agent_message',
    'text': json.dumps({'decision_json': json.dumps(decision)})}})
if control.get('failed_event'):
    event({'type': 'turn.failed', 'error': {'message': 'Failed after an intermediate message'}})
if control.get('top_level_error'):
    event({'type': 'error', 'message': 'Unknown protocol failure after an intermediate message'})
if mode != 'incomplete':
    event({'type': 'turn.completed', 'usage': control.get('usage', {'input_tokens': 12, 'output_tokens': 7})})
'''


@pytest.fixture
def fake_codex(tmp_path):
    binary = tmp_path / "fake-codex"
    binary.write_text(f"#!{sys.executable}\n{FAKE_CODEX}")
    binary.chmod(0o700)
    control_path = tmp_path / "control.json"
    control_path.write_text("{}")
    auth = tmp_path / "auth"
    auth.mkdir()
    config = RunnerConfig(codex_home=auth, jobs_dir=tmp_path / "jobs", codex_bin=str(binary), timeout_seconds=5)

    def configure(**values):
        control_path.write_text(json.dumps(values))

    def calls():
        path = tmp_path / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    return config, configure, calls


async def test_scoped_native_search_preserves_observed_events(fake_codex, request_model):
    config, configure, calls = fake_codex
    configure(tool_type="web_search")
    request = request_model.model_copy(update={"web_search": True})
    response = await runner_for(config).run(request)
    assert response.web_searches[0].id == "tool-1"
    assert 'web_search="live"' in calls()[0]["args"]
    assert 'features.shell_tool=false' in calls()[0]["args"]
    assert all('features.' + feature + '=true' in calls()[0]['args']
               for feature in ('code_mode', 'code_mode_host', 'code_mode_only'))
    assert all('features.' + feature + '=false' in calls()[0]['args']
               for feature in ('unified_exec', 'apps', 'multi_agent', 'plugins'))
    assert await runner_for(config).run(request) == response
    assert len(calls()) == 1
    with pytest.raises(ProviderFault, match="different input"):
        await runner_for(config).run(request_model)


def test_search_disabled_keeps_legacy_request_identity(request_model):
    import hashlib

    old = request_model.model_dump(exclude={"web_search", "reasoning_effort"})
    legacy = hashlib.sha256(json.dumps(old, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    assert request_digest(request_model) == legacy
    assert request_digest(request_model.model_copy(update={"web_search": True})) != legacy
    assert request_digest(request_model.model_copy(update={"reasoning_effort": "max"})) != legacy


async def test_max_effort_is_explicit_and_bound_to_the_durable_request(fake_codex, request_model):
    config, _, calls = fake_codex
    request = request_model.model_copy(update={"model": "gpt-6-astra", "reasoning_effort": "max"})
    result = await runner_for(config).run(request)
    assert 'model_reasoning_effort="max"' in calls()[0]["args"]
    receipt = json.loads((config.jobs_dir / f"{request.request_id}.json").read_text())
    assert receipt["requested_execution"] == {"model": "gpt-6-astra", "reasoning_effort": "max"}
    assert await runner_for(config).run(request) == result
    with pytest.raises(ProviderFault, match="different input"):
        await runner_for(config).run(request.model_copy(update={"reasoning_effort": "high"}))
    assert len(calls()) == 1


def test_pinned_cli_duplicate_web_item_id_is_narrowly_normalized():
    from quant_company.providers.codex_runner import strict_json

    event = '{"type":"item.completed","item":{"id":"item_2","type":"web_search","id":"exec-abc","query":"latest"}}'
    assert strict_json(event, cli_web_event=True)['item']['id'] == 'exec-abc'
    for raw in [event, '{"id":"item_1","id":"exec-abc","type":"agent_message"}',
                '{"id":"item_1","id":"exec-abc","type":"web_search","query":"one","query":"two"}']:
        with pytest.raises(ValueError, match='Duplicate JSON key'):
            strict_json(raw, cli_web_event=raw != event)


@pytest.fixture
def request_model():
    return ProviderRequest(request_id="turn-01", model="gpt-5.6-luna", prompt="Review the source and return a decision.")


def runner_for(config, **kwargs):
    return CodexRunner(config, environment={"PATH": os.environ["PATH"], "HOME": str(config.codex_home.parent)}, **kwargs)


def test_runtime_default_allows_long_validator_completion(monkeypatch, tmp_path):
    auth = tmp_path / "auth"
    auth.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(auth))
    monkeypatch.setenv("CODEX_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.delenv("CODEX_TIMEOUT_SECONDS", raising=False)
    assert runner_from_environment().config.timeout_seconds == 900
    compose = Path("deploy/compose.yaml").read_text()
    assert "CODEX_TIMEOUT_SECONDS: ${CODEX_TIMEOUT_SECONDS:-900}" in compose
    assert "COMPANY_MODEL_TIMEOUT_SECONDS: ${COMPANY_MODEL_TIMEOUT_SECONDS:-960}" in compose


async def wait_for_path(path: Path, timeout=3):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > deadline:
            raise AssertionError(f"Expected fixture was not produced: {path.name}")
        await asyncio.sleep(0.01)
    return json.loads(path.read_text())


async def assert_process_stopped(pid: int) -> None:
    # Linux can briefly retain an orphan as a non-running zombie until init reaps it.
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        stat = Path(f"/proc/{pid}/stat")
        if stat.exists() and stat.read_text().rsplit(")", 1)[1].split()[0] == "Z":
            return
        await asyncio.sleep(0.01)
    raise AssertionError("A cancelled process is still running")


async def test_http_client_to_process_to_typed_receipt_and_replay(fake_codex, request_model):
    config, _, calls = fake_codex
    runner = runner_for(config)
    app = create_app(runner=runner, token="private-runtime-token")
    client = RuntimeClient("http://runtime", "private-runtime-token", transport=httpx.ASGITransport(app=app))
    result = await client.run(request_model)
    assert result.request_id == request_model.request_id
    assert result.thread_id == "test-thread"
    assert result.decision.follow_up.at.isoformat() == "2026-09-17T09:00:00+09:00"
    assert result.usage == {"input_tokens": 12, "output_tokens": 7}
    assert len(calls()) == 1

    # New runtime object means recovery is from disk, not a process-local memo.
    app2 = create_app(runner=runner_for(config), token="private-runtime-token")
    recovered = await RuntimeClient("http://runtime", "private-runtime-token",
                                    transport=httpx.ASGITransport(app=app2)).run(request_model)
    assert recovered == result
    assert len(calls()) == 1
    receipt = json.loads((config.jobs_dir / "turn-01.json").read_text())
    assert receipt["state"] == "complete"
    assert receipt["input_digest"] == request_digest(request_model)
    assert receipt["result"]["decision"]["follow_up"]["at"] == "2026-09-17T09:00:00+09:00"
    assert not list(config.jobs_dir.glob(".turn-*"))
    assert (config.jobs_dir / "turn-01.json").stat().st_mode & 0o777 == 0o600


async def test_changed_input_reuses_no_inference(fake_codex, request_model):
    config, _, calls = fake_codex
    runner = runner_for(config)
    await runner.run(request_model)
    with pytest.raises(ProviderFault, match="different input") as caught:
        await runner.run(request_model.model_copy(update={"prompt": "changed request"}))
    assert caught.value.code == "uncertain"
    assert len(calls()) == 1


async def test_diagnostic_error_items_allow_valid_completion_without_leaking_messages(fake_codex, request_model):
    config, configure, calls = fake_codex
    configure(diagnostics=['Optional metadata unavailable SECRET-DIAGNOSTIC', 'Optional integration disabled'])
    runner = runner_for(config)
    result = await runner.run(request_model)
    assert result.decision.status == "complete"
    assert result.usage == {"input_tokens": 12, "output_tokens": 7}
    assert await runner_for(config).run(request_model) == result
    assert len(calls()) == 1
    assert "SECRET-DIAGNOSTIC" not in (config.jobs_dir / "turn-01.json").read_text()


@pytest.mark.parametrize("control,code", [
    ({"mode": "incomplete"}, "uncertain"),
    ({"failed_event": True}, "invalid_output"),
    ({"top_level_error": True}, "invalid_output"),
    ({"decision": {"say": "", "status": "complete"}}, "invalid_output"),
    ({"diagnostics": [None]}, "invalid_output"),
])
async def test_diagnostics_cannot_replace_a_valid_successful_turn(fake_codex, request_model, control, code):
    config, configure, _ = fake_codex
    configure(**{"diagnostics": ['Optional diagnostic: usage limit metadata unavailable'], **control})
    with pytest.raises(ProviderFault) as caught:
        await runner_for(config).run(request_model)
    assert caught.value.code == code


@pytest.mark.parametrize("tool_type", ["command_execution", "file_change", "mcp_tool_call", "web_search"])
async def test_diagnostics_do_not_allow_executable_tool_items(fake_codex, request_model, tool_type):
    config, configure, _ = fake_codex
    configure(diagnostics=['Optional diagnostic'], tool_type=tool_type)
    with pytest.raises(ProviderFault) as caught:
        await runner_for(config).run(request_model)
    assert caught.value.code == "uncertain"


async def test_child_environment_and_cli_capabilities_are_restricted(fake_codex, request_model):
    config, _, calls = fake_codex
    source = {"PATH": os.environ["PATH"], "HOME": str(config.codex_home.parent),
              "SLACK_BOT_TOKEN": "SECRET-SLACK", "DATABASE_URL": "SECRET-DB",
              "AWS_SECRET_ACCESS_KEY": "SECRET-AWS", "MODEL_RUNTIME_TOKEN": "SECRET-RUNTIME",
              "HTTP_PROXY": "SECRET-PROXY", "NODE_OPTIONS": "SECRET-NODE"}
    await CodexRunner(config, environment=source).run(request_model)
    call = calls()[0]
    assert not any(name in call["environment"] for name in set(source) - {"PATH", "HOME"})
    assert "SECRET" not in json.dumps(call)
    args = call["args"]
    assert args[:5] == ["exec", "--ignore-user-config", "--ignore-rules", "--strict-config", "--sandbox"]
    assert args[5] == "read-only"
    for setting in ('forced_login_method="chatgpt"', 'model_provider="openai"', "features.hooks=false",
                    "features.shell_tool=false", "features.apps=false", "mcp_servers={}",
                    "features.multi_agent=false", "features.computer_use=false", "features.plugins=false"):
        assert setting in args
    assert not any("dangerously" in arg for arg in args)
    assert request_model.prompt not in args
    assert request_model.prompt in call["stdin"]


@pytest.mark.parametrize("variable", ["OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_ACCESS_TOKEN"])
async def test_api_credentials_are_rejected_before_process(fake_codex, request_model, variable):
    config, _, calls = fake_codex
    with pytest.raises(ProviderFault) as caught:
        await CodexRunner(config, environment={variable: "SECRET-API"}).run(request_model)
    assert caught.value.code == "auth"
    assert "SECRET" not in str(caught.value)
    assert not calls()


@pytest.mark.parametrize("control,code", [
    ({"login": "Logged in using API key: SECRET-API"}, "auth"),
    ({"login": "Not logged in", "login_exit": 1}, "auth"),
    ({"version": "0.100.0"}, "unavailable"),
])
async def test_auth_or_cli_mismatch_prevents_inference(fake_codex, request_model, control, code):
    config, configure, calls = fake_codex
    configure(**control)
    with pytest.raises(ProviderFault) as caught:
        await runner_for(config).run(request_model)
    assert caught.value.code == code
    assert "SECRET" not in str(caught.value)
    assert not calls()
    assert not (config.jobs_dir / "turn-01.json").exists()


async def test_config_failure_does_not_mark_running_or_call_model(fake_codex, request_model):
    config, configure, calls = fake_codex
    configure(config_error='Error loading config.toml: unknown configuration field SECRET-PATH')
    with pytest.raises(ProviderFault) as caught:
        await runner_for(config).run(request_model)
    assert caught.value.code == "unavailable"
    assert "SECRET" not in str(caught.value)
    assert not (config.jobs_dir / "turn-01.json").exists()
    assert not calls()


@pytest.mark.parametrize("mode,code", [
    ("malformed", "invalid_output"), ("nonfinite", "invalid_output"),
    ("crash", "uncertain"), ("incomplete", "uncertain"), ("tool", "uncertain"), ("auth", "auth"),
])
async def test_failed_result_stays_terminal_after_restart(fake_codex, request_model, mode, code):
    config, configure, calls = fake_codex
    configure(mode=mode)
    for _ in range(2):
        with pytest.raises(ProviderFault) as caught:
            await runner_for(config).run(request_model)
        assert caught.value.code == code
        assert "SECRET" not in str(caught.value)
    assert len(calls()) == 1
    assert "SECRET" not in (config.jobs_dir / "turn-01.json").read_text()


@pytest.mark.parametrize("decision,reason", [
    ({"say": "SECRET-RAW", "status": "wait"}, "wait_requires_delegation"),
    ({"say": "SECRET-RAW", "status": "complete", "tools": [
        {"name": "maintenance_status", "arguments": {}}]}, "tools_require_continue"),
    ({"say": "SECRET-RAW", "status": "complete", "delegations": [
        {"agent": "data", "instruction": "SECRET-RAW"}]}, "delegation_requires_wait"),
    ({"say": "", "status": "complete"}, "empty_completion"),
    ({"say": "SECRET-RAW", "status": "complete", "SECRET-FIELD": "SECRET-RAW"}, "invalid_shape"),
])
async def test_invalid_decision_receipt_preserves_safe_cause_without_retry(
        fake_codex, request_model, decision, reason):
    config, configure, calls = fake_codex
    configure(decision=decision)
    for _ in range(2):
        with pytest.raises(ProviderFault) as caught:
            await runner_for(config).run(request_model)
        assert caught.value.code == "invalid_output"
        assert f"decision_contract:{reason}" in caught.value.message
    receipt_text = (config.jobs_dir / "turn-01.json").read_text()
    receipt = json.loads(receipt_text)
    assert receipt["state"] == "failed" and "result" not in receipt
    assert "SECRET" not in receipt_text
    assert len(calls()) == 1


@pytest.mark.parametrize("decision", [
    {"say": "진단 접수를 요청합니다.", "status": "continue", "tools": [
        {"name": "maintenance_review", "arguments": {}}]},
    {"say": "진단은 접수됐고 호출 한도로 대기합니다. 분석 완료는 아닙니다.", "status": "complete"},
])
async def test_background_review_dispatch_accepts_continue_then_report_completion(
        fake_codex, request_model, decision):
    config, configure, _ = fake_codex
    configure(decision=decision)
    response = await runner_for(config).run(request_model)
    assert response.decision.status == decision["status"]
    assert json.loads((config.jobs_dir / "turn-01.json").read_text())["state"] == "complete"


@pytest.mark.parametrize("decision", [
    {"say": "", "status": "complete"},
    {"say": "Done", "status": "wait"},
    {"say": "Done", "status": "complete", "follow_up": {"at": "2026-09-17", "instruction": "Again"}},
    {"say": "Done", "status": "complete", "follow_up": {"at": "2026-09-17T09:00:00", "instruction": "Again"}},
])
async def test_empty_or_invalid_datetime_decisions_are_rejected(fake_codex, request_model, decision):
    config, configure, _ = fake_codex
    configure(decision=decision)
    with pytest.raises(ProviderFault) as caught:
        await runner_for(config).run(request_model)
    assert caught.value.code == "invalid_output"


@pytest.mark.parametrize("usage", [{"input_tokens": -1}, {"output_tokens": 1.2}, {"output_tokens": True}])
async def test_usage_requires_nonnegative_integer_token_units(fake_codex, request_model, usage):
    config, configure, _ = fake_codex
    configure(usage=usage)
    with pytest.raises(ProviderFault) as caught:
        await runner_for(config).run(request_model)
    assert caught.value.code == "invalid_output"


async def test_explicit_quota_defers_then_retries_same_bound_input(fake_codex, request_model):
    config, configure, calls = fake_codex
    configure(mode="quota")
    for _ in range(2):
        with pytest.raises(ProviderFault) as caught:
            await runner_for(config).run(request_model)
        assert caught.value.code == "quota"
        assert 0 < caught.value.retry_after_seconds <= 900
    assert len(calls()) == 1
    path = config.jobs_dir / "turn-01.json"
    receipt = json.loads(path.read_text())
    assert receipt["state"] == "deferred"
    atomic_json(path, {**receipt, "retry_at": time.time() - 1})
    configure(mode="success")
    assert (await runner_for(config).run(request_model)).decision.status == "complete"
    assert len(calls()) == 2


@pytest.mark.parametrize("identity", ["turn-01", "news-screen-01", "news-search-01", "news-01", "quant-feed-01"])
async def test_orphan_running_receipt_never_restarts_inference(fake_codex, request_model, identity):
    config, _, calls = fake_codex
    request_model = request_model.model_copy(update={"request_id": identity})
    config.jobs_dir.mkdir()
    atomic_json(config.jobs_dir / f"{identity}.json", {
        "version": 1, "request_id": identity, "input_digest": request_digest(request_model),
        "state": "running", "started_at": time.time() - 600,
    })
    with pytest.raises(ProviderFault) as caught:
        await runner_for(config).run(request_model)
    assert caught.value.code == "uncertain"
    assert not calls()


async def test_corrupt_receipt_is_not_overwritten_or_replayed(fake_codex, request_model):
    config, _, calls = fake_codex
    config.jobs_dir.mkdir()
    path = config.jobs_dir / "turn-01.json"
    path.write_text('{"version":1, BROKEN')
    with pytest.raises(ProviderFault) as caught:
        await runner_for(config).run(request_model)
    assert caught.value.code == "uncertain"
    assert path.read_text() == '{"version":1, BROKEN'
    assert not calls()


async def test_live_duplicate_and_other_turn_receive_busy(fake_codex, request_model):
    config, configure, calls = fake_codex
    configure(mode="delay")
    runner = runner_for(config)
    operation = asyncio.create_task(runner.run(request_model))
    deadline = time.monotonic() + 3
    while not calls() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    for candidate in (request_model, request_model.model_copy(update={"request_id": "other"})):
        with pytest.raises(ProviderFault) as caught:
            await runner_for(config).run(candidate)
        assert caught.value.code == "busy"
    await operation
    assert len(calls()) == 1


async def test_news_and_company_have_independent_single_slots(fake_codex, request_model):
    config, configure, calls = fake_codex
    configure(mode="delay", delay_seconds=1)
    news = request_model.model_copy(update={"request_id": "news-screen-01"})
    # Separate runner objects prove the limit is a filesystem lock, not an in-memory semaphore.
    operations = [asyncio.create_task(runner_for(config).run(request)) for request in (request_model, news)]
    try:
        async with asyncio.timeout(3):
            while len(calls()) < 2:
                await asyncio.sleep(0.01)
        assert all(not operation.done() for operation in operations)
        for identity in ("turn-01", "another-turn", "maintenance-01", "news-screen-01", "news-search-02", "news-03"):
            with pytest.raises(ProviderFault) as caught:
                await runner_for(config).run(request_model.model_copy(update={"request_id": identity}))
            assert caught.value.code == "busy"
        results = await asyncio.gather(*operations)
        assert len(calls()) == 2
        for request, result in zip((request_model, news), results, strict=True):
            assert await runner_for(config).run(request) == result
        with pytest.raises(ProviderFault, match="different input"):
            await runner_for(config).run(news.model_copy(update={"prompt": "Changed news input"}))
        assert len(calls()) == 2
    finally:
        for operation in operations:
            operation.cancel()
        await asyncio.gather(*operations, return_exceptions=True)


async def test_three_independent_single_slots_with_durable_receipts(fake_codex, request_model):
    config, configure, calls = fake_codex
    configure(mode="delay", delay_seconds=1)
    requests = [request_model.model_copy(update={"request_id": identity})
                for identity in ("company-quant-test", "news-quant-test", "quant-feed-test")]
    operations = [asyncio.create_task(runner_for(config).run(request)) for request in requests]
    try:
        async with asyncio.timeout(3):
            while len(calls()) < 3:
                await asyncio.sleep(0.01)
        assert all(not operation.done() for operation in operations)
        for request in requests:
            with pytest.raises(ProviderFault) as caught:
                await runner_for(config).run(request.model_copy(update={"request_id": request.request_id + "-second"}))
            assert caught.value.code == "busy"
        results = await asyncio.gather(*operations)
        for request, result in zip(requests, results, strict=True):
            assert await runner_for(config).run(request) == result
        assert len(calls()) == 3
        assert {json.loads(p.read_text())["execution_lane"] for p in config.jobs_dir.glob("*.json")} == {"company", "news", "quant"}
    finally:
        for operation in operations:
            operation.cancel()
        await asyncio.gather(*operations, return_exceptions=True)


async def test_cancelling_news_does_not_cancel_company_lane(fake_codex, request_model):
    config, configure, calls = fake_codex
    configure(mode="delay", delay_seconds=1)
    runner = runner_for(config)
    client = RuntimeClient("http://runtime", "token", transport=httpx.ASGITransport(
        app=create_app(runner=runner, token="token")))
    news = request_model.model_copy(update={"request_id": "news-cancel-01"})
    operations = [asyncio.create_task(client.run(request)) for request in (request_model, news)]
    try:
        async with asyncio.timeout(3):
            while len(calls()) < 2:
                await asyncio.sleep(0.01)
        assert await client.cancel(news.request_id) == "cancelled"
        with pytest.raises(ProviderFault) as caught:
            await operations[1]
        assert caught.value.code == "uncertain"
        completed = await operations[0]
        assert await client.run(request_model) == completed
        with pytest.raises(ProviderFault) as replay:
            await client.run(news)
        assert replay.value.code == "uncertain"
        assert len(calls()) == 2
    finally:
        for operation in operations:
            operation.cancel()
        await asyncio.gather(*operations, return_exceptions=True)


async def test_cancel_endpoint_kills_process_group_and_blocks_replay(fake_codex, request_model):
    config, configure, calls = fake_codex
    configure(mode="hang")
    runner = runner_for(config)
    client = RuntimeClient("http://runtime", "token", transport=httpx.ASGITransport(
        app=create_app(runner=runner, token="token")))
    operation = asyncio.create_task(client.run(request_model))
    pids = await wait_for_path(config.codex_home.parent / "pids.json")
    assert await client.cancel(request_model.request_id) == "cancelled"
    with pytest.raises(ProviderFault) as caught:
        await operation
    assert caught.value.code == "uncertain"
    for pid in pids:
        await assert_process_stopped(pid)
    with pytest.raises(ProviderFault) as replay:
        await runner_for(config).run(request_model)
    assert replay.value.code == "uncertain"
    assert len(calls()) == 1


async def test_cancel_before_turn_arrival_prevents_process(fake_codex, request_model):
    config, _, calls = fake_codex
    runner = runner_for(config)
    assert await runner.cancel(request_model.request_id) == "cancelled"
    with pytest.raises(ProviderFault) as caught:
        await runner.run(request_model)
    assert caught.value.code == "uncertain"
    assert not calls()


async def test_cancel_completed_turn_preserves_cached_result(fake_codex, request_model):
    config, _, calls = fake_codex
    runner = runner_for(config)
    result = await runner.run(request_model)
    assert await runner.cancel(request_model.request_id) == "completed"
    assert await runner.run(request_model) == result
    assert len(calls()) == 1


@pytest.mark.parametrize("mode", ["hang", "large"])
async def test_time_and_output_limits_are_terminal_and_cleanup(fake_codex, request_model, mode):
    config, configure, calls = fake_codex
    configure(mode=mode)
    config = RunnerConfig(codex_home=config.codex_home, jobs_dir=config.jobs_dir, codex_bin=config.codex_bin,
                          timeout_seconds=0.3, max_stdout_bytes=1024)
    with pytest.raises(ProviderFault) as caught:
        await runner_for(config).run(request_model)
    assert caught.value.code == ("timeout" if mode == "hang" else "invalid_output")
    if mode == "hang":
        for pid in await wait_for_path(config.codex_home.parent / "pids.json"):
            await assert_process_stopped(pid)
    with pytest.raises(ProviderFault):
        await runner_for(config).run(request_model)
    assert len(calls()) == 1
    assert not list(config.jobs_dir.glob(".turn-*"))


async def test_transport_task_cancellation_finishes_and_caches(fake_codex, request_model):
    config, configure, calls = fake_codex
    configure(mode="delay")
    app = create_app(runner=runner_for(config), token="token")
    client = RuntimeClient("http://runtime", "token", transport=httpx.ASGITransport(app=app))
    operation = asyncio.create_task(client.run(request_model))
    deadline = time.monotonic() + 3
    while not calls() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    operation.cancel()
    with pytest.raises((asyncio.CancelledError, ProviderFault)):
        await operation
    await asyncio.gather(*app.state.operations)
    assert (await client.run(request_model)).decision.status == "complete"
    assert len(calls()) == 1


async def test_http_auth_and_validation_never_echo_payload(fake_codex):
    config, _, calls = fake_codex
    app = create_app(runner=runner_for(config), token="runtime-secret")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://runtime") as client:
        assert (await client.get("/healthz")).json() == {"status": "ok"}
        for path in ("/v1/turns", "/v1/turns/turn-01/cancel"):
            response = await client.post(path, json={"SECRET-PROMPT": True})
            assert response.status_code == 401
            assert "SECRET" not in response.text
        invalid = await client.post("/v1/turns", json={"request_id": "../../escape", "prompt": "SECRET-PROMPT"},
                                    headers={"Authorization": "Bearer runtime-secret"})
        assert invalid.status_code == 422
        assert "SECRET" not in invalid.text
    assert not calls()


async def test_client_treats_lost_response_as_uncertain_without_retry(request_model):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("SECRET-TRANSPORT-DETAIL")

    client = RuntimeClient("http://runtime", "secret", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderFault) as caught:
        await client.run(request_model)
    assert caught.value.code == "uncertain"
    assert "SECRET" not in str(caught.value)
    assert len(calls) == 1


async def test_client_rejects_wrong_request_id_and_raw_error_message(request_model):
    def handler(request):
        return httpx.Response(502, json={"code": "invalid_output", "message": "SECRET-RAW", "retry_after_seconds": 0})

    client = RuntimeClient("http://runtime", "secret", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderFault) as caught:
        await client.run(request_model)
    assert caught.value.code == "invalid_output"
    assert "SECRET" not in str(caught.value)

    def mismatch(request):
        return httpx.Response(200, json={"request_id": "wrong", "decision": {"say": "done", "status": "complete"}})

    with pytest.raises(ProviderFault) as caught:
        await RuntimeClient("http://runtime", "secret", transport=httpx.MockTransport(mismatch)).run(request_model)
    assert caught.value.code == "uncertain"
