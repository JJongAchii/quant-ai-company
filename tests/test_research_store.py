"""Real PostgreSQL; Slack/model messages below are explicit synthetic ingress fixtures."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from quant_company.api import create_app
from quant_company.company import Company, PolicyError
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.research.contracts import WorkerUpdate
from quant_company.research.store import ResearchStore, parse_command
from quant_company.slack import SlackIngress

from .conftest import queued_turns


def request(company, event="initial", thread="123.0"):
    project = company.ingest(event_key=event, owner="UHUMAN", text="Prepare fixed research replay",
                             channel="CQUANT", thread_ts=thread)
    turn = queued_turns(company, project["project_id"])[0]
    company.prepare_turn(turn)
    company.commit_turn(turn, ProviderResponse(request_id=turn, provider="fixture", decision=AgentDecision(
        say="명세를 준비합니다.", status="continue", tools=[{"name": "research_control", "arguments": {
            "action": "request", "recipe_id": "kr-etf-p11-replay-v1"}}])))
    with company.db.transaction() as conn:
        row = conn.execute("SELECT * FROM research_jobs WHERE project_id=%s", (project["project_id"],)).fetchone()
    return project, row


def owner(company, credentials, row, *, text=None, stamp="124.0", thread="123.0", user="UHUMAN"):
    text = text or f"연구 승인 {row['id']} {row['manifest_digest'][:12]}"
    return SlackIngress(company.settings, company, credentials).accept("director", {
        "team_id": "TTEST", "api_app_id": credentials["director"]["app_id"],
        "event": {"type": "message", "user": user, "channel": "CQUANT", "ts": stamp,
                  "thread_ts": thread, "text": text}}, credentials["director"])


def row_for(company, identity):
    with company.db.transaction() as conn:
        return conn.execute("SELECT * FROM research_jobs WHERE id=%s", (identity,)).fetchone()


def test_only_owner_exact_current_manifest_can_queue_and_duplicate_ack_is_idempotent(research, credentials):
    project, row = request(research)
    store = ResearchStore(research)
    assert store.poll() == {"assignment": None}
    assert owner(research, credentials, row, user="UOUTSIDE")["ignored"]
    with pytest.raises(PolicyError, match="current specification"):
        owner(research, credentials, row, text=f"연구 승인 {row['id']} {'0'*12}")
    with pytest.raises(PolicyError, match="authenticated Slack"):
        research.ingest(event_key="operator:spoof", owner="UHUMAN", project_id=project["project_id"],
                        text=f"연구 승인 {row['id']} {row['manifest_digest'][:12]}")
    first = owner(research, credentials, row)
    assert not first["duplicate"]
    assert owner(research, credentials, row)["duplicate"]
    saved = row_for(research, row["id"])
    assert saved["state"] == "queued" and saved["approved_by"] == "UHUMAN"
    assert saved["approval_event_id"] == "slack:TTEST:CQUANT:124.0:director"
    assert saved["revision"] == 1 and saved["manifest"]


def test_approval_is_thread_bound_and_revision_change_invalidates_it(research, credentials):
    _, row = request(research)
    request(research, "other", "other-thread")
    with pytest.raises(PolicyError, match="thread"):
        owner(research, credentials, row, thread="other-thread")
    research.ingest(event_key="revision", text="Change the task", owner="UHUMAN",
                    project_id=str(row["project_id"]), revise=True)
    with pytest.raises(PolicyError, match="current specification"):
        owner(research, credentials, row)
    assert row_for(research, row["id"])["state"] == "cancelled"


def test_request_retry_keeps_single_job_and_model_cannot_supply_shell_or_approval(research):
    project, row = request(research)
    with research.db.transaction() as conn:
        p = research._project(conn, project["project_id"])
        task = conn.execute("SELECT * FROM tasks WHERE id=%s", (project["task_id"],)).fetchone()
        again = ResearchStore(research).request(conn, p, task, row["recipe_id"])
        assert again["id"] == str(row["id"])
        for arguments in ({"action": "approve"}, {"action": "request", "recipe_id": row["recipe_id"],
                                                 "command": "touch /tmp/model-command"}):
            with pytest.raises(ValidationError):
                ResearchStore(research).tool(conn, p, task, arguments)
        assert conn.execute("SELECT count(*) AS n FROM research_jobs").fetchone()["n"] == 1


def test_lost_poll_response_and_server_restart_keep_the_same_assignment(research, credentials):
    _, row = request(research)
    owner(research, credentials, row)
    assigned = ResearchStore(research).poll()["assignment"]
    with research.db.transaction() as conn:
        conn.execute("UPDATE research_jobs SET claimed_at=now()-interval '30 days' WHERE id=%s", (row["id"],))
    restarted = Company(research.settings, research.roles)
    replay = ResearchStore(restarted).poll()["assignment"]
    assert assigned["action"] == "run" and replay["action"] == "run"
    assert {k: v for k, v in assigned.items() if k != "action"} == {k: v for k, v in replay.items() if k != "action"}
    ResearchStore(restarted).heartbeat(str(row["id"]), WorkerUpdate(
        lease_token=assigned["lease_token"], sequence=1, state="running", reason="preparing"))
    assert ResearchStore(restarted).poll()["assignment"]["action"] == "reconcile"
    with restarted.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM events WHERE kind='research_claimed'").fetchone()["n"] == 1


def test_two_pollers_and_multiple_projects_do_not_double_assign_worker(research, credentials):
    _, first = request(research)
    _, second = request(research, "second", "second-thread")
    owner(research, credentials, first)
    owner(research, credentials, second, thread="second-thread", stamp="125.0")
    with ThreadPoolExecutor(2) as pool:
        assignments = list(pool.map(lambda _: ResearchStore(research).poll()["assignment"], range(2)))
    assert {a["job_id"] for a in assignments} == {str(first["id"])}
    assert len({a["lease_token"] for a in assignments}) == 1


def test_offline_queue_waits_indefinitely_without_model_followups(research, credentials):
    project, row = request(research)
    owner(research, credentials, row)
    with research.db.transaction() as conn:
        conn.execute("UPDATE research_jobs SET approved_at=now()-interval '90 days'")
        state = ResearchStore(research).status(conn, project["project_id"])
        count = conn.execute("SELECT count(*) AS n FROM turns").fetchone()["n"]
    assert state["jobs"][0]["state"] == "queued"
    assert not state["jobs"][0]["worker_connected_recently"]
    assert not state["jobs"][0]["performance_visible"]
    assert count == 2  # The requesting model's tool-result turn, no timer/model poll tasks.


def test_revision_and_late_running_heartbeat_preserve_cancellation(research, credentials):
    _, row = request(research)
    owner(research, credentials, row)
    store = ResearchStore(research)
    assignment = store.poll()["assignment"]
    owner(research, credentials, row, text="중단", stamp="126.0")
    update = WorkerUpdate(lease_token=assignment["lease_token"], sequence=1, state="running")
    assert store.heartbeat(str(row["id"]), update)["state"] == "cancel_requested"
    assert store.poll()["assignment"]["action"] == "cancel"
    assert store.heartbeat(str(row["id"]), update)["duplicate"]
    with pytest.raises(PolicyError, match="sequence"):
        store.heartbeat(str(row["id"]), update.model_copy(update={"state": "failed"}))
    result = store.artifact_received(str(row["id"]), assignment["lease_token"], "/fixture/archive.zip", "a" * 64)
    assert result["state"] == "cancelled"
    assert row_for(research, row["id"])["report"] is None


def test_uncertain_worker_is_not_reassigned_and_raw_errors_are_not_in_status(research, credentials):
    _, row = request(research)
    owner(research, credentials, row)
    store = ResearchStore(research)
    assignment = store.poll()["assignment"]
    store.heartbeat(str(row["id"]), WorkerUpdate(lease_token=assignment["lease_token"], sequence=1,
                                                state="uncertain", reason="untrusted stdout 123 performance"))
    assert store.poll()["assignment"]["job_id"] == str(row["id"])
    with research.db.transaction() as conn:
        state = store.status(conn, row["project_id"])
    assert "123 performance" not in json.dumps(state)
    assert row_for(research, row["id"])["error"] == "worker_error"


def test_worker_auth_is_separate_and_returned_archive_has_durable_idempotent_identity(research, credentials):
    _, row = request(research)
    owner(research, credentials, row)
    app = TestClient(create_app(research.settings, research, credentials))
    path = "/v1/research/worker/poll"
    worker = {"Authorization": "Bearer " + research.settings.research_worker_token.get_secret_value()}
    operator = {"Authorization": "Bearer " + research.settings.require_operator_token()}
    assert app.post(path, headers=operator, json={"worker_id": "worker"}).status_code == 401
    assert app.get("/v1/projects", headers=worker).status_code == 401
    assert app.post(path, headers=worker, json={"worker_id": "worker5090"}).status_code == 422
    assignment = app.post(path, headers=worker, json={"worker_id": "worker"}).json()["assignment"]
    # Transport deliberately accepts an opaque archive; the separate audit stage withholds invalid ZIPs.
    data = b"synthetic invalid archive without any performance"
    sha = hashlib.sha256(data).hexdigest()
    headers = {**worker, "X-Research-Lease": assignment["lease_token"], "X-Artifact-Sha256": sha}
    endpoint = f"/v1/research/worker/jobs/{row['id']}/artifact"
    first = app.post(endpoint, headers=headers, content=data)
    assert first.status_code == 200 and first.json()["state"] == "received"
    assert app.post(endpoint, headers=headers, content=data).json()["duplicate"]
    assert app.post(endpoint, headers=headers, content=b"changed").status_code == 409
    saved = row_for(research, row["id"])
    assert saved["artifact_sha256"] == sha and saved["report"] is None
    assert not list(research.settings.research_artifact_dir.rglob("*.part"))


def test_commands_do_not_treat_quoted_or_generic_approval_as_authority():
    assert parse_command("응 진행해") is None
    assert parse_command('"연구 승인 00000000-0000-0000-0000-000000000000 aaaaaaaaaaaa"') is None
    assert parse_command("연구 상태") == {"action": "status"}
