"""Real PostgreSQL and Temporal; synthetic Slack/model/GitHub, no external writes."""

import asyncio
import copy
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from psycopg.types.json import Jsonb
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from quant_company.company import Company, PolicyError, load_roles
from quant_company.config import Settings
from quant_company.contracts import AgentDecision, ProviderResponse, ToolRequest
from quant_company.execution import TurnExecutor
from quant_company.maintenance import cases
from quant_company.maintenance.applications import accept_approval
from quant_company.maintenance.identity import role
from quant_company.maintenance.reporting import Reporter
from quant_company.maintenance.requests import status
from quant_company.maintenance.runner import Maintainer
from quant_company.maintenance.store import Store
from quant_company.maintenance.workflow import MaintenanceReportingWorkflow, MaintenanceWorkflow
from quant_company.slack import SlackIngress, SlackOutbox

from .conftest import queued_turns
from .test_maintenance import GitHubFixture, config, prepare
from .test_maintenance_requests import ReviewModel


@pytest.fixture
def improvements(company, credentials):
    prepare(company)
    company.settings.slack_allowed_channels.append("CIMPROVE")
    company.settings.improvements_channel_id = "CIMPROVE"
    company.settings.company_improvements_enabled = True
    company.roles["maintainer"] = role(company.roles["engineer"])
    company.roles["director"].tools += ["maintenance_review", "maintenance_status"]
    Store(company, config()).initialize()
    credentials["maintainer"] = {"app_id": "AMAIN", "bot_user_id": "UMAIN", "bot_token": "synthetic-maintainer",
                                 "signing_secret": "synthetic-signing-secret"}
    return company, credentials


def submit(company, *, channel="CQUANT", thread="2.1", text="개선 담당에게 이 오류 진단을 요청합니다."):
    agent = "maintainer" if channel == "CIMPROVE" else "director"
    result = company.ingest(event_key=str(uuid4()), owner="UHUMAN", agent=agent,
                            text=text, channel=channel, thread_ts=thread)
    with company.db.transaction() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id=%s", (result["task_id"],)).fetchone()
        receipt = company._tool(conn, result["project_id"], ToolRequest(name="maintenance_review", arguments={}), task=task)
        case = conn.execute("SELECT * FROM maintenance_cases WHERE request_id=%s", (receipt["request_id"],)).fetchone()
    return result, receipt, case


def delivery(company, credentials, *, fail=False):
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        if fail:
            raise httpx.ReadTimeout("synthetic outcome unknown")
        return httpx.Response(200, json={"ok": True, "ts": body.get("ts", f"50.{len(calls):06d}")})

    return SlackOutbox(company, credentials, httpx.MockTransport(respond)), calls


async def drain(box):
    for _ in range(25):
        if not await box.send_one():
            return
    raise AssertionError("outbox did not drain")


def test_configuration_requires_channel_and_reuses_engineer():
    with pytest.raises(ValueError, match="explicitly allowed"):
        Settings(company_improvements_enabled=True)
    settings = Settings(company_improvements_enabled=True, improvements_channel_id="CIMPROVE",
                        slack_allowed_channels=["CIMPROVE"])
    roles = load_roles(settings)
    assert roles["maintainer"].active
    assert not roles["engineer"].active
    assert (roles["maintainer"].model, roles["maintainer"].reasoning_effort) == (
        roles["engineer"].model, roles["engineer"].reasoning_effort)
    assert roles["maintainer"].can_delegate_to == []


def test_only_maintainer_receives_channel_messages_and_status_needs_no_model(improvements):
    company, credentials = improvements
    ingress = SlackIngress(company.settings, company, credentials)
    payload = {"team_id": "TTEST", "api_app_id": "AMAIN", "event_id": "E1", "event": {
        "type": "message", "user": "UHUMAN", "channel": "CIMPROVE", "ts": "3.1", "text": "이 오류를 확인해줘"}}
    first = ingress.accept("maintainer", payload, credentials["maintainer"])
    assert ingress.accept("maintainer", payload, credentials["maintainer"])["duplicate"]
    payload["api_app_id"] = "A0"
    assert ingress.accept("director", payload, credentials["director"])["ignored"]
    payload["api_app_id"] = "AMAIN"
    payload["event"].update(ts="3.2", thread_ts="3.1", text="상태")
    follow = ingress.accept("maintainer", payload, credentials["maintainer"])
    state = company.project_state(first["project_id"])
    assert first["project_id"] == follow["project_id"]
    assert len(state["turns"]) == 1  # only the initial conversation can use a model
    assert any("케이스 목록" in message["text"] for message in state["messages"])
    for changes in ({"user": "UOTHER"}, {"bot_id": "BECHO"}, {"channel": "CQUANT"}, {"channel": "DPRIVATE"}):
        rejected = copy.deepcopy(payload)
        rejected["event"].update(changes)
        assert ingress.accept("maintainer", rejected, credentials["maintainer"])["ignored"]
    payload["team_id"] = "TOTHER"
    with pytest.raises(PolicyError):
        ingress.accept("maintainer", payload, credentials["maintainer"])


