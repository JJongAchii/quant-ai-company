"""Real PostgreSQL/Git lifecycle; synthetic staff and diagnostic responses."""

import hashlib
import json
from copy import deepcopy
from importlib.resources import files
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from quant_company.company import PolicyError
from quant_company.research.technical_review import retire_candidate

from .test_research_audit import qlab_profile  # noqa: F401
from .test_research_mission_backend import backend_fixture  # noqa: F401
from .test_research_repair import failed_trial


def request_review(h):
    job = failed_trial(h)
    failure = "research-failure:" + str(job["id"])
    diagnostic = {"job_id": str(job["id"]), "trial_id": str(job["trial_id"]),
                  "mission_id": str(job["mission_id"]), "code_commit": job["manifest"]["plan"]["code_commit"],
                  "code_files": job["manifest"]["code_files"], "evaluation_started": False,
                  "qualification": {"exit_code": 1}, "diagnostic": "Synthetic pre-evaluation failure"}
    h.company.put_source(source_id=failure, title="Synthetic failure", uri="fixture:failure",
                         content=json.dumps(diagnostic), available_at=job["heartbeat_at"],
                         project_id=h.project["project_id"], approved=True, synthetic=True)
    row, snapshot = h.schedule()
    hold = {"blocked": True, "rationale": "Synthetic callback incompatible with selected obligations",
            "required_evidence": ["Verified callback contract"], "repair_source_ids": [failure]}
    row = h.respond(row, hold)
    h.backend.apply_stage(row, snapshot)
    packet = {"mission_id": h.mission_id, "mission_digest": h.digest, "trial_id": str(job["trial_id"]),
              "job_id": str(job["id"]), "code_commit": job["manifest"]["plan"]["code_commit"],
              "finding": "Synthetic verified interface mismatch"}
    identity = "research-contract:" + str(job["trial_id"])
    content = json.dumps(packet)
    h.company.put_source(source_id=identity, title="Synthetic contract review", uri="fixture:contract",
                         content=content, available_at=job["heartbeat_at"],
                         project_id=h.project["project_id"], approved=True, synthetic=True)
    evidence = {"source_ids": [identity], "content_sha256": {identity: hashlib.sha256(content.encode()).hexdigest()}}
    with h.company.db.transaction() as conn:
        current = conn.execute("SELECT * FROM research_mission_stages WHERE id=%s", (row["id"],)).fetchone()
        context = {**current["context"], "_technical_review_evidence": evidence}
        conn.execute("UPDATE research_mission_stages SET context=%s WHERE id=%s", (Jsonb(context), row["id"]))
    value = {"trial_id": str(job["trial_id"]), "failed_job_id": str(job["id"]),
             "decision": "retire_candidate", "rationale": "Independent synthetic feasibility disposition",
             "source_ids": [failure, identity]}
    return row, hold, job, value


def retire(h, value, actor="director"):
    with h.company.db.transaction() as conn:
        return retire_candidate(h.store, conn, h.mission_id, value, actor=actor)


