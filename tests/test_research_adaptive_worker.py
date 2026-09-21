"""Synthetic engineering fixtures; no economic research or Mac sandbox claim.

Real Git bundle/patch/commit, worker transport, detached process ownership and
strict typed artifact consumers are exercised. The OS sandbox call is replaced
in the child by a labelled tiny fixture producer; 3070 bwrap acceptance is separate.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from quant_company.company import fingerprint
from quant_company.research import adaptive_executor, executor
from quant_company.research.adaptive_contracts import (
    AdaptiveAssignment,
    AdaptiveExecutionProfile,
    AdaptiveExecutionReceipt,
    AdaptiveManifest,
    AdaptiveQualification,
    AdaptiveResult,
    digest_model,
    parse_assignment,
    record_digest,
)
from quant_company.research.adaptive_executor import AdaptiveProfile, read_return_series
from quant_company.research.mission_contracts import MissionSpec, TrialPlan
from quant_company.research.sandbox import runtime_digest
from quant_company.research.worker import Worker, WorkerConfig, atomic_json, read_json, sha_file
from quant_company.research.workspace import TextPatch, prepare_workspace
from tests.test_research_worker import REAL_POPEN, eventually, pinned_company  # noqa: F401

# This operation does not claim bwrap isolation. It uses actual pinned code and
# approved input fixture bytes, and produces only synthetic zero-return rows.
CHILD = r'''
import csv
import io
import json
import sys
from pathlib import Path
from quant_company.research import adaptive_executor as ae
from quant_company.research.adaptive_contracts import AdaptiveQualification, AdaptiveResult
from quant_company.research.executor import execute_child
from quant_company.research.sandbox import SandboxReceipt, build_command
from quant_company.research.worker import atomic_json, canonical_sha, sha_file, utc_now

ae.verify_host = lambda manifest: None

def fixture_sandbox(spec):
    assert spec.fixture_only is True
    build_command(spec)  # Validate actual hashes, guest argv and allowed mount sets.
    mounts = {mount.target: mount for mount in spec.input_mounts}
    manifest = json.loads(mounts[ae.CONTRACT_INPUT].source.read_text())
    action = spec.argv[0]
    started = utc_now()
    if action == 'qualify':
        assert set(mounts) == {'warmup/observations.json', ae.CONTRACT_INPUT}
        rows = json.loads(mounts['warmup/observations.json'].source.read_text())
        value = AdaptiveQualification(
            trial_id=manifest['trial_id'], plan_digest=manifest['plan_digest'],
            code_commit=manifest['plan']['code_commit'], config_files=manifest['plan']['config_files'],
            input_files={'warmup/observations.json': mounts['warmup/observations.json'].sha256},
            sample_count=len(rows), json_dates=[row['date'] for row in rows],
            json_datetimes=[row['observed_at'] for row in rows],
            typed_schema={'date':'date','observed_at':'datetime','value':'float'},
            primary_unit='fraction-per-year', empty_sample_rejected=True,
            non_finite_rejected=True, json_roundtrip_passed=True, performance_read=False, sealed_read=False,
        )
        atomic_json(spec.output_dir / 'qualification.json', value.model_dump(mode='json'))
    else:
        rows = json.loads(mounts['dev/observations.json'].source.read_text())
        for cost in ('base','stress'):
            with (spec.output_dir / (cost + '.csv')).open('w', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['date','net_return'])
                writer.writerows((row['date'], 0.0) for row in rows)
        output_files = {cost + '.csv':sha_file(spec.output_dir / (cost + '.csv')) for cost in ('base','stress')}
        refs = {cost:{'path':cost+'.csv','sha256':output_files[cost+'.csv'],
                      'frequency':'monthly','unit':'fraction-per-period'} for cost in ('base','stress')}
        value = AdaptiveResult(
            trial_id=manifest['trial_id'], plan_digest=manifest['plan_digest'],
            code_commit=manifest['plan']['code_commit'],
            metrics={'primary':{'metric':'stress-net-absolute-cagr','direction':'maximize',
                                'unit':'fraction-per-year','value':0.0},
                     'risks':{'max_drawdown':0.0}, 'sample_count':len(rows),
                     'sample_window':manifest['plan']['development']},
            base_returns=refs['base'], stress_returns=refs['stress'], output_files=output_files,
        )
        atomic_json(spec.output_dir / 'result.json', value.model_dump(mode='json'))
    (spec.output_dir / 'stdout.log').write_text('synthetic protocol fixture, no OS isolation claim\n')
    (spec.output_dir / 'stderr.log').write_text('')
    return SandboxReceipt(1, canonical_sha({'fixture':True,'argv':spec.argv}), spec.code_commit,
                          spec.profile.profile_id, 1, 0, False, started, utc_now(),
                          spec.output_dir / 'stdout.log', spec.output_dir / 'stderr.log')

ae.run_sandbox = fixture_sandbox
# Actual host check remains in production. This labelled engineering fixture uses
# the same detached executor/claim/reconciliation path with only host/sandbox mocked.
ae.platform.node = lambda: 'DESKTOP-5T00NAF'
raise SystemExit(execute_child(Path(sys.argv[1]), sys.argv[2]))
'''


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def snapshot(root: Path, contents: dict[str, str]) -> tuple[Path, str]:
    root.mkdir()
    for name, value in contents.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    git(root, "add", ".")
    git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
        "-c", "core.hooksPath=/dev/null", "commit", "-qm", "Synthetic protocol source")
    commit = git(root, "rev-parse", "HEAD")
    bundle = root.parent / (root.name + ".bundle")
    git(root, "bundle", "create", str(bundle), "HEAD")
    return bundle, commit


class Transport:
    def __init__(self, assignment, bundle):
        self.assignment, self.bundle = assignment, bundle
        self.downloads, self.updates, self.uploads = [], [], []
        self.lose_download = False
        self.lose_upload = False
        self.lose_preparing = False

    def __call__(self, request):
        if request.url.path.endswith("/poll"):
            return httpx.Response(200, json={"assignment": self.assignment.model_dump(mode="json")})
        if request.url.path.endswith("/bundle"):
            assert request.method == "GET" and request.headers["X-Research-Lease"] == self.assignment.lease_token
            self.downloads.append(request.url.path)
            if self.lose_download:
                self.lose_download = False
                raise httpx.ReadTimeout("Synthetic lost bundle response")
            return httpx.Response(200, content=self.bundle)
        if request.url.path.endswith("/heartbeat"):
            value = json.loads(request.content)
            self.updates.append(value)
            if value["state"] == "running":
                self.assignment = self.assignment.model_copy(update={"action": "reconcile"})
            if self.lose_preparing:
                self.lose_preparing = False
                raise httpx.ReadTimeout("Synthetic lost preparing acknowledgement")
            return httpx.Response(200, json={"ok": True, "state": value["state"], "duplicate": False})
        assert request.url.path.endswith("/artifact")
        data = request.read()
        assert request.headers["X-Artifact-Sha256"] == hashlib.sha256(data).hexdigest()
        self.uploads.append(data)
        if self.lose_upload:
            self.lose_upload = False
            raise httpx.ReadTimeout("Synthetic lost artifact response")
        return httpx.Response(200, json={"ok": True, "state": "received", "duplicate": len(self.uploads) > 1})


@pytest.fixture
def adaptive(tmp_path, monkeypatch, pinned_company):  # noqa: F811 -- imported pytest fixture
    source_bundle, base = snapshot(tmp_path / "research", {
        "driver.py": "# Pinned synthetic evaluator protocol; not an economic strategy.\n",
        "model.py": "fixture = 1\n", "config.json": '{"fixture":true}\n',
        "history/results.json": '{"fixture":"not accessible to qualification"}\n',
    })
    prepared = prepare_workspace(source_bundle, sha_file(source_bundle), base, tmp_path / "prepared",
                                 ("model.py",), ("driver.py",),
                                 (TextPatch("model.py", "fixture = 1\n", "fixture = 2\n"),))
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    python = runtime_root / "python"
    shutil.copyfile(Path(sys.executable).resolve(), python)
    python.chmod(0o500)
    bwrap = runtime_root / "bwrap"
    bwrap.write_text("synthetic fixture executable; not called")
    bwrap.chmod(0o500)
    runtime = {
        "profile_id": "kr-etf-monthly-python-v1", "python_executable": "/runtime/python",
        "python_sha256": sha_file(python),
        "mounts": [{"source": str(python), "target": "/runtime/python",
                    "sha256": runtime_digest(python, (runtime_root,))}],
        "allowed_roots": [str(runtime_root)], "bwrap_executable": str(bwrap),
    }
    inputs = tmp_path / "inputs"
    input_files = {}
    for name, rows in {
        "warmup/observations.json": [{"date": "2019-12-01", "observed_at": "2019-12-01T00:00:00+00:00", "value": 1}],
        "dev/observations.json": [{"date": "2020-01-01"}, {"date": "2020-02-01"}, {"date": "2020-03-01"}],
    }.items():
        file = inputs / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(rows))
        input_files[name] = sha_file(file)
    public = AdaptiveExecutionProfile(
        id="kr-etf-monthly-python-v1", entrypoint="driver.py", entrypoint_sha256=prepared.manifest["files"]["driver.py"],
        protected_paths=["driver.py"], code_paths=["driver.py", "model.py", "config.json"], python_executable=runtime["python_executable"],
        python_sha256=runtime["python_sha256"],
        runtime_mounts=[{"target": mount["target"], "sha256": mount["sha256"]} for mount in runtime["mounts"]],
        qualification_input_names=["warmup/observations.json"], evaluation_input_names=list(input_files),
        qualification_timeout_seconds=10, evaluation_timeout_seconds=10, fixture_only=True,
    )
    spec = MissionSpec(
        title="Synthetic adaptive I/O qualification, not economic research", kind="strategy",
        objective={"metric": "stress-net-absolute-cagr", "direction": "maximize", "unit": "fraction-per-year"},
        base_cost_bps=10.0, stress_cost_bps=30.0, risk_constraints=[],
        development={"start": "2020-01-01", "end": "2020-03-01"}, sealed=[{"start": "2021-01-01", "end": "2021-12-31"}],
        data={"lake_id": "synthetic-protocol-fixture", "input_files": input_files},
        code={"repository": "quant-lab", "base_commit": base, "write_paths": ["model.py"]},
        allowed_changes=["implementation"], execution_profile=public.id, execution_profile_digest=digest_model(public),
        resources={"worker_id": "worker", "priority": "owner"},
        search={"max_trials_per_cycle": 2, "patience": 2, "min_improvement": 0.0, "continuous": False},
        baseline_source_ids=["fixture:synthetic-not-research"],
    )
    plan = TrialPlan(
        trial_id=uuid4(), proposal_id=uuid4(), mission_digest=record_digest(spec), execution_profile=public.id,
        implementer="engineer", repository="quant-lab", code_commit=prepared.commit, changed_paths=["model.py"],
        config_files={"config.json": prepared.manifest["files"]["config.json"]}, input_files=input_files,
        lake_id=spec.data.lake_id, development=spec.development, worker_id="worker",
        hostname="DESKTOP-5T00NAF", gpu="NVIDIA GeForce RTX 3070",
    )
    manifest = AdaptiveManifest(
        mission_id=uuid4(), mission_digest=record_digest(spec), trial_id=plan.trial_id, plan_digest=record_digest(plan),
        plan=plan, spec=spec, code_files={path: prepared.manifest["files"][path] for path in public.code_paths},
        bundle_sha256=prepared.bundle_sha256,
        config_path="config.json", company_commit=pinned_company[1],
    )
    assignment = AdaptiveAssignment(job_id=uuid4(), project_id=uuid4(), revision=1,
                                    manifest_digest=digest_model(manifest), approval_event_id="fixture-owner-event",
                                    lease_token="b" * 64, action="run", manifest=manifest)
    profile = AdaptiveProfile(public_profile=public, runtime=runtime,
                              input_sources={name: inputs / name for name in input_files}, allowed_input_roots=[inputs])
    profile_path = tmp_path / "profile.json"
    atomic_json(profile_path, profile.model_dump(mode="json"))
    config = WorkerConfig(
        api_url="http://127.0.0.1:18764", token_file=tmp_path / "token", state_dir=tmp_path / "state",
        repo_source=tmp_path / "unused", input_source=inputs, evidence_repo=tmp_path / "unused-evidence",
        research_python=Path(sys.executable), company_repo=pinned_company[0], company_commit=pinned_company[1],
        adaptive_profiles={public.id: profile_path}, heartbeat_seconds=0.1,
    )
    transport = Transport(assignment, prepared.bundle_path.read_bytes())
    client = httpx.Client(base_url=config.api_url, transport=httpx.MockTransport(transport))
    children = []

    def launch(command, *args, **kwargs):
        if isinstance(command, list) and "quant_company.research.executor" in command:
            job = command[command.index("--job-dir") + 1]
            launch_id = command[command.index("--launch-id") + 1]
            command = [sys.executable, "-c", CHILD, job, launch_id]
            process = REAL_POPEN(command, *args, **kwargs)
            children.append(process)
            return process
        return REAL_POPEN(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", launch)
    value = SimpleNamespace(config=config, transport=transport, assignment=assignment, manifest=manifest,
                            public_profile=public, profile=profile, children=children, prepared=prepared,
                            client=client, directory=config.state_dir / "jobs" / str(assignment.job_id),
                            worker=lambda: Worker(config, client))
    yield value
    for process in children:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
    client.close()


def finish(adaptive):
    eventually(lambda: (adaptive.directory / "result.json").exists(), timeout=10)
    value = read_json(adaptive.directory / "result.json")
    assert value["state"] == "ready", (value, (adaptive.directory / "executor.log").read_text())
    adaptive.worker().step()


def test_qualification_real_git_transport_child_and_typed_artifact(adaptive):
    assert record_digest(adaptive.manifest.spec) == fingerprint(adaptive.manifest.spec.model_dump(mode="json"))
    assert record_digest(adaptive.manifest.plan) == fingerprint(adaptive.manifest.plan.model_dump(mode="json"))
    assert parse_assignment(adaptive.assignment.model_dump(mode="json")) == adaptive.assignment
    assert "history/results.json" in adaptive.prepared.manifest["files"]
    assert "history/results.json" not in adaptive.manifest.code_files
    adaptive.worker().step()
    finish(adaptive)
    assert len(adaptive.children) == 1 and len(adaptive.transport.downloads) == 1
    archive_bytes = adaptive.transport.uploads[0]
    archive_path = adaptive.directory / "artifact.zip"
    assert archive_path.read_bytes() == archive_bytes
    with zipfile.ZipFile(archive_path) as archive:
        receipt = AdaptiveExecutionReceipt.model_validate_json(archive.read("receipt.json"))
        qualification = AdaptiveQualification.model_validate_json(archive.read("qualification.json"))
        result = AdaptiveResult.model_validate_json(archive.read("result.json"))
        profile = AdaptiveExecutionProfile.model_validate_json(archive.read("profile.json"))
        manifest = AdaptiveManifest.model_validate_json(archive.read("manifest.json"))
        assert manifest == adaptive.manifest and digest_model(profile) == manifest.spec.execution_profile_digest
        assert receipt.code_commit == adaptive.prepared.commit
        assert receipt.company_commit == adaptive.config.company_commit
        assert receipt.fixture_only and receipt.scientific_trials_added == 0
        assert qualification.sample_count == 1 and not qualification.performance_read
        assert isinstance(qualification.json_datetimes[0], datetime) and qualification.json_datetimes[0].tzinfo
        assert set(qualification.input_files) == {"warmup/observations.json"}
        assert result.metrics.primary.unit == "fraction-per-year" and result.metrics.sample_count == 3
        assert set(archive.namelist()) == {
            "receipt.json", "manifest.json", "qualification.json", "result.json", "runtime.json", "profile.json",
            "sandbox-qualification.json", "sandbox-evaluation.json",
            *("code/" + name for name in manifest.code_files), *("outputs/" + name for name in result.output_files),
        }
        assert receipt.qualification_sha256 == hashlib.sha256(archive.read("qualification.json")).hexdigest()
        assert receipt.result_sha256 == hashlib.sha256(archive.read("result.json")).hexdigest()
        assert receipt.sandbox_qualification_sha256 == hashlib.sha256(archive.read("sandbox-qualification.json")).hexdigest()
        assert receipt.sandbox_evaluation_sha256 == hashlib.sha256(archive.read("sandbox-evaluation.json")).hexdigest()
        assert receipt.runtime_sha256 == hashlib.sha256(archive.read("runtime.json")).hexdigest()
        for name, digest in result.output_files.items():
            assert hashlib.sha256(archive.read("outputs/" + name)).hexdigest() == digest
        assert all(adaptive.assignment.lease_token.encode() not in archive.read(name) for name in archive.namelist())
        before, after = (json.loads(archive.read(name)) for name in ("sandbox-qualification.json", "sandbox-evaluation.json"))
        assert set(before["input_files"]) == {"warmup/observations.json"}
        assert set(after["input_files"]) == set(adaptive.manifest.plan.input_files)
        assert before["completed_at"] <= after["started_at"]
    for _ in range(2):
        adaptive.worker().step()
    assert len(adaptive.children) == 1 and len(adaptive.transport.uploads) == 1


def test_bundle_transport_retry_precedes_any_launch(adaptive):
    adaptive.transport.lose_download = True
    with pytest.raises(httpx.ReadTimeout):
        adaptive.worker().step()
    assert not adaptive.children and not (adaptive.directory / "launch-intent.json").exists()
    assert read_json(adaptive.directory / "state.json")["preparing_bundle"] is True
    adaptive.transport.assignment = adaptive.assignment.model_copy(update={"action": "reconcile"})
    adaptive.worker().step()
    finish(adaptive)
    assert len(adaptive.children) == 1 and len(adaptive.transport.downloads) == 2


def test_bad_bundle_hash_never_launches(adaptive):
    adaptive.transport.bundle = b"wrong bytes"
    for _ in range(2):
        with pytest.raises(ValueError, match="adaptive-bundle-digest-mismatch"):
            adaptive.worker().step()
    assert not adaptive.children and not (adaptive.directory / "source.bundle").exists()


def test_restart_lost_upload_reuses_identical_archive(adaptive):
    adaptive.transport.lose_upload = True
    adaptive.worker().step()
    eventually(lambda: (adaptive.directory / "result.json").exists(), timeout=10)
    with pytest.raises(httpx.ReadTimeout):
        adaptive.worker().step()
    adaptive.worker().step()
    assert len(adaptive.children) == 1 and len(adaptive.transport.uploads) == 2
    assert adaptive.transport.uploads[0] == adaptive.transport.uploads[1]


def test_company_pin_mismatch_blocks_before_launch(adaptive):
    adaptive.config.company_commit = "a" * 40
    adaptive.worker().step()
    assert not adaptive.children and not adaptive.transport.downloads
    assert adaptive.transport.updates[-1]["reason"] == "adaptive-company-pin-mismatch"


def test_prepared_job_uses_own_pinned_release_after_polling_upgrade(adaptive):
    worker = adaptive.worker()
    adaptive.transport.lose_preparing = True
    with pytest.raises(httpx.ReadTimeout):
        worker.accept(adaptive.assignment)
    state = read_json(adaptive.directory / "state.json")
    assert read_json(adaptive.directory / "launch-intent.json")["phase"] == "prepared"
    # The new polling config need not have the old pin; the stored release does.
    adaptive.config.company_commit = "a" * 40
    adaptive.worker()._resume_prepared_launch(adaptive.directory, state)
    finish(adaptive)
    assert len(adaptive.children) == 1


def test_operator_profile_hash_and_evaluator_pin_cannot_drift(adaptive):
    value = adaptive.public_profile.model_dump(mode="json")
    value["evaluation_timeout_seconds"] += 1
    profile = adaptive.profile.model_copy(update={"public_profile": AdaptiveExecutionProfile.model_validate(value)})
    atomic_json(adaptive.config.adaptive_profiles[adaptive.public_profile.id], profile.model_dump(mode="json"))
    with pytest.raises(executor.ExecutionBlocked, match="profile-digest-mismatch"):
        adaptive_executor.load_profile(adaptive.config, adaptive.manifest)


@pytest.mark.parametrize("csv_bytes", [
    b"date,net_return\n", b"date,net_return\n2020-01-01,0\n", b"date,net_return\n2020-01-01,1\n2020-02-01,0\n",
    b"date,net_return\n2020-01-01,0\n2020-02-01,NaN\n", b"date,net_return\n2020-01-01,0\n2020-02-01,inf\n",
    b"date,net_return\n2020-02-01,0\n2020-01-01,0\n", b"date,net_return\n2020-01-01,0\n2020-01-01,0\n",
    b"date,net_return\n2020-01-01,0\n2020-02-01,-1.1\n", b"datetime,return\n2020-01-01,0\n2020-02-01,0\n",
    b"date,net_return\n2020-01-01T00:00:00Z,0\n2020-02-01,0\n",
])
def test_qualification_rejects_empty_nonfinite_ambiguous_or_invalid_series(csv_bytes):
    with pytest.raises(executor.ExecutionBlocked, match="invalid-dated-return-series"):
        read_return_series(csv_bytes)


def test_contract_rejects_changed_mission_digest_and_invalid_paths(adaptive):
    value = adaptive.manifest.model_dump(mode="json")
    value["spec"]["base_cost_bps"] += 1
    with pytest.raises(ValidationError, match="digest-mismatch"):
        AdaptiveManifest.model_validate(value)
    value = adaptive.manifest.model_dump(mode="json")
    value["code_files"]["../sealed/prices.csv"] = "a" * 64
    with pytest.raises(ValidationError):
        AdaptiveManifest.model_validate(value)


def test_qualification_wrong_warmup_and_field_units_rejected(adaptive, tmp_path):
    value = {
        "trial_id": str(adaptive.manifest.trial_id), "plan_digest": adaptive.manifest.plan_digest,
        "code_commit": adaptive.manifest.plan.code_commit, "config_files": adaptive.manifest.plan.config_files,
        "input_files": {"warmup/observations.json": adaptive.manifest.plan.input_files["warmup/observations.json"]},
        "sample_count": 1, "json_dates": ["2020-01-01"], "json_datetimes": ["2020-01-01T00:00:00+00:00"],
        "typed_schema": {"date": "date", "time": "datetime"}, "primary_unit": "fraction-per-year",
        "empty_sample_rejected": True, "non_finite_rejected": True, "json_roundtrip_passed": True,
        "performance_read": False, "sealed_read": False,
    }
    path = tmp_path / "qualification.json"
    atomic_json(path, value)
    with pytest.raises(executor.ExecutionBlocked, match="outside-warmup"):
        adaptive_executor.qualification_from_file(path, adaptive.manifest, adaptive.public_profile)
    for change in ({"primary_unit": "percent"}, {"sample_count": 0}, {"performance_read": True},
                   {"json_dates": ["2019-12-01T00:00:00Z"]}, {"json_datetimes": ["2019-12-01T00:00:00"]}):
        with pytest.raises(ValidationError):
            AdaptiveQualification.model_validate({**value, **change})


def test_actual_executor_rejects_non3070_host_before_files_or_commands(adaptive, monkeypatch):
    monkeypatch.setattr(adaptive_executor.platform, "node", lambda: "fixture-mac-not-worker")
    with pytest.raises(executor.ExecutionBlocked, match="approved-3070-host"):
        adaptive_executor.execute_adaptive(adaptive.config, adaptive.assignment, adaptive.manifest,
                                           adaptive.directory, "2026-09-21T00:00:00+00:00")
    assert not adaptive.directory.exists()


def test_queued_old_pin_uses_only_verified_retained_release(adaptive, tmp_path):
    from quant_company.research.releases import activate_release, prepare_release

    source = adaptive.config.company_repo
    bundle = tmp_path / "old-company.bundle"
    git(source, "bundle", "create", str(bundle), "HEAD")
    releases = tmp_path / "releases"
    releases.mkdir()
    first = prepare_release(source_snapshot=bundle, snapshot_sha256=sha_file(bundle),
                            commit=adaptive.config.company_commit, release_root=releases, config=adaptive.config)
    active_path = tmp_path / "active-worker.json"
    old = activate_release(first, active_config=active_path)
    # Prepare an actual newer immutable company release; the queued assignment
    # remains bound to the old company commit and is never rewritten.
    newer = tmp_path / "new-company"
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(source), str(newer)], check=True)
    (newer / "reviewed-release.txt").write_text("operator fixture release change\n")
    git(newer, "add", ".")
    git(newer, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
        "-c", "core.hooksPath=/dev/null", "commit", "-qm", "Newer polling release")
    commit = git(newer, "rev-parse", "HEAD")
    bundle = tmp_path / "new-company.bundle"
    git(newer, "bundle", "create", str(bundle), "HEAD")
    second = prepare_release(source_snapshot=bundle, snapshot_sha256=sha_file(bundle), commit=commit,
                             release_root=releases, config=old)
    current = activate_release(second, active_config=active_path)
    assert current.company_commit != adaptive.assignment.manifest.company_commit
    Worker(current, adaptive.client).step()
    eventually(lambda: (adaptive.directory / "result.json").exists(), timeout=10)
    Worker(current, adaptive.client).step()
    assert len(adaptive.children) == 1 and len(adaptive.transport.uploads) == 1
    stored = WorkerConfig.from_file(adaptive.directory / "execution-config.json")
    assert stored.company_repo == old.company_repo and stored.company_commit == old.company_commit
    assert read_json(current.release_registry_file)["releases"] == {
        first.commit: str(first.config_path), second.commit: str(second.config_path),
    }
    with zipfile.ZipFile(adaptive.directory / "artifact.zip") as archive:
        receipt = AdaptiveExecutionReceipt.model_validate_json(archive.read("receipt.json"))
        assert receipt.company_commit == old.company_commit


def test_cancel_during_bundle_wait_never_rearms_stale_run(adaptive):
    adaptive.transport.lose_download = True
    with pytest.raises(httpx.ReadTimeout):
        adaptive.worker().step()
    adaptive.transport.assignment = adaptive.assignment.model_copy(update={"action": "cancel"})
    adaptive.worker().step()
    adaptive.transport.assignment = adaptive.assignment
    adaptive.worker().step()
    assert not adaptive.children
    assert adaptive.transport.updates[-1]["state"] == "cancelled"
