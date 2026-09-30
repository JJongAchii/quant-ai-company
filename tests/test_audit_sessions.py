"""Real subprocesses with a labelled fake model; no subscription calls."""
# ruff: noqa: F811 -- pytest injects imported fixtures by name

import json
from dataclasses import replace

import httpx
import pytest

from quant_company.contracts import ProviderFault, ProviderRequest, ProviderSession
from quant_company.providers.client import RuntimeClient
from quant_company.providers.codex_runner import atomic_json
from quant_company.providers.codex_sessions import turn_usage

from .test_codex_runtime import fake_codex, runner_for  # noqa: F401 -- shared subprocess fixture


def request(identity, previous=None, session="audit-01"):
    return ProviderRequest(request_id=identity, model="gpt-6-astra", reasoning_effort="max", prompt="Synthetic audit.",
                           session=ProviderSession(id=session, previous_request_id=previous))


async def test_stateless_wire_requests_remain_compatible_with_pinned_runtimes():
    sent = []

    def respond(incoming):
        value = json.loads(incoming.content)
        sent.append(value)
        return httpx.Response(200, json={"request_id": value["request_id"], "provider": "codex",
                                        "decision": {"say": "Synthetic", "status": "complete"}})

    client = RuntimeClient("http://fixture", "synthetic-token", transport=httpx.MockTransport(respond))
    await client.run(ProviderRequest(request_id="ordinary", model="gpt-6-astra", prompt="Synthetic"))
    await client.run(request("audit-start"))
    assert "session" not in sent[0]
    assert sent[1]["session"] == {"id": "audit-01", "previous_request_id": None}


async def test_explicit_session_survives_restart_and_never_forks(fake_codex):
    config, _, calls = fake_codex
    first = await runner_for(config).run(request("audit-turn-1"))
    second = await runner_for(config).run(request("audit-turn-2", "audit-turn-1"))
    assert first.thread_id == second.thread_id
    assert "--ephemeral" not in calls()[0]["args"]
    assert calls()[1]["args"][-3:] == ["resume", first.thread_id, "-"]
    assert calls()[0]["args"][calls()[0]["args"].index("--cd") + 1] == (
        calls()[1]["args"][calls()[1]["args"].index("--cd") + 1])
    assert await runner_for(config).run(request("audit-turn-2", "audit-turn-1")) == second
    for value in [request("fork", "audit-turn-1"), request("cross-audit", "audit-turn-2", "audit-02"),
                  request("new-first"), request("changed-model", "audit-turn-2").model_copy(update={"model": "other"})]:
        with pytest.raises(ProviderFault, match="reconciliation"):
            await runner_for(config).run(value)
    backup = config.codex_home.parent / "backup"
    backup.mkdir()
    with pytest.raises(ProviderFault, match="reconciliation"):
        await runner_for(replace(config, backup_codex_home=backup)).run(
            request("changed-account", "audit-turn-2"), profile="backup", revision=1)
    assert len(calls()) == 2


async def test_complete_receipt_recovers_session_commit_window(fake_codex):
    config, _, calls = fake_codex
    await runner_for(config).run(request("audit-turn-1"))
    path = config.jobs_dir / "sessions/audit-01/session.json"
    state = json.loads(path.read_text())
    atomic_json(path, {**state, "head": None, "thread_id": None, "inflight": "audit-turn-1"})
    await runner_for(config).run(request("audit-turn-2", "audit-turn-1"))
    assert len(calls()) == 2
    assert json.loads(path.read_text())["head"] == "audit-turn-2"


async def test_resumed_cli_usage_is_saved_once_per_turn_with_raw_totals_preserved(fake_codex):
    config, configure, calls = fake_codex
    # Captured counters from two real Astra calls on CLI 0.154.0. last_token_usage
    # in the session log independently confirmed these per-call differences.
    first = {"input_tokens": 9746, "cached_input_tokens": 5888, "output_tokens": 117, "reasoning_output_tokens": 79}
    cumulative = {"input_tokens": 20772, "cached_input_tokens": 15488, "output_tokens": 153, "reasoning_output_tokens": 79}
    configure(usage=first)
    one = await runner_for(config).run(request("usage-1"))
    configure(usage=cumulative)
    two = await runner_for(config).run(request("usage-2", "usage-1"))
    assert one.usage == first
    assert two.usage == {"input_tokens": 11026, "cached_input_tokens": 9600, "output_tokens": 36, "reasoning_output_tokens": 0}
    assert await runner_for(config).run(request("usage-2", "usage-1")) == two
    raw = json.loads((config.jobs_dir / "usage-2.json").read_text())
    assert raw["session_usage"] == cumulative
    configure(usage={"input_tokens": 30772, "cached_input_tokens": 23488, "output_tokens": 203, "reasoning_output_tokens": 89})
    three = await runner_for(config).run(request("usage-3", "usage-2"))
    assert three.usage == {"input_tokens": 10000, "cached_input_tokens": 8000, "output_tokens": 50, "reasoning_output_tokens": 10}
    assert len(calls()) == 3


@pytest.mark.parametrize("current,previous", [({}, {}), ({"input_tokens": 9, "output_tokens": 1}, {"input_tokens": 10}),
    ({"input_tokens": 10, "output_tokens": 1, "cached_input_tokens": 11}, {})])
def test_missing_reset_or_impossible_session_usage_requires_reconciliation(current, previous):
    with pytest.raises(ProviderFault):
        turn_usage(current, previous)


@pytest.mark.parametrize("mode", ["crash", "incomplete", "malformed"])
async def test_ambiguous_session_head_is_never_replayed_or_skipped(fake_codex, mode):
    config, configure, calls = fake_codex
    await runner_for(config).run(request("audit-turn-1"))
    configure(mode=mode)
    with pytest.raises(ProviderFault):
        await runner_for(config).run(request("audit-turn-2", "audit-turn-1"))
    configure()
    for value in [request("audit-turn-2", "audit-turn-1"), request("audit-turn-3", "audit-turn-2"),
                  request("skip-failed", "audit-turn-1")]:
        with pytest.raises(ProviderFault):
            await runner_for(config).run(value)
    assert len(calls()) == 2


async def test_unexpected_thread_id_cannot_commit_a_session(fake_codex):
    config, configure, calls = fake_codex
    await runner_for(config).run(request("audit-turn-1"))
    configure(thread_id="different-thread")
    with pytest.raises(ProviderFault, match="bound audit session"):
        await runner_for(config).run(request("audit-turn-2", "audit-turn-1"))
    assert len(calls()) == 2
    assert json.loads((config.jobs_dir / "audit-turn-2.json").read_text())["state"] == "failed"
