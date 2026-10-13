"""Real PostgreSQL admission tests; fixture responses, no live model or Slack."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.maintenance.store import Deferred

from .conftest import queued_turns
from .test_maintenance_requests import invoke, runner


def requested(company):
    maintenance = runner(company)
    invoke(company, "maintenance_review")
    maintenance.store.prepare_review(maintenance.store.next_job())
    return maintenance, maintenance.store.next_job()


def background(company):
    project = company.ingest(event_key=str(uuid4()), owner="UHUMAN", channel="CQUANT",
                             text="Fixture background request")
    with company.db.transaction() as conn:
        conn.execute("UPDATE tasks SET priority=100 WHERE id=%s", (project["task_id"],))
    return queued_turns(company, project["project_id"])[0]


def response(call):
    return ProviderResponse(request_id=call["id"], provider="fixture",
                            decision=AgentDecision(say="Fixture response", status="complete"))


@pytest.mark.integration
def test_owner_repair_reserves_behind_running_background_and_reuses_one_call(company):
    maintenance, job = requested(company)
    active = background(company)
    frozen = company.prepare_turn(active)["request"]
    call = maintenance.store.prepare_call(job, "triage", "Fixture maintenance admission")
    # A call already handed to the provider finishes with its original request.
    assert company.prepare_turn(active)["request"] == frozen
    again = maintenance.store.prepare_call(job, "triage", "This text must not replace saved input")
    assert again["id"] == call["id"] and again["request"] == call["request"]
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM maintenance_calls").fetchone()["n"] == 1
    next_background = background(company)
    assert company.prepare_turn(next_background) == {
        "state": "defer", "seconds": 2, "reason": "owner_maintenance_request"}
    maintenance.store.record_call(call["id"], response(call))
    assert company.prepare_turn(next_background)["state"] == "ready"


@pytest.mark.integration
def test_reserved_owner_call_yields_new_background_but_not_new_human_work(company):
    maintenance, job = requested(company)
    call = maintenance.store.prepare_call(job, "triage", "Fixture maintenance admission")
    work = background(company)
    assert company.prepare_turn(work)["reason"] == "owner_maintenance_request"
    human = company.ingest(event_key=str(uuid4()), owner="UHUMAN", channel="CQUANT", text="Human request")
    human_turn = queued_turns(company, human["project_id"])[0]
    assert company.prepare_turn(human_turn)["state"] == "ready"
    with pytest.raises(Deferred, match="company_work_has_priority"):
        maintenance.store.prepare_call(job, "triage", "Saved request remains authoritative")
    company.commit_turn(human_turn, ProviderResponse(
        request_id=human_turn, provider="fixture",
        decision=AgentDecision(say="Fixture human complete", status="complete")))
    assert maintenance.store.prepare_call(job, "triage", "Saved request")["id"] == call["id"]


@pytest.mark.integration
@pytest.mark.parametrize("guard", ["retry_due", "stale_heartbeat", "disabled", "owner_revoked", "revision_changed"])
def test_ineligible_owner_reservation_does_not_hold_background(company, guard):
    maintenance, job = requested(company)
    call = maintenance.store.prepare_call(job, "triage", "Fixture maintenance admission")
    if guard == "retry_due":
        maintenance.store.defer_call(call["id"], "busy", 120)
    elif guard in {"stale_heartbeat", "disabled", "owner_revoked"}:
        with company.db.transaction() as conn:
            runtime = conn.execute("SELECT runtime FROM maintenance_control WHERE id=1").fetchone()["runtime"]
            if guard == "stale_heartbeat":
                runtime["heartbeat_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
            elif guard == "disabled":
                runtime["enabled"] = False
            else:
                runtime["allowed_owners"] = []
            conn.execute("UPDATE maintenance_control SET runtime=%s WHERE id=1", (Jsonb(runtime),))
    else:
        with company.db.transaction() as conn:
            conn.execute("UPDATE projects SET revision=revision+1 WHERE id=%s",
                         (job["payload"]["request_project_id"],))
    assert company.prepare_turn(background(company))["state"] == "ready"


@pytest.mark.integration
def test_automatic_maintenance_still_yields_to_background(company):
    maintenance, job = requested(company)
    auto = {**job, "payload": {key: value for key, value in job["payload"].items()
                              if not key.startswith("request_")}}
    background(company)
    with pytest.raises(Deferred, match="company_work_has_priority"):
        maintenance.store.prepare_call(auto, "triage", "Fixture automatic maintenance")
    assert not maintenance.provider.requests

