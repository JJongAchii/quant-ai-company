"""Real PostgreSQL/Temporal and subprocesses; Slack events and model catalogs are synthetic."""

import asyncio
import json
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from temporalio.worker import Replayer

from quant_company.account_workflow import AccountControlWorkflow
from quant_company.api import create_app
from quant_company.company import Company, PolicyError, fingerprint
from quant_company.contracts import AgentDecision, ProviderRequest, ProviderResponse, ProviderSession, Role
from quant_company.model_control import ModelControl
from quant_company.model_policy import bind, effective_role, parse_command, policy, selection
from quant_company.quant_feed import schedule
from quant_company.quant_feed.contracts import QuantSource
from quant_company.quant_feed.store import QuantFeedStore
from quant_company.runtime import make_accounts_worker
from quant_company.web_tools import prepare as web_prepare

from .conftest import queued_turns
from .test_accounts import accounts as accounts
from .test_accounts import fake_codex as fake_codex
from .test_maintenance import make_maintainer
from .test_news import news as news
from .test_news import ready_article, source
from .test_research_audit_delivery import complete as complete_audit
from .test_research_audit_delivery import setup_audit
from .test_research_controller import active as active_audit
from .test_research_controller import mission as mission
from .test_slack import event, signed
from .test_staff_development import staff_company as staff_company
from .test_temporal import temporal_environment as temporal_environment


@pytest.fixture
def quant(company, tmp_path, monkeypatch):
    company.settings.quant_feed_enabled = True
    company.settings.quant_feed_publish_enabled = True
    company.settings.quant_feed_channel_id = "CQUANT"
    company.settings.quant_feed_owner_user = "UHUMAN"
    company.settings.company_web_enabled = False
    company.roles["quant_scout"] = Role(id="quant_scout", name="Quant Scout", mission="Synthetic assignment test",
                                       model="gpt-5.6-luna", instructions="Delivery only", tools=[], can_delegate_to=[])
    source = QuantSource(id="example", publisher="Example research", kind="seed", url="https://example.org/paper",
                         article_hosts=["example.org", "arxiv.org"])
    path = tmp_path / "sources.json"
    path.write_text(json.dumps([source.model_dump()]))
    company.settings.quant_feed_sources_file = path
    monkeypatch.setattr(schedule, "delivery_time", lambda at: at)
    return QuantFeedStore(company)


def original(store):
    text = ("Synthetic study by Example Author, published 2026-09-01. US equities from 2000 to 2020. "
            "No performance or tradability claim is tested in this model assignment fixture. ") * 5
    claimed = store.claim_source()
    source = store.sources()["example"]
    store.save_source(claimed, {"ok": True, "entries": [{"url": source.url, "title": "Research", "metadata": {}}]})
    candidate = store.claim_candidate()
    return store.save_original(candidate, {"ok": True, "url": candidate["url"], "original_sha256": fingerprint(text),
        "pages": [{"location": "PDF p.1", "text": text}], "metadata": {}, "links": [], "truncated": False})


def test_briefing_alias():
    assert parse_command("모델 지정 브리핑 candidate-a high")["target"] == "market_brief"


@pytest.fixture
def office(accounts):
    company = accounts[0]
    company.settings.model_assignments_enabled = True
    return accounts


def submit(company, text, *, agent="director", channel="CACC", identity=None, thread=None):
    identity = identity or uuid4().hex
    return company.ingest(event_key="slack:TTEST:" + identity, text=text, owner="UHUMAN", agent=agent,
                          channel=channel, thread_ts=thread or identity, model_command=parse_command(text))


def receipt(company, result):
    with company.db.transaction() as conn:
        return conn.execute("SELECT receipt FROM model_assignment_commands WHERE id=%s",
                            (result["task_id"],)).fetchone()["receipt"]


def current(company):
    with company.db.transaction() as conn:
        return policy(conn)


@pytest.mark.parametrize("text", ['"모델 지정 data candidate-a high"', '`모델 지정 data candidate-a high`',
                                  '> 모델 지정 data candidate-a high', '설명: 모델 지정 data candidate-a high'])
def test_quoted_commands_do_not_change_models(text):
    assert parse_command(text) is None


