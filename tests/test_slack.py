import hashlib
import hmac
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from quant_company.api import create_app
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.slack import SlackOutbox

from .conftest import queued_turns


def event(credentials, *, role="director", text="<@UBOT0> 연구를 준비해 주세요", **overrides):
    content = {"type": "app_mention", "user": "UHUMAN", "channel": "CQUANT", "ts": "100.001", "text": text}
    content.update(overrides)
    return {"type": "event_callback", "team_id": "TTEST", "api_app_id": credentials[role]["app_id"],
            "event_id": "EV1", "event": content}


def signed(payload, credential, stamp=None):
    raw = json.dumps(payload).encode()
    stamp = str(stamp if stamp is not None else int(time.time()))
    sig = "v0=" + hmac.new(credential["signing_secret"].encode(), b"v0:" + stamp.encode() + b":" + raw,
                            hashlib.sha256).hexdigest()
    return raw, {"x-slack-request-timestamp": stamp, "x-slack-signature": sig, "content-type": "application/json"}


def test_signed_ingress_ack_commits_work_without_model_call(company, credentials):
    client = TestClient(create_app(company.settings, company, credentials))
    raw, headers = signed(event(credentials), credentials["director"])
    response = client.post("/slack/events/director", content=raw, headers=headers)
    assert response.status_code == 200
    assert response.json()["duplicate"] is False
    assert client.post("/slack/events/director", content=raw, headers=headers).json()["duplicate"] is True
    state = company.project_state(response.json()["project_id"])
    assert state["turns"][0]["status"] == "queued"
    assert state["turns"][0]["attempts"] == 0


@pytest.mark.parametrize("mutation", ["signature", "expired", "other_workspace", "bot_echo", "unauthorized_user"])
def test_invalid_requests_and_bot_echo_never_start_work(company, credentials, mutation):
    payload = event(credentials)
    if mutation == "other_workspace":
        payload["team_id"] = "TOTHER"
    if mutation == "bot_echo":
        payload["event"]["bot_id"] = "BBOT"
    if mutation == "unauthorized_user":
        payload["event"]["user"] = "UOTHER"
    raw, headers = signed(payload, credentials["director"], 1 if mutation == "expired" else None)
    if mutation == "signature":
        headers["x-slack-signature"] = "v0=wrong"
    client = TestClient(create_app(company.settings, company, credentials))
    response = client.post("/slack/events/director", content=raw, headers=headers)
    assert response.status_code in {200, 401, 409}
    assert company.list_projects() == []


def test_multi_app_delivery_is_routed_and_deduplicated(company, credentials):
    client = TestClient(create_app(company.settings, company, credentials))
    for role in credentials:
        payload = event(credentials, role=role)
        raw, headers = signed(payload, credentials[role])
        assert client.post(f"/slack/events/{role}", content=raw, headers=headers).status_code == 200
    assert len(company.list_projects()) == 1
    state = company.project_state(company.list_projects()[0]["id"])
    assert len(state["tasks"]) == 1


def make_outbox(company):
    req = company.ingest(event_key="outbox", text="Work", owner="user", channel="CQUANT", thread_ts="100.1")
    turn = queued_turns(company, req["project_id"])[0]
    company.prepare_turn(turn)
    company.commit_turn(turn, ProviderResponse(request_id=turn,
                        decision=AgentDecision(say="Result", status="complete")))


async def test_slack_timeout_is_uncertain_and_never_blindly_reposted(company, credentials):
    make_outbox(company)
    requests = []

    def timeout(request):
        requests.append(request)
        raise httpx.ReadTimeout("Response lost after Slack may have accepted", request=request)

    outbox = SlackOutbox(company, credentials, httpx.MockTransport(timeout))
    await outbox.send_one()
    await outbox.send_one()
    assert len(requests) == 1
    with company.db.transaction() as conn:
        assert conn.execute("SELECT status FROM outbox").fetchone()["status"] == "uncertain"


async def test_slack_rate_limit_and_delivery_receipt(company, credentials):
    make_outbox(company)
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "2"})
        return httpx.Response(200, json={"ok": True, "ts": "200.1"})

    outbox = SlackOutbox(company, credentials, httpx.MockTransport(respond))
    await outbox.send_one()
    assert await outbox.send_one() is False
    with company.db.transaction() as conn:
        conn.execute("UPDATE outbox SET next_at=now()-interval '1 second'")
    await outbox.send_one()
    assert calls[0]["client_msg_id"] == calls[1]["client_msg_id"]
    with company.db.transaction() as conn:
        receipt = conn.execute("SELECT status,sent_ts FROM outbox").fetchone()
    assert receipt == {"status": "delivered", "sent_ts": "200.1"}


