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
from quant_company.providers.codex_runtime import create_app

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
    time.sleep(0.3)
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
decision = control.get('decision', {'say': '검토 결과를 저장했습니다.', 'status': 'complete',
    'follow_up': {'at': '2026-09-17T09:00:00+09:00', 'instruction': 'Review the new source.'}})
if mode == 'tool':
    event({'type': 'item.started', 'item': {'type': 'command_execution', 'command': 'unsafe'}})
if mode == 'nonfinite':
    decision = {'say': '', 'status': 'continue', 'tools': [{'name': 'calculate', 'arguments': {'x': float('nan')}}]}
event({'type': 'item.completed', 'item': {'id': 'i1', 'type': 'agent_message',
    'text': json.dumps({'decision_json': json.dumps(decision)})}})
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


@pytest.fixture
def request_model():
    return ProviderRequest(request_id="turn-01", model="gpt-5.6-luna", prompt="Review the source and return a decision.")


def runner_for(config, **kwargs):
    return CodexRunner(config, environment={"PATH": os.environ["PATH"], "HOME": str(config.codex_home.parent)}, **kwargs)


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


async def test_orphan_running_receipt_never_restarts_inference(fake_codex, request_model):
    config, _, calls = fake_codex
    config.jobs_dir.mkdir()
    atomic_json(config.jobs_dir / "turn-01.json", {
        "version": 1, "request_id": "turn-01", "input_digest": request_digest(request_model),
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