@pytest.mark.parametrize("text", ['모델 지정 data candidate-a', '모델 지정 data candidate-a ultra',
                                  '이번 작업 모델 candidate-a high', '모델 지정 data x/../../secret high'])
def test_incomplete_or_unsupported_syntax_is_help_not_inference(text):
    assert parse_command(text) == {"action": "help"}


async def test_malformed_assignment_returns_complete_guide_without_changing_policy(office, credentials):
    company, control, _, _, _, calls = office
    body, headers = signed(event(credentials, text="모델 지정 개발", type="message", channel="CACC"),
                           credentials["director"])
    client = TestClient(create_app(company.settings, company, credentials))
    first = client.post("/slack/events/director", content=body, headers=headers)
    assert first.status_code == 200 and first.json()["owner_control"]
    assert client.post("/slack/events/director", content=body, headers=headers).json()["duplicate"]
    before = current(company)
    await control.tick()
    assert current(company) == before
    assert receipt(company, first.json())["outcome"] == "help"
    with company.db.transaction() as conn:
        row = conn.execute("SELECT text FROM outbox").fetchone()
        assert "`도움말`" in row["text"] and "`모델 목록`" in row["text"]
        assert "`모델 계정 예비로 전환`" in row["text"]
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM turns").fetchone()["n"] == 0
    assert calls() == []


async def test_pin_is_owner_authenticated_model_free_idempotent_and_shared(office, credentials):
    company, control, _, _, _, calls = office
    client = TestClient(create_app(company.settings, company, credentials))
    body, headers = signed(event(credentials, text="모델 지정 데이터 candidate-a high", type="message", channel="CACC"),
                           credentials["director"])
    first = client.post("/slack/events/director", content=body, headers=headers)
    assert first.status_code == 200
    assert first.json()["owner_control"]
    assert client.post("/slack/events/director", content=body, headers=headers).json()["duplicate"]
    assert company.project_state(first.json()["project_id"])["turns"] == []
    await asyncio.gather(control.tick(), control.tick())
    assert current(company)["revision"] == 1
    fresh = Company(company.settings, company.roles)
    with fresh.db.transaction() as conn:
        selected = effective_role(fresh, conn, "data")
    assert (selected.model, selected.reasoning_effort) == ("candidate-a", "high")
    employees = fresh.runtime_context()["employees"]
    assert next(r for r in employees if r["id"] == "data")["model"] == "candidate-a"
    result = client.get("/v1/agents", headers={"Authorization": "Bearer test-token-with-more-than-24-characters"})
    assert next(r for r in result.json() if r["id"] == "data")["model"] == "candidate-a"
    assert calls() == []
    assert receipt(company, first.json())["outcome"] == "applied"
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 1


@pytest.mark.parametrize("change", ["owner", "channel", "signature", "bot", "workspace", "role", "dm"])
def test_wrong_identity_or_channel_cannot_create_model_commands(office, credentials, change):
    company = office[0]
    company.settings.slack_allowed_users.append("UOTHER")
    payload = event(credentials, text="모델 지정 data candidate-a high", type="message", channel="CACC")
    role = "director"
    if change == "owner":
        payload["event"]["user"] = "UOTHER"
    elif change == "channel":
        payload["event"]["channel"] = "CQUANT"
    elif change == "bot":
        payload["event"]["bot_id"] = "BOTHER"
    elif change == "workspace":
        payload["team_id"] = "TOTHER"
    elif change == "role":
        role, payload["api_app_id"] = "data", credentials["data"]["app_id"]
    elif change == "dm":
        payload["event"]["channel"] = "DOWNER"
    body, headers = signed(payload, credentials[role])
    if change == "signature":
        headers["x-slack-signature"] = "v0=bad"
    TestClient(create_app(company.settings, company, credentials)).post(f"/slack/events/{role}", content=body, headers=headers)
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM model_assignment_commands").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM turns").fetchone()["n"] == 0


async def test_rollback_reset_history_and_catalog_are_durable_without_inference(office):
    company, control, _, _, _, calls = office
    for text in ["모델 지정 data candidate-a high", "모델 지정 data candidate-b max", "모델 배정 복원 1"]:
        result = submit(company, text)
        await control.tick()
        assert receipt(company, result)["outcome"] == "applied"
    assert current(company)["bindings"]["data"]["model"] == "candidate-a"
    assert current(company)["revision"] == 3
    for text, expected in [("모델 배정 이력", "history"), ("모델 배정 상태", "status"), ("모델 목록", "catalog"),
                           ("모델 자동 data", "applied")]:
        result = submit(company, text)
        await control.tick()
        assert receipt(company, result)["outcome"] == expected
    assert current(company)["bindings"] == {}
    assert current(company)["revision"] == 4
    company.db.migrate()
    assert current(company)["revision"] == 4
    assert calls() == []


