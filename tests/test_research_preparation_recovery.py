"""Captured precheck failure shape; real disposable PostgreSQL; no economic replay."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from deploy import research_preparation_recovery as recovery
from quant_company.research.contracts import Assignment, WorkerUpdate
from quant_company.research.recipes import load_recipe, recipe_digest
from quant_company.research.store import ResearchStore
from quant_company.research.worker import (
    Worker,
    WorkerConfig,
    atomic_json,
    canonical_sha,
    read_json,
)

from .test_research_store import owner, row_for
from .test_research_store import request as request_job

# Actual sanitized production result from production-failure-preflight.json.
# The private lease, filesystem, process ID and PostgreSQL records below are fixtures.
CAPTURED_FAILURE = {
    "completed_at": "2026-09-21T05:58:41.868954+00:00",
    "launch_id": "942d7e7c58694b8eb85172d5be20decf",
    "reason": "repository-commit-mismatch",
    "state": "failed",
}
OLD_PIN = "9a90b5eeff1f7f3685099b0c05241df1306d93fa"
ACTOR = "operator:fixture"
ROOT = Path(__file__).resolve().parents[1]
TOOL_COMMIT = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()


@pytest.fixture
def local_failure(tmp_path, monkeypatch):
    repo = tmp_path / "clean-company"
    repo.mkdir()
    (repo / "fixture.txt").write_text("No research code or data is executed by this fixture.\n")
    # Release preflight now also requires the two committed executor entrypoints.
    # The captured-failure test still injects only its labelled non-performance child.
    for name in ("worker.py", "executor.py"):
        relative = Path("src/quant_company/research") / name
        destination = repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / relative).read_bytes())
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                    "-c", "core.hooksPath=/dev/null", "commit", "-qm", "clean release fixture"], check=True)
    commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    config = WorkerConfig(
        api_url="http://127.0.0.1:18764", token_file=tmp_path / "token", state_dir=tmp_path / "state",
        repo_source=tmp_path / "pilot", input_source=tmp_path / "inputs", evidence_repo=tmp_path / "audit-evidence",
        research_python=Path(sys.executable), company_repo=repo, company_commit=commit,
    )
    config.state_dir.mkdir()
    (config.state_dir / "jobs").mkdir()
    monkeypatch.setattr(recovery.platform, "node", lambda: load_recipe().hostname)

    def create(assignment=None, approved_by="UHUMAN"):
        recipe = load_recipe()
        assignment = assignment or Assignment(
            job_id=uuid4(), project_id=uuid4(), revision=5, recipe_id=recipe.id,
            manifest_digest=recipe_digest(recipe), approval_event_id="slack:TTEST:CQUANT:124.0:director",
            lease_token="c" * 64, action="run",
        )
        job = config.state_dir / "jobs" / str(assignment.job_id)
        job.mkdir()
        update = WorkerUpdate(lease_token=assignment.lease_token, sequence=3, state="failed",
                              reason=CAPTURED_FAILURE["reason"])
        atomic_json(job / "state.json", {
            "assignment": assignment.model_dump(mode="json"), "sequence": 3,
            "last_update": update.model_dump(mode="json"), "last_update_at": 1789970327.8,
            "last_ack": {"ok": True, "state": "failed", "duplicate": False},
        })
        atomic_json(job / "launch-intent.json", {
            "assignment": assignment.model_dump(mode="json"), "company_commit": OLD_PIN,
            "launch_id": CAPTURED_FAILURE["launch_id"], "phase": "spawning",
            "created_at": "2026-09-21T05:58:41+00:00",
        })
        atomic_json(job / "execution-config.json", config.model_copy(update={"company_commit": OLD_PIN}).model_dump(
            mode="json"))
        atomic_json(job / "recipe.json", recipe.model_dump(mode="json"))
        atomic_json(job / "process.json", {
            "company_commit": OLD_PIN, "launch_id": CAPTURED_FAILURE["launch_id"],
            "started_at": "2026-09-21T05:58:41.809854+00:00",
            "identity": {"pid": 99999999, "pgid": 99999999, "boot": "fixture-terminated-boot",
                         "command": "a" * 64, "start": "66330570"},
        })
        atomic_json(job / "result.json", CAPTURED_FAILURE)
        (job / "executor.log").write_bytes(b"")
        (job / "execution.lock").write_bytes(b"")
        expected = recovery.RecoveryRequest(
            **assignment.model_dump(exclude={"action", "lease_token"}), approved_by=approved_by,
            sequence=3, failed_launch_id=CAPTURED_FAILURE["launch_id"], failed_company_commit=OLD_PIN,
            company_commit=config.company_commit, failure_files=recovery.tree_files(job),
        )
        return config, expected, job

    return create


def prepare(config, expected, identity=None):
    return recovery.prepare_local(config, expected, identity or uuid4(), actor=ACTOR, tool_commit=TOOL_COMMIT)


def test_local_preserves_failure_and_prepares_same_lease_sequence(local_failure):
    config, expected, job = local_failure()
    old_state = read_json(job / "state.json")
    result = prepare(config, expected)
    proof = recovery.RecoveryProof.model_validate(result["proof"])
    assert recovery.tree_files(config.state_dir / proof.history_path) == expected.failure_files
    assert read_json(config.state_dir / proof.history_path / "result.json") == CAPTURED_FAILURE
    assert read_json(job / "state.json") == old_state
    new_intent = read_json(job / "launch-intent.json")
    assert new_intent["assignment"] == old_state["assignment"]
    assert new_intent["phase"] == "prepared" and new_intent["launch_id"] != expected.failed_launch_id
    assert new_intent["company_commit"] == config.company_commit
    assert set(recovery.tree_files(job)) == recovery.PREPARED_FILES
    assert old_state["assignment"]["lease_token"] not in json.dumps(result)
    assert proof.launch_attempt_failures == 1 and proof.economic_executions_before_recovery == 0
    assert proof.scientific_trials_added == 0
    again = prepare(config, expected, proof.recovery_id)
    assert again["duplicate"] and again["proof"] == result["proof"]
    with pytest.raises(recovery.RecoveryBlocked, match="job-already-has-recovery"):
        prepare(config, expected)


@pytest.mark.parametrize("name", ["clone.command.json", "original-audit-verification.command.json", "artifact.zip",
                                  "objective.json", "stop.json", "unknown.tmp"])
def test_any_additional_trace_blocks(local_failure, name):
    config, expected, job = local_failure()
    (job / name).write_text("fixture")
    with pytest.raises(recovery.RecoveryBlocked, match="execution-trace-or-unknown-file"):
        prepare(config, expected)
    assert read_json(job / "result.json") == CAPTURED_FAILURE


@pytest.mark.parametrize("change", [
    {"sequence": 4}, {"manifest_digest": "d" * 64}, {"approval_event_id": "slack:TOTHER:CQUANT:999.0:director"},
    {"revision": 6}, {"company_commit": "d" * 40}, {"failed_company_commit": "f" * 40},
])
def test_mismatched_expected_identity_blocks(local_failure, change):
    config, expected, _ = local_failure()
    with pytest.raises(recovery.RecoveryBlocked):
        prepare(config, expected.model_copy(update=change))


def test_symlink_and_directory_block(local_failure, tmp_path):
    config, expected, job = local_failure()
    outside = tmp_path / "outside"
    outside.write_bytes(b"")
    (job / "executor.log").unlink()
    (job / "executor.log").symlink_to(outside)
    with pytest.raises(recovery.RecoveryBlocked, match="symlink-in-attempt"):
        prepare(config, expected)
    (job / "executor.log").unlink()
    (job / "executor.log").write_bytes(b"")
    (job / "checkout").mkdir()
    with pytest.raises(recovery.RecoveryBlocked, match="execution-trace-or-unknown-directory"):
        prepare(config, expected)


def test_live_executor_or_surviving_group_blocks(local_failure, monkeypatch):
    config, expected, job = local_failure()
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], start_new_session=True)
    try:
        record = read_json(job / "process.json")
        record["identity"] = recovery.process_identity(child.pid)
        atomic_json(job / "process.json", record)
        expected = expected.model_copy(update={"failure_files": recovery.tree_files(job)})
        with pytest.raises(recovery.RecoveryBlocked, match="executor-still-present"):
            prepare(config, expected)
        monkeypatch.setattr(recovery, "process_identity", lambda pid: None)
        with pytest.raises(recovery.RecoveryBlocked, match="executor-or-group-still-present"):
            prepare(config, expected)
    finally:
        child.kill()
        child.wait(timeout=5)


def test_active_polling_lock_blocks(local_failure):
    config, expected, _ = local_failure()
    with recovery.exclusive_lock(config.state_dir / "worker.lock"), pytest.raises(BlockingIOError):
        prepare(config, expected)
    assert not (config.state_dir / "preparation-recoveries").exists()


def test_interrupted_install_preserves_failure_and_blocks_reinvocation(local_failure, monkeypatch):
    config, expected, job = local_failure()
    identity = uuid4()
    original_rename = os.rename

    def interrupted(source, destination):
        if Path(source).name == "prepared":
            raise OSError("fixture crash after preserving original directory")
        original_rename(source, destination)

    monkeypatch.setattr(os, "rename", interrupted)
    with pytest.raises(OSError):
        prepare(config, expected, identity)
    journal_dir = config.state_dir / "preparation-recoveries" / str(identity)
    assert recovery.tree_files(journal_dir / "original") == expected.failure_files
    assert read_json(journal_dir / "journal.json")["stage"] == "installing"
    assert not (journal_dir / "proof.json").exists(), "incomplete preparation cannot issue server proof"
    assert not job.exists()
    with pytest.raises(recovery.RecoveryBlocked, match="incomplete-recovery-journal"):
        prepare(config, expected, identity)


@pytest.fixture
def failed_database(research, credentials, local_failure):
    # Approval is through the repository's synthetic authenticated Slack fixture;
    # only PostgreSQL is real. No live owner event is generated by this test.
    config, _, disposable_job = local_failure()
    import shutil
    shutil.rmtree(disposable_job)
    research.settings.company_code_commit = config.company_commit
    _, row = request_job(research)
    owner(research, credentials, row)
    store = ResearchStore(research)
    assignment = Assignment.model_validate(store.poll()["assignment"])
    store.heartbeat(row["id"], WorkerUpdate(lease_token=assignment.lease_token, sequence=1,
                                          state="running", reason="preparing"))
    store.heartbeat(row["id"], WorkerUpdate(lease_token=assignment.lease_token, sequence=2, state="running"))
    store.heartbeat(row["id"], WorkerUpdate(lease_token=assignment.lease_token, sequence=3,
                                          state="failed", reason=CAPTURED_FAILURE["reason"]))
    config, expected, job = local_failure(assignment)
    prepared = prepare(config, expected)
    proof = recovery.RecoveryProof.model_validate(prepared["proof"])
    return research, store, config, expected, job, proof, prepared["proof_digest"]


def resume(fixture):
    company, _, _, _, _, proof, digest = fixture
    return recovery.resume_server(company, proof, digest, actor=ACTOR, tool_commit=TOOL_COMMIT)


def test_real_postgres_recovery_and_duplicate_after_progress_do_not_rearm(failed_database):
    company, store, _, expected, _, proof, digest = failed_database
    before = row_for(company, expected.job_id)
    assert resume(failed_database) == {
        "ok": True, "state": "claimed", "duplicate": False, "recovery_id": str(proof.recovery_id),
    }
    after = row_for(company, expected.job_id)
    for key in ("lease_token", "approval_event_id", "approved_by", "approved_at", "sequence", "update_digest", "error"):
        assert after[key] == before[key]
    assignment = store.poll()["assignment"]
    assert assignment["action"] == "reconcile" and assignment["lease_token"] == before["lease_token"]
    store.heartbeat(expected.job_id, WorkerUpdate(lease_token=before["lease_token"], sequence=4,
                                                 state="running", reason="preparing"))
    assert resume(failed_database)["duplicate"]
    final = row_for(company, expected.job_id)
    assert final["state"] == "running" and final["sequence"] == 4
    with company.db.transaction() as conn:
        rows = conn.execute("SELECT detail FROM events WHERE kind='research_preparation_recovered'").fetchall()
    assert len(rows) == 1 and rows[0]["detail"]["proof_digest"] == digest
    assert rows[0]["detail"]["failure_digest"] == canonical_sha(expected.failure_files)
    assert before["lease_token"] not in json.dumps(rows[0]["detail"])


@pytest.mark.parametrize("field,value", [
    ("revision", 99), ("approval_event_id", "slack:TOTHER:CQUANT:999.0:director"),
    ("approved_by", "UOTHER"), ("lease_token", "d" * 64), ("sequence", 4),
    ("manifest_digest", "d" * 64), ("company_commit", "d" * 40), ("state", "cancelled"),
    ("artifact_sha256", "d" * 64),
])
def test_real_postgres_changed_row_is_blocked(failed_database, field, value):
    from psycopg import sql
    company, _, _, expected, _, _, _ = failed_database
    with company.db.transaction() as conn:
        conn.execute(sql.SQL("UPDATE research_jobs SET {}=%s WHERE id=%s").format(sql.Identifier(field)),
                     (value, expected.job_id))
    with pytest.raises(recovery.RecoveryBlocked):
        resume(failed_database)
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM events WHERE kind='research_preparation_recovered'").fetchone()["n"] == 0


def test_real_postgres_new_owner_revision_is_blocked(failed_database):
    company, _, _, expected, _, _, _ = failed_database
    with company.db.transaction() as conn:
        conn.execute("UPDATE projects SET revision=revision+1 WHERE id=%s", (expected.project_id,))
    with pytest.raises(recovery.RecoveryBlocked, match="project-revision"):
        resume(failed_database)


def test_captured_failure_shape_roundtrip(failed_database, monkeypatch):
    """Qualification: captured failure→typed proof→real DB→unchanged worker→child receipt."""
    company, store, config, expected, job, proof, digest = failed_database
    assert proof.failure.model_dump(mode="json")["reason"] == CAPTURED_FAILURE["reason"]
    resume(failed_database)
    operations = []
    original_popen = subprocess.Popen
    script = r'''
import sys
from pathlib import Path
from quant_company.research.executor import execute_child
from quant_company.research.worker import atomic_json, sha_file

def operation(config, assignment, recipe, job, started):
    atomic_json(job / 'fixture-operation.json', {'mode':'non-performance-fixture', 'sequence_at_prepare':3})
    (job / 'artifact.zip').write_bytes(b'NON-PERFORMANCE ENGINEERING FIXTURE')
    return sha_file(job / 'artifact.zip')
raise SystemExit(execute_child(Path(sys.argv[1]), sys.argv[2], operation=operation))
'''

    def spawn(command, *args, **kwargs):
        if isinstance(command, list) and "quant_company.research.executor" in command:
            directory = command[command.index("--job-dir") + 1]
            launch = command[command.index("--launch-id") + 1]
            kwargs["env"] = {**kwargs["env"], "PYTHONPATH": str(ROOT / "src")}
            process = original_popen([sys.executable, "-c", script, directory, launch], *args, **kwargs)
            operations.append(process)
            return process
        return original_popen(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", spawn)

    def transport(request):
        if request.url.path.endswith("/poll"):
            return httpx.Response(200, json=store.poll())
        if request.url.path.endswith("/heartbeat"):
            value = WorkerUpdate.model_validate_json(request.content)
            return httpx.Response(200, json=store.heartbeat(expected.job_id, value))
        content = request.read()
        assert content == b"NON-PERFORMANCE ENGINEERING FIXTURE"
        return httpx.Response(200, json=store.artifact_received(
            expected.job_id, request.headers["X-Research-Lease"], "/fixture/opaque.zip",
            hashlib.sha256(content).hexdigest()))

    try:
        with httpx.Client(base_url=config.api_url, transport=httpx.MockTransport(transport)) as client:
            worker = Worker(config, client)
            worker.step()
            deadline = time.monotonic() + 5
            while not (job / "result.json").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            worker.step()
            worker.step()
        assert len(operations) == 1 and operations[0].wait(timeout=5) == 0
        assert read_json(job / "fixture-operation.json")["mode"] == "non-performance-fixture"
        assert read_json(job / "result.json")["launch_id"] == proof.new_launch_id
        assert row_for(company, expected.job_id)["sequence"] > 3
        assert recovery.tree_files(config.state_dir / proof.history_path) == expected.failure_files
        assert resume(failed_database)["duplicate"]
        assert len(operations) == 1
        # Typed public proof survives JSON without dates/fields/units changing.
        assert recovery.RecoveryProof.model_validate_json(proof.model_dump_json()) == proof
        assert recovery.proof_digest(proof) == digest
        receipt_name = os.environ.get("RECOVERY_QUALIFICATION_RECEIPT")
        if receipt_name:
            receipt_path = Path(receipt_name).resolve()
            assert receipt_path.is_relative_to(ROOT / ".local/recovery-build")
            captured_path = ROOT / ".local/recovery-evidence/production-failure-preflight.json"
            captured = read_json(captured_path)
            assert captured["result"] == CAPTURED_FAILURE
            assert not captured["checkout_exists"] and not captured["command_receipts"]
            assert not captured["performance_files"]
            assert subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain"], text=True) == ""
            observed_git_head = subprocess.check_output(
                ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
            assert observed_git_head == TOOL_COMMIT
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(receipt_path, {
                "schema_version": 1, "code_commit": observed_git_head,
                "proof_recovery_tool_commit": proof.recovery_tool_commit,
                "qualification": "captured-failure-shape-roundtrip",
                "captured_source_sha256": hashlib.sha256(captured_path.read_bytes()).hexdigest(),
                "proof_digest": digest, "proof_schema_version": proof.schema_version,
                "producer": "prepare_local", "consumer": "resume_server + unchanged Worker/execute_child",
                "database": "real disposable PostgreSQL", "process": "real detached non-performance fixture",
                "failure_history_preserved": True, "original_sequence": 3,
                "resumed_sequence": row_for(company, expected.job_id)["sequence"],
                "child_launch_count": len(operations), "duplicate_rearm_count": 0,
                "economic_performance_computed": False, "scientific_trials_added": 0,
                "real_3070_production_acceptance": "root-owned; not run by builder",
            })
    finally:
        for process in operations:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


@pytest.mark.parametrize("change", [{"state": "cancelled"}, {"reason": "executor-operation-failed"}])
def test_unknown_or_cancelled_result_is_not_a_precheck_failure(local_failure, change):
    config, expected, job = local_failure()
    atomic_json(job / "result.json", {**CAPTURED_FAILURE, **change})
    expected = expected.model_copy(update={"failure_files": recovery.tree_files(job)})
    with pytest.raises(ValueError):
        prepare(config, expected)
    assert not list((config.state_dir / "preparation-recoveries").iterdir())


def test_change_beyond_company_pin_is_blocked(local_failure):
    config, expected, _ = local_failure()
    config = config.model_copy(update={"repo_source": config.repo_source / "different"})
    with pytest.raises(recovery.RecoveryBlocked, match="configuration-changed-outside-company-pin"):
        prepare(config, expected)
