"""Real subprocesses with a labelled fake model; no subscription calls."""
# ruff: noqa: F811 -- pytest injects imported fixtures by name

import json
from dataclasses import replace

import httpx
import pytest

from quant_company.contracts import ProviderFault, ProviderRequest, ProviderSession
from quant_company.providers.client import RuntimeClient
from quant_company.providers.codex_runner import atomic_json

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