@pytest.mark.parametrize("text", ["모델 지정 data missing-model high", "모델 지정 data candidate-a low",
                                  "모델 지정 director candidate-a high", "모델 지정 nobody candidate-a max",
                                  "모델 지정 tech_scout candidate-a high", "모델 배정 복원 123"])
async def test_invalid_assignment_fails_without_changing_policy_or_calling_model(office, text):
    company, control, _, _, _, calls = office
    result = submit(company, text)
    await control.tick()
    assert receipt(company, result)["outcome"] == "rejected"
    assert current(company)["revision"] == 0
    assert calls() == []


async def test_task_override_wins_persists_for_followups_and_web_but_not_other_tasks(office):
    company, control, _, _, _, _ = office
    company.roles["data"].tools.append("web_search")
    result = submit(company, "이번 작업 모델 candidate-a high\n이번 자료를 확인해줘", agent="data", channel="CQUANT")
    await control.tick()
    assert receipt(company, result)["outcome"] == "task_queued"
    first = queued_turns(company, result["project_id"])[0]
    original = company.prepare_turn(first)["request"]
    assert original["model"] == "candidate-a"
    submit(company, "모델 지정 data candidate-b max")
    await control.tick()
    decision = AgentDecision(status="continue", say="검색합니다", tools=[
        {"name": "web_search", "arguments": {"query": "synthetic", "limit": 1}}])
    searches = web_prepare(company, first, decision)
    assert searches[0]["provider_request"]["model"] == "candidate-a"
    assert searches[0]["provider_request"]["reasoning_effort"] == "high"
    company.commit_turn(first, ProviderResponse(request_id=first, decision=AgentDecision(status="continue", say="다음 확인")))
    second = queued_turns(company, result["project_id"])[0]
    assert company.prepare_turn(second)["request"]["model"] == "candidate-a"
    other = company.ingest(event_key="normal-data", text="별도 업무", owner="UHUMAN", agent="data")
    assert company.prepare_turn(queued_turns(company, other["project_id"])[0])["request"]["model"] == "candidate-b"
    with company.db.transaction() as conn:
        assert selection(company, conn, "financial_strategist")["source"] == "default"


async def test_signed_task_selection_does_not_run_in_the_control_channel(office, credentials):
    company, control, _, _, _, _ = office
    client = TestClient(create_app(company.settings, company, credentials))
    text = "이번 작업 모델 candidate-a high\n이번 데이터만 확인해줘"
    for channel in ("CACC", "DOWNER"):
        payload = event(credentials, text=text, type="message", channel=channel)
        payload["api_app_id"] = credentials["data"]["app_id"]
        body, headers = signed(payload, credentials["data"])
        result = client.post("/slack/events/data", content=body, headers=headers).json()
        if channel == "CACC":
            assert result["ignored"]
        else:
            await control.tick()
            assert receipt(company, result)["outcome"] == "task_queued"


async def test_revoked_owner_and_revised_task_are_rejected_before_dispatch(office):
    company, control, _, _, _, calls = office
    first = submit(company, "모델 지정 data candidate-a high")
    company.settings.slack_allowed_users.remove("UHUMAN")
    await control.tick()
    assert receipt(company, first)["outcome"] == "rejected"
    company.settings.slack_allowed_users.append("UHUMAN")
    second = submit(company, "이번 작업 모델 candidate-a high\n업무", agent="data", channel="CQUANT")
    with company.db.transaction() as conn:
        conn.execute("UPDATE projects SET revision=revision+1 WHERE id=%s", (second["project_id"],))
    await control.tick()
    assert receipt(company, second)["outcome"] == "rejected"
    assert company.project_state(second["project_id"])["turns"] == []
    assert calls() == []