def test_operator_api_requires_secret(company, credentials):
    client = TestClient(create_app(company.settings, company, credentials))
    assert client.get("/v1/projects").status_code == 401
    headers = {"Authorization": "Bearer " + company.settings.operator_token.get_secret_value()}
    result = client.post("/v1/requests", headers=headers, json={"request_id": "test", "text": "Research"})
    assert result.status_code == 200
    assert client.get("/v1/projects", headers=headers).json()[0]["id"] == result.json()["project_id"]


def test_status_request_works_without_inference_during_quota_pause(company, credentials):
    client = TestClient(create_app(company.settings, company, credentials))
    raw, headers = signed(event(credentials), credentials["director"])
    project_id = client.post("/slack/events/director", content=raw, headers=headers).json()["project_id"]
    with company.db.transaction() as conn:
        conn.execute("UPDATE runtime_control SET paused_until=now()+interval '1 hour',reason='quota' WHERE id=1")
    raw, headers = signed(event(credentials, text="상태", ts="101.001", thread_ts="100.001", type="message"),
                           credentials["director"])
    assert client.post("/slack/events/director", content=raw, headers=headers).status_code == 200
    state = company.project_state(project_id)
    status = next(message for message in state["messages"] if message["kind"] == "status")
    assert "quota" in status["text"]
    assert all(turn["attempts"] == 0 for turn in state["turns"])


@pytest.mark.parametrize("phrase", ["진행상황이 어떻게되니?", "진행상황이 어떻게되?",
                                    "진행상황이 어떻게 되니?", "진행중이야?"])
def test_natural_progress_question_enters_conversation_router(company, credentials, phrase):
    seed = company.ingest(event_key="seed", text="Research", owner="UHUMAN",
                          channel="CQUANT", thread_ts="100.001")
    payload = event(credentials, text=phrase, ts="101.001", thread_ts="100.001", type="message")
    raw, headers = signed(payload, credentials["director"])
    client = TestClient(create_app(company.settings, company, credentials))
    response = client.post("/slack/events/director", content=raw, headers=headers)
    assert response.status_code == 200
    assert response.json()["project_id"] == seed["project_id"]
    assert client.post("/slack/events/director", content=raw, headers=headers).json()["duplicate"] is True
    state = company.project_state(seed["project_id"])
    assert len(state["turns"]) == 2
    assert any(task["kind"] == "routing" and task["instruction"] == phrase
               for task in state["tasks"])
    assert not any(message["kind"] == "status" and message["author"] == "director"
                   for message in state["messages"])
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0


def test_slack_question_at_project_task_limit_gets_one_visible_notice(company, credentials):
    company.settings.company_max_project_tasks = 1
    seed = company.ingest(event_key="seed", text="Research", owner="UHUMAN",
                          channel="CQUANT", thread_ts="100.001")
    payload = event(credentials, text="새 질문입니다", ts="101.001", thread_ts="100.001", type="message")
    raw, headers = signed(payload, credentials["director"])
    client = TestClient(create_app(company.settings, company, credentials))
    response = client.post("/slack/events/director", content=raw, headers=headers)
    assert response.status_code == 200
    assert response.json()["project_id"] == seed["project_id"]
    assert client.post("/slack/events/director", content=raw, headers=headers).json()["duplicate"] is True
    state = company.project_state(seed["project_id"])
    assert len(state["turns"]) == 1
    assert any(message["kind"] == "status" and "새 스레드" in message["text"]
               for message in state["messages"])
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 1


def test_slack_question_after_forty_tasks_queues_a_real_turn(company, credentials):
    assert company.settings.company_max_project_tasks == 0
    seed = company.ingest(event_key="seed", text="Research", owner="UHUMAN",
                          channel="CQUANT", thread_ts="100.001")
    for index in range(40):
        company.ingest(event_key=f"history-{index}", text="Prior work", owner="UHUMAN",
                       project_id=seed["project_id"])
    payload = event(credentials, text="새 질문입니다", ts="101.001", thread_ts="100.001", type="message")
    raw, headers = signed(payload, credentials["director"])
    response = TestClient(create_app(company.settings, company, credentials)).post(
        "/slack/events/director", content=raw, headers=headers)
    assert response.status_code == 200
    state = company.project_state(seed["project_id"])
    assert len(state["turns"]) == 42
    assert not any("업무 한도" in message["text"] for message in state["messages"])
