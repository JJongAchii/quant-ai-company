"""The Analyst's signal turns on Claude Fable 5.2 (owner, 2026-11-24: "모델은 CODEX말고 claude 5.2 Fable 이면 좋겠다").
Real PostgreSQL and subprocess boundaries; the CLIs are fakes, never a subscription call."""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from quant_company.config import Settings
from quant_company.contracts import AgentDecision, ProviderFault, ProviderRequest
from quant_company.model_policy import selection
from quant_company.providers.claude_runner import CLI_VERSION, FABLE_CLI_VERSION, ClaudeRunner, cli
from quant_company.providers.routing import CLAUDE_FABLE, CLAUDE_MODEL, CLAUDE_MODELS, is_claude, own_lane
from quant_company.signals.store import SignalStore

from .signals_fixtures import DAY, KST, clock, signals, snapshot  # noqa: F401
from .test_claude_runtime import fake_claude  # noqa: F401

DECISION = AgentDecision(status="complete", say="합성 응답")


def kst(hour, minute=0):
    return datetime(DAY.year, DAY.month, DAY.day, hour, minute, tzinfo=KST)


def test_fable_routes_to_claude_on_the_company_lane():
    request = ProviderRequest(request_id=str(uuid4()), model=CLAUDE_FABLE, reasoning_effort="high", prompt="p")
    assert is_claude(CLAUDE_FABLE) and own_lane(request) == "company"
    assert CLAUDE_FABLE in {item["model"] for item in CLAUDE_MODELS}


@pytest.fixture
def fable_cli(fake_claude, tmp_path):  # noqa: F811
    """The default fake CLI (2.1.280, Opus) and a second one for Fable in its own directory (own control and calls)."""
    config, configure, calls = fake_claude
    root = tmp_path / "next"
    root.mkdir()
    binary = root / "claude-next"
    binary.write_text(Path(config.binary).read_text())
    binary.chmod(0o700)
    control = {"version": FABLE_CLI_VERSION, "model": CLAUDE_FABLE, "used_model": CLAUDE_FABLE,
               "review": {"decision_json": DECISION.model_dump_json()}}
    (root / "control.json").write_text(json.dumps(control))
    configure(model=CLAUDE_MODEL, used_model=CLAUDE_MODEL, review={"decision_json": DECISION.model_dump_json()})

    def fable_calls():
        log = root / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def configure_fable(**changes):
        (root / "control.json").write_text(json.dumps({**control, **changes}))

    from dataclasses import replace

    return replace(config, fable_binary=str(binary)), calls, fable_calls, configure_fable


def company_request(model):
    return ProviderRequest(request_id=str(uuid4()), model=model, reasoning_effort="high", prompt="Synthetic signal task")


async def test_only_fable_runs_on_the_newer_cli(fable_cli):
    """CLI 2.1.280 refuses claude-fable-5-2 (unrecognized_model), so Fable turns run on their own binary; every other
    Claude turn keeps 2.1.280."""
    config, calls, fable_calls, _ = fable_cli
    assert cli(config, CLAUDE_FABLE) == (config.fable_binary, FABLE_CLI_VERSION)
    assert cli(config, CLAUDE_MODEL) == (config.binary, CLI_VERSION)
    runner = ClaudeRunner(config, source_environment={"PATH": "/usr/bin:/bin"})
    response = await runner.run(company_request(CLAUDE_FABLE))
    assert response.decision == DECISION and response.usage["cli_version"] == FABLE_CLI_VERSION
    [call] = fable_calls()
    assert call["args"][call["args"].index("--model") + 1] == CLAUDE_FABLE
    assert call["env"]["CLAUDE_CONFIG_DIR"] == str(config.auth_dir.resolve())  # the same isolated login
    assert calls() == []
    opus = await runner.run(company_request(CLAUDE_MODEL))
    assert opus.usage["cli_version"] == CLI_VERSION and len(calls()) == 1 and len(fable_calls()) == 1


async def test_fable_refuses_a_cli_of_another_version(fable_cli):
    config, calls, fable_calls, configure_fable = fable_cli
    configure_fable(version=CLI_VERSION)
    with pytest.raises(ProviderFault) as fault:
        await ClaudeRunner(config, source_environment={"PATH": "/usr/bin:/bin"}).run(company_request(CLAUDE_FABLE))
    assert fault.value.code == "unavailable" and fable_calls() == [] and calls() == []


def test_signal_model_setting():
    assert (Settings().analyst_signals_model, Settings().analyst_signals_effort) == (CLAUDE_FABLE, "high")
    assert Settings(analyst_signals_model="").analyst_signals_model == ""
    for bad in ({"analyst_signals_model": "gpt-6-astra"}, {"analyst_signals_effort": "ultra"}):
        with pytest.raises(ValidationError):
            Settings(**bad)
    compose = (Path(__file__).resolve().parents[1] / "deploy" / "compose.yaml").read_text()
    # No colon: an empty runtime.env value keeps the Analyst's own binding instead of falling back to Fable.
    assert "ANALYST_SIGNALS_MODEL: ${ANALYST_SIGNALS_MODEL-claude-fable-5-2}" in compose
    assert "ANALYST_SIGNALS_EFFORT: ${ANALYST_SIGNALS_EFFORT:-high}" in compose


def test_dockerfile_installs_the_fable_cli_for_the_claude_runtime():
    dockerfile = (Path(__file__).resolve().parents[1] / "deploy" / "Dockerfile").read_text()
    assert f"@anthropic-ai/claude-code@{FABLE_CLI_VERSION}" in dockerfile
    assert f"/claude-next --version | grep -Fx '{FABLE_CLI_VERSION} (Claude Code)'" in dockerfile
    assert "COPY --from=claude-next-dist /claude-next /usr/local/bin/claude-next" in dockerfile
    assert f"@anthropic-ai/claude-code@{CLI_VERSION}" in dockerfile  # every other Claude turn keeps its CLI


def one(company, query, *args):
    with company.db.transaction() as conn:
        return conn.execute(query, args).fetchone()


def test_signal_task_carries_the_signal_model(signals):  # noqa: F811
    store = SignalStore(signals)
    store.schedule(kst(7))
    [row] = store.due_asks(kst(8))
    assert store.ask(row, snapshot("intraday"), kst(8))
    task = one(signals, "SELECT id,model_selection FROM tasks WHERE kind='trading_signal'")
    assert task["model_selection"] == {"model": CLAUDE_FABLE, "reasoning_effort": "high", "source": "analyst_signals"}
    # With model assignments on (production), the task's own selection is the turn's model; the Analyst's thread
    # answers and briefing keep the Analyst's binding.
    company = SimpleNamespace(settings=SimpleNamespace(model_assignments_enabled=True), roles=signals.roles)
    assert selection(company, None, "market_brief", task=dict(task))["model"] == CLAUDE_FABLE


def test_empty_signal_model_keeps_the_analyst_binding(signals):  # noqa: F811
    signals.settings.analyst_signals_model = ""
    store = SignalStore(signals)
    store.schedule(kst(7))
    [row] = store.due_asks(kst(8))
    assert store.ask(row, snapshot("intraday"), kst(8))
    assert one(signals, "SELECT model_selection FROM tasks WHERE kind='trading_signal'")["model_selection"] is None