async def test_staff_practice_snapshot_and_maintainer_inheritance_survive_new_pins(office, staff_company):
    from quant_company.staff.store import StaffStore

    company, control, _, _, _, _ = office
    submit(company, "모델 지정 engineer candidate-a high")
    await control.tick()
    staff = StaffStore(company)
    run = staff.enqueue("maintainer", "UHUMAN")
    submit(company, "모델 지정 maintainer candidate-b max")
    await control.tick()
    ready = staff.prepare()
    assert ready["run_id"] == run
    assert ready["request"]["model"] == "candidate-a"
    assert company.runtime_context()["background_model_requests"]["maintainer"]["model"] == "candidate-b"
    with company.db.transaction() as conn:
        assert not company.roles["engineer"].active
        assert effective_role(company, conn, "engineer").model == "candidate-a"


async def test_maintenance_new_requests_follow_pin_but_replays_keep_frozen_model(office):
    company, control, _, _, _, _ = office
    maintenance = make_maintainer(company)
    submit(company, "모델 지정 engineer candidate-a high")
    await control.tick()
    maintenance.store.collect()
    job = maintenance.store.next_job()
    assert job
    first = maintenance.store.prepare_call(job, "model-policy-test", "Synthetic maintenance")
    assert first["request"]["model"] == "candidate-a"
    submit(company, "모델 지정 maintainer candidate-b max")
    await control.tick()
    assert maintenance.store.prepare_call(job, "model-policy-test", "ignored")["request"] == first["request"]
    replay = maintenance.store.prepare_call(job, "frozen-test", "Replay", model="legacy-model", reasoning_effort=None)
    assert replay["request"]["model"] == "legacy-model" and replay["request"]["reasoning_effort"] is None


@pytest.mark.parametrize("target", ["reporter", "news_screening", "news_search"])
async def test_news_editor_screening_and_discovery_have_separate_targets(office, news, target):
    from quant_company.news.discovery import NewsDiscoveryStore
    from quant_company.news.screening import NewsScreeningStore

    company, control, _, _, _, _ = office
    submit(company, f"모델 지정 {target} candidate-a high")
    await control.tick()
    ready_article(news)
    company.settings.news_optimization_enabled = target != "reporter"
    company.settings.news_search_enabled = target == "news_search"
    if target == "news_search":
        company.settings.news_sources_file.write_text(json.dumps([source().model_dump(), source("media", kind="media").model_dump()]))
    prepare = {"reporter": news.prepare_review, "news_screening": NewsScreeningStore(company).prepare,
               "news_search": NewsDiscoveryStore(company).prepare}[target]
    ready = prepare()
    assert ready["request"]["model"] == "candidate-a"
    submit(company, f"모델 지정 {target} candidate-b max")
    await control.tick()
    assert prepare()["request"] == ready["request"]


async def test_quant_feed_uses_new_policy_without_changing_frozen_calls(office, quant):
    company, control, _, _, _, _ = office
    submit(company, "모델 지정 quant_scout candidate-a high")
    await control.tick()
    original(quant)
    ready = quant.prepare()
    assert ready["request"]["model"] == "candidate-a"
    submit(company, "모델 지정 quant_scout candidate-b max")
    await control.tick()
    assert quant.prepare()["request"] == ready["request"]


async def test_frozen_retry_and_session_continuation_keep_original_selection(office):
    company, control, _, _, _, _ = office
    work = company.ingest(event_key="frozen", text="기존 업무", owner="UHUMAN", agent="data")
    turn = queued_turns(company, work["project_id"])[0]
    original = company.prepare_turn(turn)["request"]
    submit(company, "모델 지정 data candidate-b max")
    await control.tick()
    assert company.prepare_turn(turn)["request"] == original
    continuation = ProviderRequest(request_id="continuation", model="candidate-b", reasoning_effort="max", prompt="next",
        session=ProviderSession(id="test-session", previous_request_id=turn))
    with company.db.transaction() as conn:
        saved = bind(company, conn, continuation, "data")
    assert (saved.model, saved.reasoning_effort) == (original["model"], original["reasoning_effort"])


