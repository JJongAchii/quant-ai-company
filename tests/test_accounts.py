"""Real PostgreSQL/Temporal and subprocess receipts; simulated Slack and Codex output."""

import asyncio
import json
import time
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from temporalio import activity
from temporalio.worker import Replayer, Worker

from quant_company.account_gateway import create_app as gateway_app
from quant_company.account_workflow import AccountControlWorkflow
from quant_company.accounts import AccountControl, AccountProvider, parse_command
from quant_company.api import create_app
from quant_company.company import Company
from quant_company.contracts import ProviderFault
from quant_company.execution import TurnExecutor
from quant_company.providers.client import RuntimeClient
from quant_company.providers.codex_runner import atomic_json, request_digest
from quant_company.providers.codex_runtime import create_app as runtime_app
from quant_company.runtime import dispatch_once, make_accounts_worker, make_worker
from quant_company.workflow import CompanyTurnWorkflow

from .conftest import queued_turns
from .legacy_turn_workflow import LegacyCompanyTurnWorkflow
from .test_codex_runtime import fake_codex as fake_codex
from .test_codex_runtime import request_model as request_model
from .test_codex_runtime import runner_for
from .test_slack import event, signed
from .test_temporal import temporal_environment as temporal_environment


@pytest.fixture
def accounts(company, fake_codex, tmp_path):
    company.settings.model_accounts_owner_user = "UHUMAN"
    company.settings.model_accounts_enabled = True
    config, configure, calls = fake_codex
    backup = tmp_path / "backup-auth"
    backup.mkdir()
    config = replace(config, backup_codex_home=backup)
    runner = runner_for(config)
    client = RuntimeClient("http://runtime", "private-token", transport=httpx.ASGITransport(app=runtime_app(
        runner=runner, token="private-token")))
    return company, AccountControl(company, client), AccountProvider(company, client), runner, configure, calls


def command(company, text="모델 계정 예비로 전환", *, identity=None):
    return company.ingest(event_key="slack:TTEST:DOWNER:" + (identity or uuid4().hex), text=text,
        owner="UHUMAN", channel="DOWNER", thread_ts="100.1", account_command=parse_command(text))


def policy(company):
    with company.db.transaction() as conn:
        return conn.execute("SELECT * FROM model_account_policy WHERE id=1").fetchone()


@pytest.mark.parametrize("text", ["모델 계정 예비로 전환?", "모델 계정 예비로 전환해줘", "`모델 계정 예비로 전환`",
                                  "> 모델 계정 예비로 전환", "설명: 모델 계정 예비로 전환", "모델 계정 /tmp 로 전환"])
def test_account_commands_require_exact_intent(text):
    assert parse_command(text) is None


async def test_signed_owner_dm_is_model_free_deduplicated_and_persistent(accounts, credentials):
    company, control, _, _, _, calls = accounts
    client = TestClient(create_app(company.settings, company, credentials))
    raw, headers = signed(event(credentials, text="모델 계정 예비로 전환", type="message", channel="DOWNER"),
                          credentials["director"])
    response = client.post("/slack/events/director", content=raw, headers=headers)
    assert response.status_code == 200
    assert response.json()["owner_control"]
    assert client.post("/slack/events/director", content=raw, headers=headers).json()["duplicate"]
    assert company.project_state(response.json()["project_id"])["turns"] == []
    await asyncio.gather(control.tick(), control.tick())
    assert policy(Company(company.settings, company.roles))["profile"] == "backup"
    assert policy(company)["revision"] == 1
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 1
        assert conn.execute("SELECT receipt FROM model_account_commands").fetchone()["receipt"]["outcome"] == "switched"
    assert calls() == []


@pytest.mark.parametrize("change", ["other_user", "other_channel_user", "channel", "bot", "signature",
                                  "workspace", "edited"])
def test_account_control_rejects_untrusted_events(accounts, credentials, change):
    company, _, _, _, _, _ = accounts
    company.settings.slack_allowed_users.append("UOTHER")
    payload = event(credentials, text="모델 계정 예비로 전환", type="message", channel="DOWNER")
    if change == "other_user":
        payload["event"]["user"] = "UOTHER"
    elif change == "other_channel_user":
        payload["event"].update(user="UOTHER", channel="CQUANT", type="app_mention",
                                text="<@UBOT0> 모델 계정 예비로 전환")
    elif change == "channel":
        payload["event"]["channel"] = "CUNCONFIGURED"
        payload["event"]["type"] = "app_mention"
        payload["event"]["text"] = "<@UBOT0> 모델 계정 예비로 전환"
    elif change == "bot":
        payload["event"]["bot_id"] = "BOTHER"
    elif change == "workspace":
        payload["team_id"] = "TOTHER"
    elif change == "edited":
        payload["event"]["subtype"] = "message_changed"
    raw, headers = signed(payload, credentials["director"])
    if change == "signature":
        headers["x-slack-signature"] = "v0=invalid"
    response = TestClient(create_app(company.settings, company, credentials)).post(
        "/slack/events/director", content=raw, headers=headers)
    assert response.status_code in {200, 401, 409}
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM model_account_commands").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM turns").fetchone()["n"] == 0