def test_independent_technical_retirement_preserves_history_and_counters_then_allows_selection(backend_fixture):  # noqa: F811
    h = backend_fixture
    held, hold, job, value = request_review(h)
    before = h.snapshot()
    assert before["stage"]["stage"] == "technical_review"
    closed = retire(h, value)
    assert closed["state"] == "technical_waiting" and closed["technical_closed_at"] is not None
    assert closed["result_id"] is None and closed["ordinal"] is None and closed["feasible"] is None
    assert retire(h, value) == closed
    after = h.snapshot()
    assert after["stage"]["stage"] == "proposal"
    for field in ("cycle", "cycle_trials", "cumulative_trials", "stagnant_trials", "incumbent_trial_id"):
        assert after[field] == before[field]
    assert after["outcomes"] == before["outcomes"]
    assert after["publications"] == before["publications"] == []
    assert h.job()["state"] == "failed"
    with pytest.raises(PolicyError, match="cannot be repaired or requeued"):
        h.invoke("set_plan", h.mission_id, closed["id"], closed["plan"], actor="engineer",
                 repair_source_ids=value["source_ids"], repair_rationale="Synthetic stale retry")
    with h.company.db.transaction() as conn:
        row = conn.execute("SELECT * FROM research_mission_stages WHERE id=%s", (held["id"],)).fetchone()
        assert row["result"] == row["context"]["_repair_hold"] == hold
        assert row["attempt"] == held["attempt"] and row["retry_at"] is None
        assert conn.execute("SELECT count(*) AS n FROM research_jobs").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM research_technical_dispositions").fetchone()["n"] == 1
    trial_id, _ = h.select()
    assert trial_id != str(job["trial_id"])
    assert next(t for t in h.snapshot()["trials"] if t["id"] == trial_id)["state"] == "selected"
    public = h.snapshot(public=True)
    failed = next(t for t in public["trials"] if t["id"] == str(job["trial_id"]))
    assert failed["state"] == "technical_closed" and failed["performance_visible"] is False


@pytest.mark.parametrize("case", ["wrong_actor", "wrong_job", "wrong_trial", "missing_source", "wrong_evidence",
                                  "changed_evidence", "economic_started", "archive", "uncertain", "stale_revision"])
def test_unverified_or_unauthorized_retirement_is_rejected_without_releasing_pending_trial(backend_fixture, case):  # noqa: F811
    h = backend_fixture
    _, _, job, value = request_review(h)
    actor = "engineer" if case == "wrong_actor" else "director"
    with h.company.db.transaction() as conn:
        if case == "wrong_job":
            value["failed_job_id"] = str(uuid4())
        elif case == "wrong_trial":
            value["trial_id"] = str(uuid4())
        elif case == "missing_source":
            value["source_ids"] = value["source_ids"][:1]
        elif case in {"wrong_evidence", "changed_evidence"}:
            identity = value["source_ids"][1]
            source = conn.execute("SELECT content FROM sources WHERE id=%s", (identity,)).fetchone()
            packet = json.loads(source["content"])
            packet["job_id"] = str(uuid4())
            content = json.dumps(packet)
            conn.execute("UPDATE sources SET content=%s WHERE id=%s", (content, identity))
            if case == "wrong_evidence":
                held = conn.execute("SELECT * FROM research_mission_stages WHERE stage='repair'").fetchone()
                context = deepcopy(held["context"])
                context["_technical_review_evidence"]["content_sha256"][identity] = hashlib.sha256(content.encode()).hexdigest()
                conn.execute("UPDATE research_mission_stages SET context=%s WHERE id=%s", (Jsonb(context), held["id"]))
        elif case == "economic_started":
            source = conn.execute("SELECT content FROM sources WHERE id=%s", (value["source_ids"][0],)).fetchone()
            packet = json.loads(source["content"])
            packet["evaluation_started"] = True
            conn.execute("UPDATE sources SET content=%s WHERE id=%s", (json.dumps(packet), value["source_ids"][0]))
        elif case == "archive":
            conn.execute("UPDATE research_jobs SET artifact_sha256=%s WHERE id=%s", ("f" * 64, job["id"]))
        elif case == "uncertain":
            conn.execute("UPDATE research_jobs SET state='uncertain' WHERE id=%s", (job["id"],))
        elif case == "stale_revision":
            conn.execute("UPDATE projects SET revision=revision+1 WHERE id=%s", (h.project["project_id"],))
    with pytest.raises(PolicyError):
        retire(h, value, actor)
    with h.company.db.transaction() as conn:
        assert conn.execute("SELECT state FROM research_mission_trials WHERE id=%s", (job["trial_id"],)).fetchone()["state"] == "technical_waiting"
        assert conn.execute("SELECT count(*) AS n FROM research_technical_dispositions").fetchone()["n"] == 0