async def test_audit_producer_keeps_session_selection_across_policy_change(office, mission):
    from quant_company.research.audit_delivery import packet_data

    company, control, _, _, _, _ = office
    submit(company, "모델 지정 validator candidate-a high")
    await control.tick()
    _, _, identity = setup_audit(mission, {"source.txt": "Synthetic service evidence.\n" * 6000})
    first = company.prepare_turn(identity)["request"]
    assert first["model"] == "candidate-a"
    data = packet_data(first["prompt"])
    assert data["phase"] != "final"
    submit(company, "모델 지정 validator candidate-b max")
    await control.tick()
    complete_audit(company, identity, {"packet_digest": data["packet_digest"], "notes": "Synthetic notes only."})
    _, following = active_audit(mission)
    second = company.prepare_turn(following)["request"]
    assert (second["model"], second["reasoning_effort"]) == ("candidate-a", "high")
    assert second["session"]["previous_request_id"] == identity
    assert packet_data(second["prompt"])


async def test_director_can_select_new_supported_model_and_status_works_without_catalog(office):
    company, control, _, _, configure, calls = office
    result = submit(company, "모델 지정 director candidate-a max")
    await control.tick()
    assert receipt(company, result)["outcome"] == "applied"
    configure(catalog_error=True)
    for text in ("모델 배정 상태", "모델 배정 이력"):
        result = submit(company, text)
        await control.tick()
        assert receipt(company, result)["outcome"] in {"status", "history"}
    assert next(role for role in company.runtime_context()["employees"] if role["id"] == "director")["model"] == "candidate-a"
    assert calls() == []


async def test_catalog_failure_and_account_switch_during_check_fail_closed(office):
    company, control, _, _, configure, calls = office
    configure(catalog_error=True)
    first = submit(company, "모델 지정 data candidate-a high")
    await control.tick()
    assert receipt(company, first)["outcome"] == "rejected"
    configure()
    second = submit(company, "모델 지정 data candidate-a high")
    selector = ModelControl(company, control.client)
    _, account = selector.pending()
    models = await control.client.models(account["profile"])
    with company.db.transaction() as conn:
        conn.execute("UPDATE model_account_policy SET profile='backup',revision=revision+1 WHERE id=1")
    selector.apply(UUID(second["task_id"]), account, models)
    assert receipt(company, second)["outcome"] == "rejected"
    assert current(company)["revision"] == 0
    assert calls() == []


async def test_restore_revalidates_models_and_feature_disable_does_not_queue_work(office):
    company, control, _, _, configure, _ = office
    submit(company, "모델 지정 data candidate-a high")
    await control.tick()
    submit(company, "모델 지정 data candidate-b max")
    await control.tick()
    configure(catalog=[{"model": "candidate-b", "supportedReasoningEfforts": [{"reasoningEffort": "max"}]}])
    result = submit(company, "모델 배정 복원 1")
    await control.tick()
    assert receipt(company, result)["outcome"] == "rejected"
    assert current(company)["bindings"]["data"]["model"] == "candidate-b"
    company.settings.model_assignments_enabled = False
    result = submit(company, "이번 작업 모델 candidate-a high\n업무", agent="data", channel="CQUANT")
    assert receipt(company, result)["outcome"] == "disabled"
    assert company.project_state(result["project_id"])["turns"] == []


def test_direct_ingress_cannot_forge_selection(office):
    company = office[0]
    with pytest.raises(PolicyError):
        company.ingest(event_key="slack:forged", text="그냥 업무", owner="UHUMAN", channel="CACC", thread_ts="1",
                       model_command={"action": "pin", "target": "data", "model": "candidate-a", "reasoning_effort": "high"})


@pytest.mark.integration
async def test_real_temporal_applies_durable_command_and_replays_history(office, temporal_environment):
    company, control, _, _, _, calls = office
    client = temporal_environment.client
    company.settings.temporal_task_queue = "model-policy-" + uuid4().hex
    result = submit(company, "모델 지정 data candidate-a high")
    async with make_accounts_worker(client, company, control):
        handle = await client.start_workflow(AccountControlWorkflow.run, id="models-" + uuid4().hex,
            task_queue=company.settings.temporal_task_queue + "-accounts")
        async with asyncio.timeout(15):
            while receipt(company, result) is None:
                await asyncio.sleep(0.05)
        await Replayer(workflows=[AccountControlWorkflow]).replay_workflow(await handle.fetch_history())
        await handle.cancel()
    assert receipt(company, result)["outcome"] == "applied"
    assert current(Company(company.settings, company.roles))["revision"] == 1
    assert calls() == []