async def test_cross_channel_receipted_thread_card_update_and_scoped_followup(improvements):
    company, credentials = improvements
    source, receipt, case = submit(company)
    assert receipt["destination"]["delivery"] == "pending"
    assert receipt["destination"]["thread_url"] is None
    box, calls = delivery(company, credentials)
    await drain(box)
    with company.db.transaction() as conn:
        target = company._project(conn, case["project_id"])
        assert target["thread_ts"] == "50.000001"
        assert target["id"] != source["project_id"]
        value = status(conn, company, target)
        assert value["requests"][0]["request_id"] == receipt["request_id"]
    Store(company, config()).save(receipt["request_id"], "blocked", error="synthetic_failure")
    await Reporter(company, config()).tick()
    await drain(box)
    assert sum(path.endswith("chat.update") for path, _ in calls) == 1
    update = next(body for path, body in calls if path.endswith("chat.update"))
    assert update["channel"] == "CIMPROVE" and update["ts"] == "50.000001"
    assert "synthetic_failure" in update["text"] and "<@UHUMAN>" not in update["text"]
    assert sum("<@UHUMAN>" in body["text"] for _, body in calls) == 1
    before = len(calls)
    await Reporter(company, config()).tick()
    await drain(box)
    assert len(calls) == before
    with company.db.transaction() as conn:
        source_messages = conn.execute("SELECT text FROM messages WHERE project_id=%s AND kind='maintenance'",
                                       (source["project_id"],)).fetchall()
        assert len(source_messages) == 1 and "50" in source_messages[0]["text"]


async def test_uncertain_root_is_not_replayed_or_followed_by_orphan_updates(improvements):
    company, credentials = improvements
    _, _, case = submit(company)
    box, calls = delivery(company, credentials, fail=True)
    await drain(box)
    await Reporter(company, config()).tick()
    await drain(box)
    assert len(calls) == 1
    with company.db.transaction() as conn:
        assert cases.destination(conn, case)["delivery"] == "uncertain"
        assert cases.destination(conn, case)["thread_url"] is None
        assert conn.execute("SELECT count(*) AS n FROM outbox WHERE update_ts IS NOT NULL").fetchone()["n"] == 0


async def test_uncertain_update_blocks_later_updates_and_revoked_case_stops_delivery(improvements):
    company, credentials = improvements
    _, receipt, case = submit(company, channel="CIMPROVE")
    box, calls = delivery(company, credentials)
    await drain(box)
    store = Store(company, config())
    store.save(receipt["request_id"], "triage")
    await Reporter(company, config()).tick()
    failing, failures = delivery(company, credentials, fail=True)
    await drain(failing)
    store.save(receipt["request_id"], "done", receipt={"reason": "No confirmed defect"})
    await Reporter(company, config()).tick()
    await drain(failing)
    assert len(failures) == 1
    with company.db.transaction() as conn:
        project = company._project(conn, case["source_project_id"])
        assert project["thread_ts"] == "2.1"  # human root was never edited/replaced
    _, _, new_case = submit(company, thread="8.1")
    company.settings.slack_allowed_channels.remove("CQUANT")
    await drain(box)
    with company.db.transaction() as conn:
        assert cases.destination(conn, new_case)["delivery"] == "stale"