async def test_owner_channel_thread_status_and_switch_are_model_free(accounts, credentials):
    company, control, _, _, _, calls = accounts
    client = TestClient(create_app(company.settings, company, credentials))

    def receive(text, ts, *, mention):
        payload = event(credentials, text=("<@UBOT0> " if mention else "") + text,
                        ts=ts, thread_ts="100.1", channel="CQUANT",
                        type="app_mention" if mention else "message")
        raw, headers = signed(payload, credentials["director"])
        first = client.post("/slack/events/director", content=raw, headers=headers)
        assert first.status_code == 200
        assert first.json()["owner_control"]
        assert client.post("/slack/events/director", content=raw, headers=headers).json()["duplicate"]
        return first.json()

    status = receive("모델 계정 상태", "100.2", mention=True)
    await control.tick()
    assert policy(company)["profile"] == "primary"
    assert policy(company)["revision"] == 0
    switch = receive("모델 계정 예비로 전환", "100.3", mention=False)
    await control.tick()
    assert switch["project_id"] == status["project_id"]
    assert company.project_state(status["project_id"])["turns"] == []
    assert policy(company)["profile"] == "backup"
    assert policy(company)["revision"] == 1
    with company.db.transaction() as conn:
        replies = conn.execute("SELECT channel,thread_ts,text FROM outbox ORDER BY created_at").fetchall()
        assert len(replies) == 2
        assert all(r["channel"] == "CQUANT" and r["thread_ts"] == "100.1" for r in replies)
        assert "회사 공용 모델 계정: 기본" in replies[0]["text"]
        assert "예비 계정으로 전환했습니다" in replies[1]["text"]
    assert calls() == []


async def test_queued_account_switch_rechecks_allowed_channel(accounts, credentials):
    company, control, _, _, _, calls = accounts
    payload = event(credentials, text="<@UBOT0> 모델 계정 예비로 전환", type="app_mention",
                    channel="CQUANT", thread_ts="100.1")
    raw, headers = signed(payload, credentials["director"])
    response = TestClient(create_app(company.settings, company, credentials)).post(
        "/slack/events/director", content=raw, headers=headers)
    assert response.status_code == 200
    assert response.json()["owner_control"]
    company.settings.slack_allowed_channels.clear()
    await control.tick()
    assert policy(company)["profile"] == "primary"
    assert policy(company)["revision"] == 0
    with company.db.transaction() as conn:
        assert conn.execute("SELECT receipt FROM model_account_commands").fetchone()["receipt"]["outcome"] == "rejected"
    assert calls() == []


async def test_quota_switch_preserves_id_cached_completion_and_profile_cooldowns(accounts, request_model):
    company, control, provider, runner, configure, calls = accounts
    completed = request_model.model_copy(update={"request_id": "completed-before-switch"})
    original_result = await provider.run(completed)
    command(company, "모델 계정 상태")
    await control.tick()
    configure(mode="quota")
    with pytest.raises(ProviderFault) as denied:
        await provider.run(request_model)
    assert denied.value.code == "quota"
    with pytest.raises(ProviderFault) as paused:
        await provider.run(request_model.model_copy(update={"request_id": "news-waiting"}))
    assert paused.value.code == "quota"
    assert policy(company)["profile"] == "primary"
    assert len(calls()) == 2
    assert await provider.run(completed) == original_result
    command(company)
    await control.tick()
    configure()
    result = await provider.run(request_model)
    assert result.request_id == request_model.request_id
    assert calls()[-1]["environment"]["CODEX_HOME"] == str(runner.config.backup_codex_home)
    transfers = list((runner.config.jobs_dir / "account-transfers").glob("*.json"))
    assert len(transfers) == 1
    receipt = json.loads(transfers[0].read_text())
    assert receipt["previous_receipt"]["fault"]["code"] == "quota"
    assert receipt["previous_receipt"]["input_digest"] == request_digest(request_model)
    assert await provider.run(completed) == original_result
    assert len(calls()) == 3
    restarted = AccountProvider(Company(company.settings, company.roles), provider.client)
    await restarted.run(request_model.model_copy(update={"request_id": "news-waiting"}))
    assert calls()[-1]["environment"]["CODEX_HOME"] == str(runner.config.backup_codex_home)
    command(company, "모델 계정 기본으로 전환")
    await control.tick()
    assert policy(company)["profile"] == "backup"  # Known exhausted target stays on cooldown.
    with company.db.transaction() as conn:
        conn.execute("UPDATE model_accounts SET paused_until=now()-interval '1 second' WHERE profile='primary'")
        notifications = conn.execute("SELECT text FROM outbox WHERE text LIKE '기본 계정에서 사용 한도%%'").fetchall()
        assert len(notifications) == 1
    command(company, "모델 계정 기본으로 전환")
    await control.tick()
    await restarted.run(request_model.model_copy(update={"request_id": "new-on-primary"}))
    assert calls()[-1]["environment"]["CODEX_HOME"] == str(runner.config.codex_home)
    with pytest.raises(ProviderFault, match="different input"):
        await provider.run(request_model.model_copy(update={"prompt": "changed"}))


