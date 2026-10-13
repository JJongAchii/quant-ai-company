"""Real PostgreSQL; synthetic research/review records, no model or external writes."""

import json
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from quant_company.maintenance.policy import Triage
from quant_company.maintenance.store import Store
from quant_company.staff.store import StaffStore

from .test_maintenance import config, prepare


@pytest.fixture
def intake(company):
    source = prepare(company)
    company.settings.company_improvements_enabled = True
    company.settings.improvements_channel_id = "CQUANT"
    store = Store(company, config())
    store.initialize()
    return company, store, source


def stage_record(intake, error="invalid_output", *, state="waiting", program_state="active", attempt=2):
    company, _, source = intake
    stage_id, program_id = uuid4(), uuid4()
    with company.db.transaction() as conn:
        conn.execute("UPDATE tasks SET agent='data',kind='research_stage' WHERE id=%s", (source["task_id"],))
        conn.execute("""INSERT INTO research_programs
            (id,project_id,owner_user,revision,spec,manifest_digest,state,approval_event_id)
            VALUES (%s,%s,'UHUMAN',1,'{}',%s,%s,'maintenance-test')""",
            (program_id, source["project_id"], "a"*64, program_state))
        conn.execute("""INSERT INTO research_mission_stages
            (id,mission_id,program_id,stage_key,stage,actor,context,task_id,attempt,state,error)
            VALUES (%s,NULL,%s,'data','program_data','data','{}',%s,%s,%s,%s)""",
            (stage_id, program_id, source["task_id"], attempt, state, error))
    return stage_id


def review_record(intake, error="uncertain", *, owner="UHUMAN"):
    company, _, _ = intake
    if owner not in company.settings.slack_allowed_users:
        company.settings.slack_allowed_users.append(owner)
    run_id = StaffStore(company).enqueue("data", owner)
    review_id = "review-" + str(run_id)
    with company.db.transaction() as conn:
        conn.execute("""UPDATE staff_runs SET state='completed',final_answer='{}',
            grade=%s,completed_at=now() WHERE id=%s""", (Jsonb({"objective_passed": True}), run_id))
        conn.execute("""INSERT INTO staff_independent_reviews
            (id,run_id,state,model,rubric_version,source_digest,request,input_digest,schedule_day,error)
            VALUES (%s,%s,'blocked','claude-opus-5','fixture-rubric',%s,%s,%s,CURRENT_DATE,%s)""",
            (review_id, run_id, "b"*64, Jsonb({"request_id": review_id, "prompt": "Frozen synthetic request"}),
             "c"*64, error))
    return review_id


def finish(intake, identity):
    company, store, _ = intake
    with company.db.transaction() as conn:
        job = conn.execute("SELECT * FROM maintenance_jobs WHERE id=%s", (identity,)).fetchone()
    store.finish_triage(job, Triage(reason="Synthetic diagnostic complete; no external operation justified."))
    with company.db.transaction() as conn:
        conn.execute("UPDATE maintenance_control SET next_observe_at=now()")


def observations(intake, identity):
    with intake[0].db.transaction() as conn:
        return conn.execute("SELECT payload FROM maintenance_jobs WHERE id=%s", (identity,)).fetchone()["payload"]


def test_current_repeated_contract_failure_is_observed_once_without_changing_research(intake):
    stage_id = stage_record(intake, "1 validation error for DataAssessment\n Value error, wrong version "
                            "[type=value_error, input_value={'private_metric': 987.123}]")
    company, store, _ = intake
    identity = store.collect()
    payload = observations(intake, identity)
    fault = next(row for row in payload["observations"] if row["kind"] == "research_contract_failure")
    assert fault["detail"]["stage_id"] == str(stage_id) and fault["detail"]["attempt"] == 2
    assert "987.123" not in json.dumps(payload)
    finish(intake, identity)
    with company.db.transaction() as conn:
        assert conn.execute("SELECT state,error FROM research_mission_stages WHERE id=%s", (stage_id,)).fetchone()[
            "state"] == "waiting"
        conn.execute("UPDATE research_mission_stages SET updated_at=now() WHERE id=%s", (stage_id,))
    assert store.collect() is None


@pytest.mark.parametrize("updates", [
    {"error": "auth"}, {"error": "quota"}, {"error": "missing_data_evidence"},
    {"state": "completed"}, {"program_state": "cancelled"}, {"attempt": 1},
])
def test_normal_wait_resolved_cancelled_and_first_attempt_are_not_auto_repairs(intake, updates):
    stage_record(intake, **updates)
    assert intake[1].collect() is None


@pytest.mark.parametrize("stale", ["revision", "owner", "channel"])
def test_research_failure_requires_current_owner_revision_and_channel(intake, stale):
    stage_record(intake)
    company, store, source = intake
    with company.db.transaction() as conn:
        if stale == "revision":
            conn.execute("UPDATE projects SET revision=2 WHERE id=%s", (source["project_id"],))
        elif stale == "owner":
            conn.execute("UPDATE projects SET owner_user='UOTHER' WHERE id=%s", (source["project_id"],))
        else:
            conn.execute("UPDATE projects SET channel='COTHER' WHERE id=%s", (source["project_id"],))
    assert store.collect() is None


def test_paused_child_mission_is_not_observed_even_when_program_is_active(intake):
    stage_id = stage_record(intake)
    company, store, source = intake
    with company.db.transaction() as conn:
        program_id = conn.execute("SELECT program_id FROM research_mission_stages WHERE id=%s",
                                  (stage_id,)).fetchone()["program_id"]
        mission_id = uuid4()
        conn.execute("""INSERT INTO research_missions
            (id,project_id,owner_user,revision,spec,manifest_digest,state,program_id)
            VALUES (%s,%s,'UHUMAN',1,'{}',%s,'paused',%s)""",
            (mission_id, source["project_id"], "d"*64, program_id))
        conn.execute("UPDATE research_mission_stages SET mission_id=%s WHERE id=%s", (mission_id, stage_id))
    assert store.collect() is None


def test_uncertain_review_creates_diagnosis_once_and_preserves_original_request_and_grade(intake):
    review_id = review_record(intake)
    company, store, _ = intake
    with company.db.transaction() as conn:
        original = conn.execute("SELECT * FROM staff_independent_reviews WHERE id=%s", (review_id,)).fetchone()
    identity = store.collect()
    payload = observations(intake, identity)
    fault = next(row for row in payload["observations"] if row["kind"] == "staff_independent_review_failure")
    assert fault["detail"]["operator_reconciliation_required"] is True
    assert "do not replay" in fault["text"] and payload["owners"] == ["UHUMAN"]
    finish(intake, identity)
    assert store.collect() is None
    with company.db.transaction() as conn:
        after = conn.execute("SELECT * FROM staff_independent_reviews WHERE id=%s", (review_id,)).fetchone()
        assert after == original
        assert conn.execute("SELECT grade FROM staff_runs WHERE id=%s", (original["run_id"],)).fetchone()[
            "grade"] == {"objective_passed": True}
        assert conn.execute("SELECT count(*) AS n FROM maintenance_calls").fetchone()["n"] == 0


@pytest.mark.parametrize("error,owner", [("auth", "UHUMAN"), ("quota", "UHUMAN"), ("uncertain", "UOTHER")])
def test_review_backoff_and_other_owner_do_not_create_diagnosis(intake, error, owner):
    review_record(intake, error, owner=owner)
    assert intake[1].collect() is None