async def test_pr_approval_only_after_notice_in_case_and_same_owner_revision(improvements):
    company, credentials = improvements
    source, receipt, case = submit(company)
    box, calls = delivery(company, credentials)
    await drain(box)
    repair_id = str(uuid4())
    with company.db.transaction() as conn:
        conn.execute("""INSERT INTO maintenance_jobs(id,kind,state,payload,receipt)
            VALUES (%s,'repair','pr_open',%s,%s)""", (repair_id,
            Jsonb({"owners": ["UHUMAN"], "observations": [{"project_id": source["project_id"]}]}),
            Jsonb({"head": "c" * 40, "pr": {"number": 31, "url": "https://github.com/example/pull/31"}})))
        conn.execute("UPDATE maintenance_jobs SET state='done',receipt=%s WHERE id=%s",
                     (Jsonb({"case_id": repair_id}), receipt["request_id"]))
    await Reporter(company, config()).tick()
    command = dict(text="PR 31 반영해", owner="UHUMAN", channel="CIMPROVE",
                   thread_ts="50.000001", event_key="approval-31", event_ts="60.1")
    assert accept_approval(company, **command) is None  # queued notice does not count
    await drain(box)
    assert accept_approval(company, **{**command, "owner": "UOTHER"}) is None
    assert accept_approval(company, **{**command, "event_ts": "1.1"}) is None
    assert accept_approval(company, **{**command, "channel": "CQUANT", "thread_ts": "2.1"}) is None
    with company.db.transaction() as conn:
        conn.execute("UPDATE maintenance_jobs SET receipt=jsonb_set(receipt,'{head}',%s) WHERE id=%s",
                     (Jsonb("d" * 40), repair_id))
    assert accept_approval(company, **command)["maintenance_approval"] == "announced_candidate_changed"
    with company.db.transaction() as conn:
        conn.execute("UPDATE maintenance_jobs SET receipt=jsonb_set(receipt,'{head}',%s) WHERE id=%s",
                     (Jsonb("c" * 40), repair_id))
    approved = accept_approval(company, **command)
    assert approved["maintenance_approval"] == "approved"
    assert accept_approval(company, **command)["duplicate"]
    with company.db.transaction() as conn:
        application = conn.execute("SELECT * FROM maintenance_applications").fetchone()
        assert application["head"] == "c" * 40 and application["project_id"] == case["project_id"]
        conn.execute("UPDATE projects SET revision=revision+1 WHERE id=%s", (source["project_id"],))
    assert accept_approval(company, **command)["maintenance_approval"] == "case_scope_changed"


def test_automatic_intake_only_new_failures_and_existing_jobs_not_moved(improvements):
    company, _ = improvements
    store = Store(company, config())
    assert store.collect() is None  # ordinary historical conversation is not work authorization
    result = company.ingest(event_key="observed-error", owner="UHUMAN", text="fixture",
                             channel="CQUANT", thread_ts="7.1")
    with company.db.transaction() as conn:
        conn.execute("UPDATE maintenance_control SET next_observe_at=now()")
        company._event(conn, "turn_blocked", {"reason": "model_invalid_output"}, result["project_id"])
    request_id = store.collect()
    assert request_id
    with company.db.transaction() as conn:
        case = conn.execute("SELECT * FROM maintenance_cases WHERE request_id=%s", (request_id,)).fetchone()
        assert case and str(case["source_project_id"]) == result["project_id"]
        assert conn.execute("SELECT count(*) AS n FROM maintenance_cases").fetchone()["n"] == 1


async def test_card_sender_requires_own_receipted_target_and_handles_missing_receipt(improvements):
    company, credentials = improvements
    _, receipt, case = submit(company, channel="CIMPROVE")
    box = SlackOutbox(company, credentials, httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True})))
    await drain(box)
    with company.db.transaction() as conn:
        assert cases.destination(conn, case)["delivery"] == "uncertain"
        # Operator reconciliation of a synthetic fixture, not an automatic retry.
        conn.execute("UPDATE outbox SET status='delivered',sent_ts='3.7' WHERE id=%s", (case["root_message_id"],))
    Store(company, config()).save(receipt["request_id"], "triage")
    await Reporter(company, config()).tick()
    with company.db.transaction() as conn:
        conn.execute("UPDATE outbox SET update_ts='9.9' WHERE update_ts IS NOT NULL")
    box, calls = delivery(company, credentials)
    await drain(box)
    assert calls == []
    with company.db.transaction() as conn:
        assert conn.execute("SELECT status FROM outbox WHERE update_ts='9.9'").fetchone()["status"] == "stale"


def test_repeated_submission_and_new_diagnosis_preserve_case_identity(improvements):
    company, _ = improvements
    source, first, case = submit(company, channel="CIMPROVE")
    with company.db.transaction() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id=%s", (source["task_id"],)).fetchone()
        duplicate = company._tool(conn, source["project_id"], ToolRequest(name="maintenance_review", arguments={}), task=task)
        assert duplicate["duplicate"] and duplicate["destination"]["case_id"] == str(case["id"])
    _, second, second_case = submit(company, channel="CIMPROVE", text="새 조건으로 별도의 진단을 요청합니다.")
    assert first["request_id"] != second["request_id"]
    assert case["project_id"] != second_case["project_id"]
    assert second["destination"]["thread_url"] is None