@pytest.mark.parametrize("state", ["running", "failed", "cancelled"])
async def test_switch_never_replays_ambiguous_or_cancelled_receipts(accounts, request_model, state):
    company, control, provider, runner, _, calls = accounts
    runner.config.jobs_dir.mkdir()
    atomic_json(runner.config.jobs_dir / f"{request_model.request_id}.json", {
        "version": 1, "request_id": request_model.request_id, "input_digest": request_digest(request_model),
        "state": state, "fault": {"code": "uncertain", "message": "Lost response", "retry_after_seconds": 0}})
    command(company)
    await control.tick()
    with pytest.raises(ProviderFault) as result:
        await provider.run(request_model)
    assert result.value.code == "uncertain"
    assert calls() == []


async def test_failed_auth_and_revoked_owner_do_not_switch(accounts):
    company, control, _, runner, configure, calls = accounts
    configure(login="Logged in using an API key")
    command(company)
    await control.tick()
    assert policy(company)["profile"] == "primary"
    configure()
    runner.config.backup_codex_home.rmdir()
    command(company)
    await control.tick()
    assert policy(company)["revision"] == 0
    runner.config.backup_codex_home.mkdir()
    command(company)
    company.settings.model_accounts_owner_user = "UOTHER"
    await control.tick()
    assert policy(company)["revision"] == 0
    assert calls() == []


async def test_quota_arriving_after_switch_retries_soon_on_new_selection(accounts, request_model):
    company, control, provider, _, _, _ = accounts
    route, _ = provider.select(request_model)
    assert route["profile"] == "primary"
    entered, release = asyncio.Event(), asyncio.Event()

    class DelayedDenial:
        async def run(self, request, *, account):
            assert account == route
            entered.set()
            await release.wait()
            raise ProviderFault("quota", "Quota denial", 900)

    waiting = AccountProvider(company, DelayedDenial())
    task = asyncio.create_task(waiting.run(request_model))
    await entered.wait()
    command(company)
    await control.tick()
    release.set()
    with pytest.raises(ProviderFault) as result:
        await task
    assert result.value.retry_after_seconds == 5
    assert provider.select(request_model) == ({"profile": "backup", "revision": 1}, 0)


async def test_pinned_worker_gateway_routes_without_changing_frozen_request(accounts, request_model):
    company, control, provider, runner, configure, calls = accounts
    company.settings.model_provider = "codex"
    app = gateway_app(company=company, client=provider.client, token="gateway-token")
    legacy = RuntimeClient("http://gateway", "gateway-token", transport=httpx.ASGITransport(app=app))
    configure(mode="quota")
    for _ in range(2):
        with pytest.raises(ProviderFault) as quota:
            await legacy.run(request_model)
        assert quota.value.code == "quota" and quota.value.retry_after_seconds == 30
    assert len(calls()) == 1
    with company.db.transaction() as conn:
        row = conn.execute("SELECT paused_until>now()+interval '14 minutes' AS full_delay FROM model_accounts WHERE profile='primary'").fetchone()
        assert row["full_delay"]
        conn.execute("UPDATE runtime_control SET paused_until=now()+interval '15 minutes',reason='subscription_quota'")
    command(company)
    await control.tick()
    configure()
    result = await legacy.run(request_model)
    assert result.request_id == request_model.request_id
    assert calls()[-1]["environment"]["CODEX_HOME"] == str(runner.config.backup_codex_home)
    assert await legacy.run(request_model) == result
    assert await legacy.cancel(request_model.request_id) == "completed"
    assert len(calls()) == 2
    with company.db.transaction() as conn:
        assert conn.execute("SELECT paused_until FROM runtime_control").fetchone()["paused_until"] is None


async def test_gateway_cannot_accept_an_untrusted_selection_header(accounts, request_model):
    company, _, provider, _, _, calls = accounts
    company.settings.model_provider = "codex"
    app = gateway_app(company=company, client=provider.client, token="gateway-token")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        for headers in ({}, {"Authorization":"Bearer gateway-token", "X-Company-Account":"backup",
                              "X-Company-Account-Revision":"100"}):
            result = await client.post("/v1/turns", json=request_model.model_dump(), headers=headers)
            assert result.status_code == 401
    assert calls() == []
    assert policy(company)["profile"] == "primary"


