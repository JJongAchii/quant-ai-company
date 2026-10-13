"""Real disposable PostgreSQL; simulated owner/model records; zero inference or Slack writes."""
import json
import os
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from quant_company.company import Company
from quant_company.config import Settings
from quant_company.contracts import AgentDecision, ProviderResponse, Role, ToolRequest
from quant_company.maintenance.policy import MaintenanceConfig
from quant_company.maintenance.store import Deferred, Store


def frozen(file, name, package):
    module = types.ModuleType(name)
    module.__package__ = package
    exec(compile(Path(file).read_bytes(), file, "exec"), module.__dict__)
    return module

old_company = frozen("/opt/baseline-company.py", "quant_company.frozen_company", "quant_company").Company
frozen_store = frozen("/opt/baseline-store.py", "quant_company.maintenance.frozen_store", "quant_company.maintenance")
old_store = frozen_store.Store
deferred_types = (Deferred, frozen_store.Deferred)
PG = os.environ["QUALIFICATION_PG"]

def fixture(company_type, store_type):
    name = "qual_" + uuid4().hex
    with psycopg.connect(f"postgresql://postgres@{PG}/postgres", autocommit=True) as admin:
        admin.execute("CREATE DATABASE " + name + " TEMPLATE template0")
    settings = Settings(database_url=f"postgresql://postgres@{PG}/{name}", model_provider="fixture",
                        fixture_mode=True, slack_team_id="TTEST",
                        slack_allowed_users=["UHUMAN"], slack_allowed_channels=["CQUANT"])
    roles = {name: Role(id=name, name=name, mission="Synthetic fixture", model="fixture-model",
                        instructions="Synthetic fixture only", tools=["calculate", "maintenance_review"],
                        can_delegate_to=[], active=True) for name in ["director", "engineer"]}
    company = company_type(settings, roles)
    company.db.migrate()
    config = MaintenanceConfig(app_id=1, installation_id=2, private_key_file=Path("unused-fixture-key"),
                               allowed_owners=["UHUMAN"], enabled=True)
    store = store_type(company, config)
    store.initialize()
    project = company.ingest(event_key=str(uuid4()), owner="UHUMAN", text="Fixture owner maintenance request",
                             channel="CQUANT", thread_ts="fixture")
    def next_turn():
        with company.db.transaction() as conn:
            return str(conn.execute("SELECT id FROM turns WHERE task_id=%s AND status='queued'",
                                    (project["task_id"],)).fetchone()["id"])
    turn = next_turn()
    company.prepare_turn(turn)
    company.commit_turn(turn, ProviderResponse(request_id=turn, provider="fixture", decision=AgentDecision(
        say="Fixture tool", status="continue", tools=[ToolRequest(name="maintenance_review", arguments={})])))
    turn = next_turn()
    company.prepare_turn(turn)
    company.commit_turn(turn, ProviderResponse(request_id=turn, provider="fixture", decision=AgentDecision(
        say="Fixture receipt", status="complete")))
    store.prepare_review(store.next_job())
    return company, store, store.next_job()

def background(company):
    project = company.ingest(event_key=str(uuid4()), owner="UHUMAN", channel="CQUANT", text="Fixture background")
    with company.db.transaction() as conn:
        conn.execute("UPDATE tasks SET priority=100 WHERE id=%s", (project["task_id"],))
        return str(conn.execute("SELECT id FROM turns WHERE task_id=%s", (project["task_id"],)).fetchone()["id"])

def exercise(company_type, store_type):
    company, store, job = fixture(company_type, store_type)
    active = background(company)
    frozen_request = company.prepare_turn(active)["request"]
    try:
        call = store.prepare_call(job, "triage", "Fixture owner admission")
    except deferred_types as error:
        behind = False
        behind_reason = str(error)
    else:
        behind = True
        behind_reason = None
        assert company.prepare_turn(active)["request"] == frozen_request
        assert store.prepare_call(job, "triage", "Cannot replace frozen request")["request"] == call["request"]
    company, store, job = fixture(company_type, store_type)
    call = store.prepare_call(job, "triage", "Fixture owner admission")
    work = background(company)
    yielded = company.prepare_turn(work).get("reason") == "owner_maintenance_request"
    if yielded:
        human = company.ingest(event_key=str(uuid4()), owner="UHUMAN", channel="CQUANT", text="Fixture human")
        with company.db.transaction() as conn:
            turn = str(conn.execute("SELECT id FROM turns WHERE task_id=%s", (human["task_id"],)).fetchone()["id"])
        assert company.prepare_turn(turn)["state"] == "ready"
        try:
            store.prepare_call(job, "triage", "Original request")
        except deferred_types as error:
            assert str(error) == "company_work_has_priority"
        else:
            raise AssertionError("Human request retains admission priority")
        company.commit_turn(turn, ProviderResponse(request_id=turn, provider="fixture",
                            decision=AgentDecision(say="Fixture complete", status="complete")))
        assert store.prepare_call(job, "triage", "Original request")["request"] == call["request"]
        store.record_call(call["id"], ProviderResponse(request_id=call["id"], provider="fixture",
                          decision=AgentDecision(say="Fixture complete", status="complete")))
        assert company.prepare_turn(work)["state"] == "ready"
    return {"reserves_behind_running_background":behind, "baseline_reason":behind_reason,
            "next_new_background_yields":yielded, "frozen_request_human_priority_and_release_verified":yielded and behind}

baseline = exercise(old_company, old_store)
assert not baseline["reserves_behind_running_background"] and not baseline["next_new_background_yields"]
candidate = exercise(Company, Store)
assert candidate["reserves_behind_running_background"] and candidate["next_new_background_yields"]
guards = {}
for guard in ["retry_due", "stale_heartbeat", "disabled", "owner_revoked", "revision_changed"]:
    company, store, job = fixture(Company, Store)
    call = store.prepare_call(job, "triage", "Fixture guarded admission")
    if guard == "retry_due":
        store.defer_call(call["id"], "busy", 120)
    elif guard == "revision_changed":
        with company.db.transaction() as conn:
            conn.execute("UPDATE projects SET revision=revision+1 WHERE id=%s",
                         (job["payload"]["request_project_id"],))
    else:
        with company.db.transaction() as conn:
            runtime = conn.execute("SELECT runtime FROM maintenance_control WHERE id=1").fetchone()["runtime"]
            if guard == "stale_heartbeat":
                runtime["heartbeat_at"] = (datetime.now(UTC)-timedelta(hours=1)).isoformat()
            elif guard == "disabled":
                runtime["enabled"] = False
            else:
                runtime["allowed_owners"] = []
            conn.execute("UPDATE maintenance_control SET runtime=%s WHERE id=1", (Jsonb(runtime),))
    guards[guard] = company.prepare_turn(background(company))["state"] == "ready"
assert all(guards.values())
print(json.dumps({"baseline":baseline, "candidate":candidate, "guards":guards,
    "models":"not invoked", "subscription_model_calls":0, "production_database_writes":0,
    "database":"real PostgreSQL in separate ephemeral container/network",
    "slack":"simulated records in disposable database only", "slack_writes":0}))