async def test_typed_conversation_to_existing_engine_and_final_case_result(improvements):
    company, credentials = improvements

    class Conversation:
        calls = 0

        async def run(self, request):
            self.calls += 1
            decision = (AgentDecision(say="기록된 오류의 진단을 접수합니다.", status="continue",
                                     tools=[{"name": "maintenance_review", "arguments": {}}])
                        if self.calls == 1 else AgentDecision(say="접수 기록을 확인했습니다. 완료 결과는 케이스에 보고합니다.",
                                                              status="complete"))
            return ProviderResponse(request_id=request.request_id, provider="fixture", decision=decision)

    request = company.ingest(event_key="typed-case", owner="UHUMAN", agent="maintainer",
                             text="이 오류의 원인을 진단해줘", channel="CIMPROVE", thread_ts="4.1")
    provider = Conversation()
    executor = TurnExecutor(company, provider)
    for _ in range(2):
        turn = queued_turns(company, request["project_id"])[0]
        assert (await executor.execute(turn))["state"] == "completed"
    assert provider.calls == 2
    maintenance = Maintainer(company, config(), github=GitHubFixture(), provider=ReviewModel())
    assert (await maintenance.tick())["state"] == "advanced"
    assert (await maintenance.tick())["state"] == "advanced"
    box, calls = delivery(company, credentials)
    await drain(box)
    await Reporter(company, config()).tick()
    await drain(box)
    with company.db.transaction() as conn:
        case = cases.lookup(conn, request["project_id"])
        assert cases.current(conn, case)["state"] == "done"
        assert conn.execute("SELECT count(*) AS n FROM maintenance_calls").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM maintenance_cases").fetchone()["n"] == 1
    assert any(path.endswith("chat.update") and "점검을 마쳤습니다" in body["text"] for path, body in calls)


async def test_real_temporal_reporting_runs_while_maintenance_is_busy_and_replays(improvements, tmp_path):
    company, credentials = improvements
    _, receipt, _ = submit(company)
    box, _ = delivery(company, credentials)
    await drain(box)
    started, finish = asyncio.Event(), asyncio.Event()

    @activity.defn(name="company_maintenance_tick")
    async def blocked_model():
        started.set()
        await finish.wait()

    cache = Path(".local/temporal")
    cache.mkdir(parents=True, exist_ok=True)
    async with await WorkflowEnvironment.start_local(download_dest_dir=str(cache.resolve()), ui=False) as env:
        queue = "case-test-" + uuid4().hex
        reporter = Reporter(company, config())
        async with (
            Worker(env.client, task_queue=queue, workflows=[MaintenanceWorkflow], activities=[blocked_model],
                   max_concurrent_activities=1),
            Worker(env.client, task_queue=queue + "-report", workflows=[MaintenanceReportingWorkflow],
                   activities=[reporter.tick], max_concurrent_activities=1),
        ):
            work = await env.client.start_workflow(MaintenanceWorkflow.run, 300, id=queue, task_queue=queue)
            await asyncio.wait_for(started.wait(), 10)
            Store(company, config()).save(receipt["request_id"], "blocked", error="synthetic_long_call")
            report = await env.client.start_workflow(MaintenanceReportingWorkflow.run, 60,
                                                     id=queue + "-report", task_queue=queue + "-report")
            async with asyncio.timeout(15):
                while True:
                    with company.db.transaction() as conn:
                        count = conn.execute("SELECT count(*) AS n FROM outbox WHERE update_ts IS NOT NULL").fetchone()["n"]
                    if count:
                        break
                    await asyncio.sleep(0.1)
            assert not finish.is_set()
            history = await report.fetch_history()
            await Replayer(workflows=[MaintenanceReportingWorkflow]).replay_workflow(history)
            finish.set()
            await work.cancel()
            await report.cancel()
        # A new company process reads the same case and queues no duplicate card.
        restarted = Company(company.settings, company.roles)
        await Reporter(restarted, config()).tick()
        with company.db.transaction() as conn:
            assert conn.execute("SELECT count(*) AS n FROM maintenance_cases").fetchone()["n"] == 1
            assert conn.execute("SELECT count(*) AS n FROM outbox WHERE update_ts IS NOT NULL").fetchone()["n"] == 1