async def test_private_account_status_does_not_infer_or_expose_credentials(accounts):
    _, _, provider, _, configure, calls = accounts
    async with httpx.AsyncClient(transport=provider.client.transport, base_url="http://runtime") as client:
        assert (await client.get("/v1/accounts")).status_code == 401
        configure(login="secret@example.invalid token-value")
        result = await client.get("/v1/accounts", headers={"Authorization": "Bearer private-token"})
        assert result.json() == {"accounts": [{"profile": p, "authentication": "needs_login"}
                                             for p in ("primary", "backup")]}
    assert calls() == []


@pytest.mark.parametrize("revision,fault", [(0, "quota"), (1, "quota"), (2, "uncertain")])
async def test_runtime_requires_newer_selection_and_explicit_quota(accounts, request_model, revision, fault):
    _, _, _, runner, _, calls = accounts
    runner.config.jobs_dir.mkdir()
    atomic_json(runner.config.jobs_dir / f"{request_model.request_id}.json", {
        "version": 1, "request_id": request_model.request_id, "input_digest": request_digest(request_model),
        "state": "deferred", "account": {"profile": "primary", "revision": 1}, "retry_at": time.time() + 900,
        "fault": {"code": fault, "message": "Observed denial", "retry_after_seconds": 900}})
    with pytest.raises(ProviderFault) as result:
        await runner.run(request_model, profile="backup", revision=revision)
    assert result.value.code == "uncertain"
    assert calls() == []


@pytest.mark.integration
async def test_pre_upgrade_timer_history_replays_and_adopts_wake_on_next_iteration(temporal_environment):
    client = temporal_environment.client
    queue = "account-upgrade-" + uuid4().hex
    calls = 0
    second_iteration = asyncio.Event()

    @activity.defn(name="company_execute_turn")
    async def execute(turn_id: str):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"state": "defer", "seconds": 2}
        if calls == 2:
            second_iteration.set()
            return {"state": "defer", "seconds": 900}
        return {"state": "completed"}

    async with Worker(client, task_queue=queue, workflows=[LegacyCompanyTurnWorkflow], activities=[execute]):
        handle = await client.start_workflow(LegacyCompanyTurnWorkflow.run, "upgrade-turn", id=queue, task_queue=queue)
        async with asyncio.timeout(10):
            while True:
                history = await handle.fetch_history()
                if any(event.HasField("timer_started_event_attributes") for event in history.events):
                    break
                await asyncio.sleep(0.05)
        await Replayer(workflows=[CompanyTurnWorkflow]).replay_workflow(history)
    async with Worker(client, task_queue=queue, workflows=[CompanyTurnWorkflow], activities=[execute]):
        await asyncio.wait_for(second_iteration.wait(), timeout=10)
        await handle.signal("model_account_changed", 1)
        assert await asyncio.wait_for(handle.result(), timeout=10) == {"state": "completed"}
        await Replayer(workflows=[CompanyTurnWorkflow]).replay_workflow(await handle.fetch_history())


@pytest.mark.integration
async def test_real_temporal_switch_wakes_quota_wait_and_replays_history(accounts, temporal_environment):
    company, control, provider, runner, configure, calls = accounts
    client = temporal_environment.client
    company.settings.temporal_task_queue = "accounts-" + uuid4().hex
    control.temporal = client
    configure(mode="quota")
    result = company.ingest(event_key="quota-turn", text="Synthetic task", owner="UHUMAN", agent="data")
    turn = queued_turns(company, result["project_id"])[0]
    async with (make_worker(client, company, TurnExecutor(company, provider)),
                make_accounts_worker(client, company, control)):
        await dispatch_once(client, company)
        handle = client.get_workflow_handle("company-turn-" + turn)
        async with asyncio.timeout(20):
            while True:
                state = await asyncio.to_thread(company.project_state, result["project_id"])
                if state["turns"][0]["status"] == "waiting":
                    break
                await asyncio.sleep(0.1)
        configure()
        command(company)
        configure(decision={"say": "Synthetic task complete", "status": "complete"})
        outcome = await asyncio.wait_for(handle.result(), timeout=20)
        assert outcome["state"] == "completed", company.project_state(result["project_id"])
        history = await handle.fetch_history()
        await Replayer(workflows=[CompanyTurnWorkflow]).replay_workflow(history)
        account_history = await client.get_workflow_handle("company-model-accounts-v1").fetch_history()
        await Replayer(workflows=[AccountControlWorkflow]).replay_workflow(account_history)
    assert len(calls()) == 2
    assert calls()[1]["environment"]["CODEX_HOME"] == str(runner.config.backup_codex_home)
    assert company.project_state(result["project_id"])["turns"][0]["id"] == turn