def test_current_stage_prompts_director_and_wait_stops_after_one_bound_review(backend_fixture):  # noqa: F811
    h = backend_fixture
    held, hold, _, value = request_review(h)
    row, snapshot = h.schedule()
    assert row["actor"] == "director" and row["stage"] == "technical_review"
    value["decision"] = "wait"
    with pytest.raises(PolicyError, match="Independent technical review"):
        h.respond(row, value)
    for path in row["context"]["required_technical_reads"]:
        h.respond(row, read_path=path)
    received = h.respond(row, value)
    with h.company.db.transaction() as conn:
        h.controller._apply(conn, received, snapshot)
    for _ in range(3):
        assert h.controller.tick() == {"state": "waiting", "stage": "technical_review"}
    assert h.stage_row()["attempt"] == 1
    with h.company.db.transaction() as conn:
        assert conn.execute("SELECT result FROM research_mission_stages WHERE id=%s", (held["id"],)).fetchone()["result"] == hold
        assert conn.execute("SELECT count(*) AS n FROM research_technical_dispositions").fetchone()["n"] == 0


def test_typed_director_response_retires_then_schedules_normal_researcher_pipeline(backend_fixture):  # noqa: F811
    h = backend_fixture
    _, _, _, value = request_review(h)
    row, _ = h.schedule()
    for path in row["context"]["required_technical_reads"]:
        h.respond(row, read_path=path)
    for entry in row["context"]["evidence_sources"]:
        if entry["source_id"] in value["source_ids"]:
            h.respond(row, read_path=entry["file"])
    received = h.respond(row, value)
    assert received["state"] == "received"
    assert h.controller.tick() == {"state": "completed"}
    snapshot = h.snapshot()
    assert snapshot["trials"][0]["technical_closed_at"] is not None
    assert snapshot["stage"]["stage"] == "proposal"
    assert h.controller.tick()["state"] == "running"
    current = h.stage_row()
    assert current["stage"] == "proposal" and current["actor"] == "researcher_kr"
    assert current["attempt"] == 1
    assert snapshot["cumulative_trials"] == 0
    with h.company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM research_jobs").fetchone()["n"] == 1
        assert conn.execute("SELECT state FROM research_mission_stages WHERE id=%s", (row["id"],)).fetchone()["state"] == "completed"


def test_old_pending_index_is_migrated_idempotently_before_retirement(backend_fixture):  # noqa: F811
    h = backend_fixture
    _, _, _, value = request_review(h)
    with h.company.db.transaction() as conn:
        conn.execute("ALTER TABLE research_mission_trials DROP COLUMN technical_closed_at CASCADE")
        conn.execute("""CREATE UNIQUE INDEX research_mission_one_pending_trial ON research_mission_trials(mission_id)
            WHERE state <> 'reported'""")
        migration = files("quant_company.research").joinpath("mission_schema.sql").read_text()
        conn.execute(migration)
        conn.execute(migration)
    retire(h, value)
    trial_id, _ = h.select()
    assert next(t for t in h.snapshot()["trials"] if t["id"] == trial_id)["state"] == "selected"


def test_previous_reader_preserves_physical_failure_and_fails_closed_after_new_disposition(backend_fixture):  # noqa: F811
    import subprocess

    h = backend_fixture
    _, _, _, value = request_review(h)
    retire(h, value)
    original = subprocess.check_output([
        "git", "show", "a235e087d5e3adec85d5bad898c2bec8adfdae39:src/quant_company/research/missions.py"], text=True)
    namespace = {"__name__": "quant_company.research.previous_missions", "__package__": "quant_company.research"}
    exec(compile(original, "previous_missions.py", "exec"), namespace)
    store = namespace["MissionStore"](h.company)
    with h.company.db.transaction() as conn:
        old = store.snapshot(conn, h.mission_id, public=True)
    assert old["stage"] == "repair"
    assert old["trials"][0]["state"] == "technical_waiting"
    assert old["trials"][0]["performance_visible"] is False
    assert old["cumulative_trials"] == 0
