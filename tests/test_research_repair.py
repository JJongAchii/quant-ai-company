"""Real PostgreSQL/Git repair lifecycle; scripted staff, no scientific execution."""

import json

import pytest

from quant_company.company import PolicyError
from quant_company.research.builds import read_stage_file
from quant_company.research.contracts import WorkerUpdate
from quant_company.research.store import ResearchStore

from .test_research_audit import qlab_profile  # noqa: F401
from .test_research_mission_backend import backend_fixture  # noqa: F401


def failed_trial(h):
    h.build()
    assignment = ResearchStore(h.company).poll()["assignment"]
    ResearchStore(h.company).heartbeat(assignment["job_id"], WorkerUpdate(
        lease_token=assignment["lease_token"], sequence=1, state="failed", reason="qualification_failed"))
    assert h.backend.reconcile()["state"] == "technical_waiting"
    return h.job()


def register_diagnostic(h, job, *, wrong_trial=False):
    identity = "research-failure:" + str(job["id"])
    content = {"job_id": str(job["id"]), "trial_id": str(job["trial_id"]),
               "mission_id": str(job["mission_id"]), "code_commit": job["manifest"]["plan"]["code_commit"],
               "code_files": job["manifest"]["code_files"], "diagnostic": "Synthetic qualification failure"}
    if wrong_trial:
        content["trial_id"] = "00000000-0000-0000-0000-000000000000"
    h.company.put_source(source_id=identity, title="Synthetic failure receipt", uri="fixture:failure",
                         content=json.dumps(content), available_at=job["heartbeat_at"],
                         project_id=h.project["project_id"], approved=True, synthetic=True)
    return identity


def test_repair_prompt_distinguishes_failed_code_from_patch_preimages_and_exact_schema(backend_fixture):  # noqa: F811
    h = backend_fixture
    job = failed_trial(h)
    identity = register_diagnostic(h, job)
    row, _ = h.schedule()
    with h.company.db.transaction() as conn:
        turn = conn.execute("SELECT id FROM turns WHERE task_id=%s AND status='queued'", (row["task_id"],)).fetchone()
    request = h.company.prepare_turn(str(turn["id"]))["request"]
    context = json.loads(request["prompt"].split("MISSION DATA JSON:\n", 1)[1])
    failed = context["failed_build"]
    assert failed["job_id"] == str(job["id"])
    assert failed["code_commit"] == job["manifest"]["plan"]["code_commit"]
    assert failed["failure_source_ids"] == [identity]
    assert "failed-code/candidate.py" in failed["resident_evidence_paths"]
    assert "not patch preimages" in request["prompt"]
    assert "never evidence file paths" in request["prompt"]
    schema = context["output_schema"]["$defs"]
    assert set(schema["RepairArtifact"]["required"]) == {"patches", "rationale", "repair_source_ids"}
    assert set(schema["RepairHold"]["required"]) == {"blocked", "rationale", "required_evidence", "repair_source_ids"}
    assert not schema["RepairArtifact"]["additionalProperties"]
    assert schema["RepairHold"]["properties"]["blocked"]["const"] is True
    base = read_stage_file(h.company, row, {"action": "read_stage_file", "path": "code/candidate.py"})
    actual = read_stage_file(h.company, row, {"action": "read_stage_file", "path": "failed-code/candidate.py"})
    assert base["content"] == "SYNTHETIC_FIXTURE = True\n"
    assert actual["content"] == "SYNTHETIC_FIXTURE = False\n"
    assert actual["sha256"] == job["manifest"]["code_files"]["candidate.py"]


@pytest.mark.parametrize("mutation", ["candidate", "diagnostic"])
def test_changed_failed_build_or_cross_trial_diagnostic_cannot_reach_staff(backend_fixture, mutation):  # noqa: F811
    h = backend_fixture
    job = failed_trial(h)
    if mutation == "candidate":
        from pathlib import Path

        with h.company.db.transaction() as conn:
            records = h.backend._build_records(conn, h.mission_id)
        bundle = Path(records[0]["result"]["build_receipt"]["bundle_path"])
        (bundle.parent / "code/candidate.py").write_text("SYNTHETIC_CHANGED = True\n")
        error = "mission_repair_failed_code_changed"
    else:
        register_diagnostic(h, job, wrong_trial=True)
        error = "mission_repair_failure_evidence_identity_mismatch"
    with pytest.raises(PolicyError, match=error):
        h.schedule()


def test_independent_repair_hold_preserves_failure_and_stops_unchanged_auto_attempts(backend_fixture):  # noqa: F811
    h = backend_fixture
    failed_trial(h)
    before = h.snapshot()
    row, snapshot = h.schedule()
    response = {"blocked": True, "rationale": "Synthetic external evidence is still unavailable",
                "required_evidence": ["Verified diagnostic for the registered failed trial"],
                "repair_source_ids": ["fixture:baseline"]}
    received = h.respond(row, response)
    assert h.backend.apply_stage(received, snapshot) == {"state": "waiting", "requires_evidence": True}
    held = h.stage_row()
    assert held["result"] == response
    assert held["retry_at"] is None and held["context"]["_repair_hold"] == response
    for _ in range(3):
        assert h.controller.tick() == {"state": "waiting", "stage": "repair"}
    assert h.stage_row()["attempt"] == held["attempt"]
    assert h.snapshot()["outcomes"] == before["outcomes"]
    assert h.snapshot()["cumulative_trials"] == before["cumulative_trials"] == 0
    assert h.job()["state"] == "failed"
    with h.company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS count FROM research_jobs").fetchone()["count"] == 1
        assert conn.execute("SELECT count(*) AS count FROM research_stage_attempts WHERE stage_id=%s",
                            (held["id"],)).fetchone()["count"] == 1
