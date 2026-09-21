"""Lifecycle/contract tests. These are not the actual 3070 replay qualification."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from quant_company.research import executor
from quant_company.research.contracts import Assignment, ExecutionReceipt
from quant_company.research.recipes import load_recipe, recipe_digest
from quant_company.research.worker import (
    Worker,
    WorkerConfig,
    atomic_json,
    canonical_sha,
    identity_alive,
    process_identity,
    read_json,
    sha_file,
    signal_group,
)

ROOT = Path(__file__).resolve().parents[1]
REAL_POPEN = subprocess.Popen

# Only the expensive research operation is replaced. Launch intent, detached
# process, flock, PID identity and terminal receipts use the production code.
CHILD_FIXTURE = r'''
import subprocess
import sys
from pathlib import Path
from quant_company.research.executor import execute_child
from quant_company.research.worker import sha_file

def fixture_operation(config, assignment, recipe, job, started_at):
    with (job / 'executions.txt').open('a') as stream:
        stream.write('one bounded fixture execution\n')
    if sys.argv[3] == 'wait':
        child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'])
        (job / 'descendant.pid').write_text(str(child.pid))
        child.wait()
    (job / 'artifact.zip').write_bytes(b'opaque fixture bytes, no economic evidence')
    return sha_file(job / 'artifact.zip')

raise SystemExit(execute_child(Path(sys.argv[1]), sys.argv[2], operation=fixture_operation))
'''


def eventually(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("fixture did not reach expected state")


class FakeTransport:
    def __init__(self):
        self.assignment = None
        self.updates = []
        self.uploads = []
        self.loss_update = False
        self.loss_upload = False
        self.sequences = {}
        self.preparing_reply = None

    def __call__(self, request):
        if request.url.path.endswith("/poll"):
            assert json.loads(request.content) == {"worker_id": "worker"}
            value = self.assignment.model_dump(mode="json") if self.assignment else None
            return httpx.Response(200, json={"assignment": value})
        if request.url.path.endswith("/heartbeat"):
            value = json.loads(request.content)
            previous = self.sequences.setdefault(value["sequence"], value)
            assert previous == value, "a sequence must never acquire a different payload"
            self.updates.append(value)
            if value["state"] == "running" and self.assignment.action != "cancel":
                self.assignment = self.assignment.model_copy(update={"action": "reconcile"})
            if self.loss_update:
                self.loss_update = False
                raise httpx.ReadTimeout("fixture lost acknowledged heartbeat response")
            if value.get("reason") == "preparing" and self.preparing_reply is not None:
                return httpx.Response(200, json=self.preparing_reply)
            return httpx.Response(200, json={"ok": True, "state": value["state"], "duplicate": False})
        assert request.url.path.endswith("/artifact")
        data = request.read()
        assert request.headers["X-Artifact-Sha256"] == hashlib.sha256(data).hexdigest()
        self.uploads.append(data)
        if self.loss_upload:
            self.loss_upload = False
            raise httpx.ReadTimeout("fixture lost durable artifact response")
        return httpx.Response(200, json={"ok": True, "state": "received", "duplicate": len(self.uploads) > 1})


@pytest.fixture
def harness(tmp_path, monkeypatch):
    recipe = load_recipe()
    config = WorkerConfig(
        api_url="http://127.0.0.1:18764", token_file=tmp_path / "token", state_dir=tmp_path / "state",
        repo_source=tmp_path / "source", input_source=tmp_path / "inputs", evidence_repo=tmp_path / "evidence",
        research_python=Path(sys.executable), company_repo=ROOT, company_commit="a" * 40,
        heartbeat_seconds=0.1, cancel_grace_seconds=3,
    )
    config.token_file.write_text("test-worker-token-never-log")
    assignment = Assignment(
        job_id=uuid4(), project_id=uuid4(), revision=1, recipe_id=recipe.id,
        manifest_digest=recipe_digest(recipe), approval_event_id="fixture-authenticated-owner-event",
        lease_token="b" * 64, action="run",
    )
    transport = FakeTransport()
    transport.assignment = assignment
    client = httpx.Client(base_url=config.api_url, transport=httpx.MockTransport(transport))
    children = []
    mode = {"value": "finish"}

    def launch(command, *args, **kwargs):
        if isinstance(command, list) and "quant_company.research.executor" in command:
            directory = command[command.index("--job-dir") + 1]
            launch_id = command[command.index("--launch-id") + 1]
            command = [sys.executable, "-c", CHILD_FIXTURE, directory, launch_id, mode["value"]]
            process = REAL_POPEN(command, *args, **kwargs)
            children.append(process)
            return process
        return REAL_POPEN(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", launch)

    class Harness:
        def worker(self):
            return Worker(config, client)

    result = Harness()
    result.config, result.assignment, result.recipe = config, assignment, recipe
    result.transport, result.children, result.mode = transport, children, mode
    result.directory = config.state_dir / "jobs" / str(assignment.job_id)
    yield result
    for process in children:
        try:
            process.wait(timeout=0.3)
        except subprocess.TimeoutExpired:
            identity = process_identity(process.pid)
            if identity is not None:
                signal_group(identity, signal.SIGKILL)
            process.wait(timeout=5)
    client.close()


def wait_for_terminal(harness):
    return eventually(lambda: (harness.directory / "result.json").exists())


def test_recipe_digest_matches_shared_canonical_contract():
    recipe = load_recipe()
    assert recipe_digest(recipe) == canonical_sha(recipe.model_dump(mode="json"))
    assert len(recipe.expected_outputs) == 33
    assert set(recipe.expected_outputs) == {
        f"{method}/{name}" for method in executor.METHODS for name in executor.ECONOMIC_FILES
    }


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:18764", "http://example.com", "http://127.0.0.1@evil.example",
    "http://token@localhost:18764", "http://127.0.0.1:18764/?token=bad",
])
def test_worker_only_uses_explicit_loopback_tunnel(harness, url):
    with pytest.raises(ValidationError):
        WorkerConfig.model_validate({**harness.config.model_dump(), "api_url": url})


def test_duplicate_assignment_and_polling_restart_keep_single_execution(harness):
    harness.mode["value"] = "wait"
    worker = harness.worker()
    worker.step()
    eventually(lambda: (harness.directory / "descendant.pid").exists())
    for _ in range(3):
        harness.worker().step()
    assert len(harness.children) == 1
    assert (harness.directory / "executions.txt").read_text().count("one bounded") == 1
    assert all(update["state"] == "running" for update in harness.transport.updates)
    assert identity_alive(read_json(harness.directory / "process.json")["identity"])


def test_fsync_intent_without_spawn_is_uncertain_and_never_relaunched(harness):
    worker = harness.worker()
    directory = harness.directory
    directory.mkdir()
    atomic_json(directory / "state.json", {"assignment": harness.assignment.model_dump(mode="json"), "sequence": 0})
    atomic_json(directory / "launch-intent.json", {"launch_id": "crash-before-spawn"})
    for _ in range(3):
        worker.step()
        worker = harness.worker()
    assert not harness.children
    assert harness.transport.updates[-1]["state"] == "uncertain"
    assert harness.transport.updates[-1]["reason"] == "launch-without-process-receipt"
    assert (directory / "stop.json").is_file()


def test_reconcile_on_empty_disk_cannot_launch(harness):
    harness.transport.assignment = harness.assignment.model_copy(update={"action": "reconcile"})
    harness.worker().step()
    harness.transport.assignment = harness.assignment
    harness.worker().step()
    assert not harness.children
    assert harness.transport.updates[-1]["reason"] == "reconcile-without-local-state"


def test_subprocess_death_leaves_uncertain_without_reexecution(harness):
    harness.mode["value"] = "wait"
    harness.worker().step()
    eventually(lambda: (harness.directory / "descendant.pid").exists())
    identity = read_json(harness.directory / "process.json")["identity"]
    assert signal_group(identity, signal.SIGKILL)
    harness.children[0].wait(timeout=5)
    for _ in range(2):
        harness.worker().step()
    assert len(harness.children) == 1
    assert harness.transport.updates[-1]["reason"] == "process-outcome-unknown"


def test_second_private_executor_cannot_claim_existing_flock_or_receipt(harness):
    harness.mode["value"] = "wait"
    harness.worker().step()
    eventually(lambda: (harness.directory / "descendant.pid").exists())
    launch_id = read_json(harness.directory / "launch-intent.json")["launch_id"]
    process = REAL_POPEN(
        [sys.executable, "-c", CHILD_FIXTURE, str(harness.directory), launch_id, "finish"],
        start_new_session=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert process.wait(timeout=5) == 2
    assert (harness.directory / "executions.txt").read_text().count("one bounded") == 1
    harness.transport.assignment = harness.assignment.model_copy(update={"action": "cancel"})
    harness.worker().step()
    wait_for_terminal(harness)
    process = REAL_POPEN(
        [sys.executable, "-c", CHILD_FIXTURE, str(harness.directory), launch_id, "finish"],
        start_new_session=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert process.wait(timeout=5) == 2
    assert (harness.directory / "executions.txt").read_text().count("one bounded") == 1


def test_cancel_signals_verified_process_group_and_prevents_upload(harness):
    harness.mode["value"] = "wait"
    harness.worker().step()
    eventually(lambda: (harness.directory / "descendant.pid").exists())
    descendant = int((harness.directory / "descendant.pid").read_text())
    descendant_identity = process_identity(descendant)
    assert descendant_identity is not None
    harness.transport.assignment = harness.assignment.model_copy(update={"action": "cancel"})
    harness.worker().step()
    wait_for_terminal(harness)
    harness.worker().step()
    assert harness.transport.updates[-1]["state"] == "cancelled"
    assert not harness.transport.uploads
    eventually(lambda: not identity_alive(descendant_identity))
    assert read_json(harness.directory / "result.json")["state"] == "cancelled"


@pytest.mark.parametrize("change", [{"revision": 2}, {"lease_token": "c" * 64}, {"manifest_digest": "d" * 64}])
def test_changed_assignment_is_stopped_and_not_adopted(harness, change):
    harness.mode["value"] = "wait"
    harness.worker().step()
    eventually(lambda: (harness.directory / "descendant.pid").exists())
    harness.transport.assignment = harness.assignment.model_copy(update=change)
    harness.worker().step()
    wait_for_terminal(harness)
    assert len(harness.children) == 1
    assert not harness.transport.uploads
    state = read_json(harness.directory / "state.json")
    assert state["assignment"] == harness.assignment.model_dump(mode="json")
    assert state["uncertain_reason"] == "assignment-identity-changed"


def test_lost_heartbeat_response_retries_same_body_and_increases_sequence(harness):
    harness.mode["value"] = "wait"
    harness.transport.loss_update = True
    with pytest.raises(httpx.ReadTimeout):
        harness.worker().step()
    original = harness.transport.updates[-1]
    assert len(harness.children) == 0, "a lost prelaunch acknowledgement cannot start execution"
    assert not (harness.directory / "process.json").exists()
    assert read_json(harness.directory / "launch-intent.json")["phase"] == "prepared"
    assert read_json(harness.directory / "state.json")["pending_update"] == original
    harness.worker().step()
    assert harness.transport.updates[1] == original
    time.sleep(0.12)
    harness.worker().step()
    assert harness.transport.updates[-1]["sequence"] > original["sequence"]
    assert len(harness.children) == 1


def test_lost_upload_ack_retries_identical_bytes_after_restart(harness):
    harness.transport.loss_upload = True
    try:
        harness.worker().step()
    except httpx.ReadTimeout:
        pass
    wait_for_terminal(harness)
    if not harness.transport.uploads:
        with pytest.raises(httpx.ReadTimeout):
            harness.worker().step()
    harness.worker().step()
    assert len(harness.children) == 1
    assert len(harness.transport.uploads) == 2
    assert harness.transport.uploads[0] == harness.transport.uploads[1]
    assert read_json(harness.directory / "state.json")["uploaded"] is True
    harness.worker().step()
    assert len(harness.transport.uploads) == 2


def test_changed_local_archive_never_uploads(harness):
    harness.transport.loss_upload = True
    try:
        harness.worker().step()
    except httpx.ReadTimeout:
        pass
    wait_for_terminal(harness)
    if not harness.transport.uploads:
        with pytest.raises(httpx.ReadTimeout):
            harness.worker().step()
    (harness.directory / "artifact.zip").write_bytes(b"changed after first upload")
    harness.worker().step()
    assert len(harness.transport.uploads) == 1
    assert harness.transport.updates[-1]["reason"] == "local-artifact-digest-mismatch"
    assert len(harness.children) == 1


def test_cancel_before_launch_never_spawns(harness):
    harness.transport.assignment = harness.assignment.model_copy(update={"action": "cancel"})
    harness.worker().step()
    assert not harness.children
    assert harness.transport.updates[-1]["state"] == "cancelled"


def test_pid_reuse_cannot_signal_different_process(harness):
    harness.mode["value"] = "wait"
    harness.worker().step()
    identity = read_json(harness.directory / "process.json")["identity"]
    reused = {**identity, "start": "unrelated-start-identity"}
    assert not signal_group(reused, signal.SIGTERM)
    assert identity_alive(identity)


def test_fixed_path_guard_rejects_sealed_or_unregistered_paths(harness):
    with pytest.raises(executor.ExecutionBlocked, match="invalid-registered-path"):
        executor.checked_path(harness.config.input_source, "../sealed/prices.parquet")
    assert all("sealed" not in name for name in harness.recipe.input_files)


def test_qualification_contract_requires_typed_nonempty_no_performance_receipt(harness, tmp_path):
    recipe = harness.recipe
    path = tmp_path / "qualification.json"
    value = {
        "status": "passed", "performanceRead": False, "registeredPerformanceWindowRead": False,
        "sealedRead": False, "emptySampleRejected": True, "codeCommit": recipe.code_commit,
        "configDigest": recipe.config_files[f"{executor.PILOT}/configs/m1-liquid-11.json"],
        "executionMachine": "worker", "hostname": recipe.hostname, "gpu": recipe.gpu,
        "lakeId": recipe.lake_id, "python": "3.11.15", "dirtyPaths": [],
        "discoveryEnvelope": {"path": executor.DISCOVERY_ARGS,
                              "sha256": recipe.config_files[executor.DISCOVERY_ARGS]},
        "qualifiedUniverseCount": 1, "jsonDates": ["2013-12-30"], "typedSchema": {"asof": "datetime64[ms]"},
    }
    atomic_json(path, value)
    executor.verify_qualification(path, recipe, "m1")
    for change in (
        {"qualifiedUniverseCount": 0}, {"jsonDates": []}, {"typedSchema": {}},
        {"performanceRead": True}, {"sealedRead": True}, {"codeCommit": "0" * 40},
        {"dirtyPaths": ["changed config"]},
    ):
        atomic_json(path, {**value, **change})
        with pytest.raises(executor.ExecutionBlocked):
            executor.verify_qualification(path, recipe, "m1")


def test_nonapproved_host_is_blocked_before_any_research_command(harness, monkeypatch):
    monkeypatch.setattr(executor.platform, "node", lambda: "not-the-approved-3070")
    with pytest.raises(executor.ExecutionBlocked, match="execution-requires-approved-3070-host"):
        executor.execute_replay(harness.config, harness.assignment, harness.recipe, harness.directory, "fixture")
    assert not harness.directory.exists()


def test_archive_exact_members_typed_receipt_and_no_lease(harness, tmp_path):
    recipe = harness.recipe
    job, outputs, evidence = (tmp_path / name for name in ("bundle", "outputs", "evidence"))
    for directory in (job, outputs, evidence):
        directory.mkdir()
    hashes = {}
    for name in recipe.expected_outputs:
        file = outputs / name
        file.parent.mkdir(exist_ok=True)
        file.write_text("synthetic contract fixture\n")
        hashes[name] = sha_file(file)
    recipe = recipe.model_copy(update={"expected_outputs": hashes})
    audit = evidence / recipe.audit_path
    audit.parent.mkdir(parents=True)
    audit.write_bytes(b"original audit fixture bytes")
    audit.with_suffix(".receipt.json").write_bytes(b"original receipt fixture bytes")
    atomic_json(job / "audit-verification.json", {"fixture": "not an actual audit verification"})
    receipt = ExecutionReceipt(
        **harness.assignment.model_dump(exclude={"action", "lease_token"}),
        worker_id="worker", hostname=recipe.hostname, gpu=recipe.gpu,
        code_commit=recipe.code_commit, company_commit=harness.config.company_commit,
        input_files=recipe.input_files, config_files=recipe.config_files, output_files=hashes,
        started_at="2026-09-21T00:00:00+00:00", completed_at="2026-09-21T00:01:00+00:00",
        execution_count=1, qualification_passed=True, sealed_read=False, scientific_trials_added=0,
    )
    digest = executor.build_archive(job, recipe, receipt, outputs, evidence)
    assert digest == sha_file(job / "artifact.zip")
    with zipfile.ZipFile(job / "artifact.zip") as archive:
        assert set(archive.namelist()) == set(recipe.expected_outputs) | {
            "receipt.json", "audit.md", "audit.receipt.json", "audit-verification.json",
        }
        assert ExecutionReceipt.model_validate_json(archive.read("receipt.json")) == receipt
        for name, digest in hashes.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
        assert all(harness.assignment.lease_token.encode() not in archive.read(name) for name in archive.namelist())
        assert all(not info.is_dir() and (info.external_attr >> 16) & 0o170000 == 0o100000
                   for info in archive.infolist())


def test_snapshot_staging_fresh_commit_and_readonly_copies(harness, tmp_path):
    source = tmp_path / "repository"
    source.mkdir()
    for name in harness.recipe.config_files:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")
    (source / ".gitignore").write_text("labs/company-kr-etf-pilot/data/\n")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run([
        "git", "-C", str(source), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
        "-c", "core.hooksPath=/dev/null", "commit", "-qm", "synthetic checkout fixture",
    ], check=True)
    commit = executor.git_value(source, "rev-parse", "HEAD")
    inputs = tmp_path / "input-source"
    input_hashes = {}
    for name in harness.recipe.input_files:
        path = inputs / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic staging fixture, not parquet")
        input_hashes[name] = sha_file(path)
    recipe = harness.recipe.model_copy(update={
        "code_commit": commit, "input_files": input_hashes,
        "config_files": {name: sha_file(source / name) for name in harness.recipe.config_files},
    })
    config = harness.config.model_copy(update={"repo_source": source, "input_source": inputs})
    job = tmp_path / "staging-job"
    job.mkdir()
    checkout = executor.stage_checkout(config, recipe, job)
    assert executor.git_value(checkout, "rev-parse", "HEAD") == commit
    assert executor.git_value(source, "status", "--porcelain") == ""
    for name, digest in input_hashes.items():
        assert sha_file(checkout / name) == digest == sha_file(inputs / name)
        assert (checkout / name).stat().st_mode & 0o222 == 0
        assert (inputs / name).stat().st_mode & 0o200
    with pytest.raises(executor.ExecutionBlocked, match="execution-checkout-already-exists"):
        executor.stage_checkout(config, recipe, job)


def test_cancel_during_unacknowledged_preparation_never_spawns(harness):
    harness.transport.loss_update = True
    with pytest.raises(httpx.ReadTimeout):
        harness.worker().step()
    harness.transport.assignment = harness.assignment.model_copy(update={"action": "cancel"})
    harness.worker().step()
    assert not harness.children
    assert harness.transport.updates[-1]["state"] == "cancelled"
    assert read_json(harness.directory / "launch-intent.json")["phase"] == "prepared"


def test_actual_polling_process_kill_does_not_kill_or_relaunch_executor(harness, tmp_path):
    configuration = tmp_path / "poller-config.json"
    assignment_path = tmp_path / "poller-assignment.json"
    atomic_json(configuration, harness.config.model_dump(mode="json"))
    atomic_json(assignment_path, harness.assignment.model_dump(mode="json"))
    # The polling process is really killed, not just reconstructed in-process.
    # Its executor is a different session leader with the production claim path.
    script = r'''
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import httpx
from quant_company.research.worker import Worker, WorkerConfig

config = WorkerConfig.from_file(Path(sys.argv[1]))
assignment = json.loads(Path(sys.argv[2]).read_text())
fixture = sys.argv[3]
original = subprocess.Popen

def spawn(command, *args, **kwargs):
    if isinstance(command, list) and 'quant_company.research.executor' in command:
        directory = command[command.index('--job-dir') + 1]
        launch_id = command[command.index('--launch-id') + 1]
        command = [sys.executable, '-c', fixture, directory, launch_id, 'wait']
    return original(command, *args, **kwargs)

subprocess.Popen = spawn

def respond(request):
    if request.url.path.endswith('/poll'):
        return httpx.Response(200, json={'assignment': assignment})
    value = json.loads(request.content)
    return httpx.Response(200, json={'ok': True, 'state': value['state'], 'duplicate': False})

client = httpx.Client(base_url=config.api_url, transport=httpx.MockTransport(respond))
Worker(config, client).step()
(config.state_dir / 'poller-ready').write_text(str(os.getpid()))
time.sleep(30)
'''
    poller = REAL_POPEN(
        [sys.executable, "-c", script, str(configuration), str(assignment_path), CHILD_FIXTURE],
        start_new_session=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    try:
        eventually(lambda: (harness.config.state_dir / "poller-ready").exists())
        eventually(lambda: (harness.directory / "descendant.pid").exists())
        identity = read_json(harness.directory / "process.json")["identity"]
        assert identity["pid"] != poller.pid and identity["pgid"] != poller.pid
        poller.kill()
        poller.wait(timeout=5)
        assert identity_alive(identity)
        harness.transport.assignment = harness.assignment.model_copy(update={"action": "reconcile"})
        harness.worker().step()
        assert not harness.children
        assert (harness.directory / "executions.txt").read_text().count("one bounded") == 1
        harness.transport.assignment = harness.assignment.model_copy(update={"action": "cancel"})
        harness.worker().step()
        wait_for_terminal(harness)
        harness.worker().step()
        assert harness.transport.updates[-1]["state"] == "cancelled"
        eventually(lambda: not identity_alive(identity))
    finally:
        if poller.poll() is None:
            poller.kill()
        poller.wait(timeout=5)
        process_path = harness.directory / "process.json"
        if process_path.exists():
            signal_group(read_json(process_path)["identity"], signal.SIGKILL)


def test_configuration_error_does_not_log_token_material(tmp_path):
    config = tmp_path / "invalid.json"
    secret = "fixture-token-must-not-appear-in-output"
    config.write_text(json.dumps({"api_url": "http://" + secret + "@example.test"}))
    result = subprocess.run(
        [sys.executable, "-m", "quant_company.research.worker", "--config", str(config), "--once"],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert secret not in result.stdout + result.stderr
    assert "worker-configuration-unavailable" in result.stderr


def test_http_200_cancel_request_racing_preparing_heartbeat_prevents_launch(harness):
    harness.transport.preparing_reply = {"ok": True, "state": "cancel_requested", "duplicate": False}
    harness.worker().step()
    assert not harness.children
    assert not (harness.directory / "process.json").exists()
    assert read_json(harness.directory / "launch-intent.json")["phase"] == "prepared"
    assert harness.transport.updates[-1]["state"] == "cancelled"
    assert read_json(harness.directory / "state.json")["cancelled"] is True


@pytest.mark.parametrize("status", ["received", "completed", "failed", "awaiting_audit"])
def test_server_terminal_preparing_acknowledgement_suppresses_launch(harness, status):
    harness.transport.preparing_reply = {"ok": True, "state": status, "duplicate": False}
    harness.worker().step()
    harness.worker().step()
    assert not harness.children
    assert read_json(harness.directory / "state.json")["server_terminal"] == status
    assert read_json(harness.directory / "launch-intent.json")["phase"] == "prepared"


@pytest.mark.parametrize("response", [
    {"ok": True}, {"ok": False, "state": "running", "duplicate": False},
    {"ok": True, "state": "unexpected", "duplicate": False},
])
def test_malformed_preparing_ack_retries_pending_without_launch(harness, response):
    harness.transport.preparing_reply = response
    for _ in range(2):
        with pytest.raises(ValueError, match="invalid-heartbeat-acknowledgement"):
            harness.worker().step()
    assert not harness.children
    assert harness.transport.updates[0] == harness.transport.updates[1]
    assert read_json(harness.directory / "state.json")["pending_update"]["sequence"] == 1
    assert read_json(harness.directory / "launch-intent.json")["phase"] == "prepared"
