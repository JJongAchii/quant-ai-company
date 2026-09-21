"""Opt-in complete adaptive I/O qualification on the registered 3070.

Run from a clean committed company checkout:
  RESEARCH_SANDBOX_SMOKE_PROFILE=/absolute/profile.json \
    uv run --frozen pytest -q tests/test_research_adaptive_linux.py

Only HTTP transport is simulated. The opted-in case uses the actual detached
executor, actual host/GPU check, actual bwrap and actual evidence consumer. Its
stdlib driver emits synthetic zero-return protocol rows, never economic research.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import platform
import runpy
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from quant_company.research.adaptive_contracts import (
    AdaptiveAssignment,
    AdaptiveExecutionProfile,
    AdaptiveManifest,
    AdaptiveQualification,
    AdaptiveResult,
    digest_model,
    record_digest,
)
from quant_company.research.adaptive_executor import AdaptiveProfile, validate_result_files
from quant_company.research.mission_contracts import MissionSpec, TrialPlan
from quant_company.research.sandbox import profile_from_dict
from quant_company.research.worker import (
    Worker,
    WorkerConfig,
    atomic_json,
    identity_alive,
    process_identity,
    read_json,
    sha_file,
    signal_group,
    utc_now,
)
from quant_company.research.workspace import TextPatch, prepare_workspace
from tests.test_research_adaptive_worker import Transport, git, snapshot

ROOT = Path(__file__).resolve().parents[1]
PROFILE_ENV = "RESEARCH_SANDBOX_SMOKE_PROFILE"

# No third-party libraries, provider calls or economic implementation. These
# functions are also contract-tested locally without launching a research job.
DRIVER = r'''
import argparse
import csv
import hashlib
import json
import math
import os
import sys
from datetime import date, datetime
from pathlib import Path


def dump(path, value):
    path.write_text(json.dumps(value, sort_keys=True, allow_nan=False) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows_contract(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError('empty-sample')
    previous = None
    for row in rows:
        if set(row) != {'date', 'observed_at', 'value'}:
            raise ValueError('fields-mismatch')
        day = date.fromisoformat(row['date'])
        instant = datetime.fromisoformat(row['observed_at'])
        if day.isoformat() != row['date'] or instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError('date-contract')
        if instant.date() != day or (previous is not None and day <= previous):
            raise ValueError('date-order')
        if type(row['value']) not in (int, float) or not math.isfinite(row['value']):
            raise ValueError('non-finite-sample')
        previous = day
    return rows


def qualification_document(manifest, rows):
    rows_contract(rows)
    for invalid in ([], [dict(rows[0], value=float('nan'))], [dict(rows[0], value=float('inf'))]):
        try:
            rows_contract(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError('qualification-negative-probe-failed')
    if json.loads(json.dumps(rows, allow_nan=False)) != rows:
        raise AssertionError('json-roundtrip')
    start = date.fromisoformat(manifest['plan']['development']['start'])
    if any(date.fromisoformat(row['date']) >= start for row in rows):
        raise ValueError('warmup-only')
    return {
        'schema_version':1, 'kind':'adaptive_qualification',
        'trial_id':manifest['trial_id'], 'plan_digest':manifest['plan_digest'],
        'code_commit':manifest['plan']['code_commit'], 'config_files':manifest['plan']['config_files'],
        'input_files':{'warmup/rows.json':manifest['plan']['input_files']['warmup/rows.json']},
        'sample_count':len(rows), 'json_dates':[row['date'] for row in rows],
        'json_datetimes':[row['observed_at'] for row in rows],
        'typed_schema':{'date':'date','observed_at':'datetime','value':'float'},
        'primary_unit':'fraction-per-year', 'empty_sample_rejected':True,
        'non_finite_rejected':True, 'json_roundtrip_passed':True,
        'performance_read':False, 'sealed_read':False,
    }


def write_result(manifest, rows, output, isolation):
    rows_contract(rows)
    if len(rows) < 2:
        raise ValueError('dated-series-requires-two-observations')
    files = {}
    for cost in ('base', 'stress'):
        path = output / (cost + '.csv')
        with path.open('w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['date', 'net_return'])
            writer.writerows((row['date'], 0.0) for row in rows)
        files[path.name] = sha(path)
    dump(output / 'isolation.json', isolation)
    files['isolation.json'] = sha(output / 'isolation.json')
    references = {cost:{'path':cost+'.csv', 'sha256':files[cost+'.csv'],
                        'frequency':'monthly', 'unit':'fraction-per-period'} for cost in ('base', 'stress')}
    value = {
        'schema_version':1, 'kind':'adaptive_result', 'trial_id':manifest['trial_id'],
        'plan_digest':manifest['plan_digest'], 'code_commit':manifest['plan']['code_commit'],
        'metrics':{
            'primary':{'metric':'stress-net-absolute-cagr','direction':'maximize',
                       'unit':'fraction-per-year','value':0.0},
            'risks':{'max_drawdown':0.0}, 'sample_count':len(rows),
            'sample_window':manifest['plan']['development'],
        },
        'base_returns':references['base'], 'stress_returns':references['stress'], 'output_files':files,
    }
    dump(output / 'result.json', value)
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('qualify', 'evaluate'))
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from model import FIXTURE_REVISION
    assert FIXTURE_REVISION == 2, 'patched-committed-code-not-consumed'
    assert Path.cwd() == Path('/code') and Path.home() == Path('/home/sandbox')
    assert json.loads(args.config.read_text()) == {'fixture_only':True}
    assert not any(name in os.environ for name in (
        'RESEARCH_SANDBOX_SMOKE_PROFILE', 'AWS_SECRET_ACCESS_KEY', 'OPENAI_API_KEY', 'SLACK_BOT_TOKEN',
    ))
    for absent in ('/code/.git', '/code/.env', '/code/history/prior.json', '/home/achii', '/root/.ssh', '/inputs/sealed'):
        assert not Path(absent).exists(), 'unapproved-path-visible'
    visible = sorted(path.relative_to('/inputs').as_posix() for path in Path('/inputs').rglob('*') if path.is_file())
    expected = ['__contract__/manifest.json', 'warmup/rows.json']
    if args.action == 'evaluate':
        expected.append('dev/rows.json')
    assert visible == sorted(expected), 'phase-input-mount-mismatch'
    for readonly in (Path('/code/driver.py'), Path('/code/model.py'), Path('/inputs/warmup/rows.json')):
        try:
            with readonly.open('a') as stream:
                stream.write('forbidden-write')
        except OSError:
            pass
        else:
            raise AssertionError('readonly-mount-writable')
    manifest = json.loads(args.manifest.read_text())
    isolation = {
        'fixture_only':True, 'phase':args.action, 'python':sys.version,
        'network_namespace':os.readlink('/proc/self/ns/net'), 'pid_namespace':os.readlink('/proc/self/ns/pid'),
        'visible_inputs':visible, 'patched_fixture_revision':FIXTURE_REVISION,
        'history_hidden':True, 'home_hidden':True, 'parent_environment_hidden':True,
        'code_readonly':True, 'inputs_readonly':True,
    }
    if args.action == 'qualify':
        rows = json.loads(Path('/inputs/warmup/rows.json').read_text())
        dump(args.output / 'qualification.json', qualification_document(manifest, rows))
        dump(args.output / 'isolation.json', isolation)
    else:
        rows = json.loads(Path('/inputs/dev/rows.json').read_text())
        write_result(manifest, rows, args.output, isolation)


if __name__ == '__main__':
    main()
'''


def build_fixture(root: Path, runtime: dict, *, company_repo: Path, company_commit: str):
    source_bundle, base = snapshot(root / "research-source", {
        "driver.py": DRIVER, "model.py": "FIXTURE_REVISION = 1\n", "config.json": '{"fixture_only":true}\n',
        ".env": "SYNTHETIC_HIDDEN_FIXTURE=not-mounted\n",
        "history/prior.json": '{"fixture_only":true,"must_not_be_mounted":true}\n',
    })
    prepared = prepare_workspace(
        source_bundle, sha_file(source_bundle), base, root / "prepared", ("model.py",),
        ("driver.py", "config.json"), (TextPatch("model.py", "FIXTURE_REVISION = 1\n", "FIXTURE_REVISION = 2\n"),),
    )
    inputs = root / "inputs"
    samples = {
        "warmup/rows.json": [{"date": "2019-12-01", "observed_at": "2019-12-01T00:00:00+00:00", "value": 1.0}],
        "dev/rows.json": [{"date": day, "observed_at": day + "T00:00:00+00:00", "value": 1.0}
                          for day in ("2020-01-01", "2020-02-01", "2020-03-01")],
    }
    input_files = {}
    for name, rows in samples.items():
        path = inputs / name
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, rows)
        input_files[name] = sha_file(path)
    # Runtime bytes and mounts remain operator-provisioned; only the public ID is
    # scoped to this standalone adaptive fixture, not a production profile file.
    runtime = {**runtime, "profile_id": "kr-etf-monthly-python-v1"}
    profile_from_dict(runtime)
    code_files = {name: prepared.manifest["files"][name] for name in ("driver.py", "model.py", "config.json")}
    public = AdaptiveExecutionProfile(
        id=runtime["profile_id"], entrypoint="driver.py", entrypoint_sha256=code_files["driver.py"],
        protected_paths=["driver.py", "config.json"], code_paths=list(code_files),
        python_executable=runtime["python_executable"], python_sha256=runtime["python_sha256"],
        runtime_mounts=[{"target": mount["target"], "sha256": mount["sha256"]} for mount in runtime["mounts"]],
        qualification_input_names=["warmup/rows.json"], evaluation_input_names=list(input_files),
        qualification_timeout_seconds=30, evaluation_timeout_seconds=30, fixture_only=True,
    )
    spec = MissionSpec(
        title="Real Linux adaptive transport fixture; no economic research", kind="strategy",
        objective={"metric": "stress-net-absolute-cagr", "direction": "maximize", "unit": "fraction-per-year"},
        base_cost_bps=10.0, stress_cost_bps=30.0, risk_constraints=[],
        development={"start": "2020-01-01", "end": "2020-03-01"},
        sealed=[{"start": "2021-01-01", "end": "2021-12-31"}],
        data={"lake_id": "synthetic-adaptive-linux-fixture", "input_files": input_files},
        code={"repository": "quant-lab", "base_commit": base, "write_paths": ["model.py"]},
        allowed_changes=["implementation"], execution_profile=public.id, execution_profile_digest=digest_model(public),
        resources={"worker_id": "worker", "priority": "owner"},
        search={"max_trials_per_cycle": 2, "patience": 2, "min_improvement": 0.0, "continuous": False},
        baseline_source_ids=["fixture:synthetic-linux-protocol-not-research"],
    )
    plan = TrialPlan(
        trial_id=uuid4(), proposal_id=uuid4(), mission_digest=record_digest(spec), execution_profile=public.id,
        implementer="engineer", repository="quant-lab", code_commit=prepared.commit, changed_paths=["model.py"],
        config_files={"config.json": code_files["config.json"]}, input_files=input_files,
        lake_id=spec.data.lake_id, development=spec.development, worker_id="worker",
        hostname="DESKTOP-5T00NAF", gpu="NVIDIA GeForce RTX 3070",
    )
    manifest = AdaptiveManifest(
        mission_id=uuid4(), mission_digest=record_digest(spec), trial_id=plan.trial_id, plan_digest=record_digest(plan),
        plan=plan, spec=spec, code_files=code_files, bundle_sha256=prepared.bundle_sha256,
        config_path="config.json", company_commit=company_commit,
    )
    assignment = AdaptiveAssignment(
        job_id=uuid4(), project_id=uuid4(), revision=1, manifest_digest=digest_model(manifest),
        approval_event_id="synthetic-opt-in-linux-engineering-fixture", lease_token="b" * 64,
        action="run", manifest=manifest,
    )
    local_profile = AdaptiveProfile(public_profile=public, runtime=runtime,
                                    input_sources={name: inputs / name for name in input_files}, allowed_input_roots=[inputs])
    profile_path = root / "adaptive-profile.json"
    atomic_json(profile_path, local_profile.model_dump(mode="json"))
    token = root / "synthetic-token.txt"
    token.write_text("synthetic-local-transport-token\n")
    config = WorkerConfig(
        api_url="http://127.0.0.1:18764", token_file=token, state_dir=root / "state",
        repo_source=root / "unused-research-source", input_source=inputs, evidence_repo=root / "unused-evidence",
        research_python=Path(sys.executable), company_repo=company_repo, company_commit=company_commit,
        adaptive_profiles={public.id: profile_path}, heartbeat_seconds=0.2,
    )
    return SimpleNamespace(config=config, public_profile=public, assignment=assignment, manifest=manifest,
                           prepared=prepared, samples=samples, input_files=input_files,
                           job=config.state_dir / "jobs" / str(assignment.job_id))


@pytest.fixture
def local_protocol(tmp_path):
    runtime = {
        "profile_id": "engineering-namespace-smoke-v1", "python_executable": "/runtime/python",
        "python_sha256": "a" * 64, "mounts": [{"source": str(tmp_path), "target": "/runtime", "sha256": "a" * 64}],
        "allowed_roots": [str(tmp_path)], "bwrap_executable": "/usr/bin/bwrap",
    }
    fixture = build_fixture(tmp_path, runtime, company_repo=ROOT, company_commit="a" * 40)
    driver = fixture.prepared.worktree / "driver.py"
    namespace = runpy.run_path(str(driver), run_name="synthetic_protocol_unit_test")
    return fixture, namespace, tmp_path


def test_stdlib_fixture_driver_matches_typed_qualification_and_result(local_protocol):
    fixture, driver, root = local_protocol
    ast.parse(DRIVER)
    manifest = fixture.manifest.model_dump(mode="json")
    value = driver["qualification_document"](manifest, fixture.samples["warmup/rows.json"])
    qualification = AdaptiveQualification.model_validate_json(json.dumps(value))
    assert qualification.sample_count == 1 and not qualification.performance_read
    assert qualification.primary_unit == "fraction-per-year"
    assert qualification.plan_digest == fixture.manifest.plan_digest
    output = root / "local-protocol-output"
    output.mkdir()
    result = driver["write_result"](manifest, fixture.samples["dev/rows.json"], output,
                                     {"fixture_only": True, "unit_contract_test": True})
    assert AdaptiveResult.model_validate_json(json.dumps(result)) == validate_result_files(output, fixture.manifest)
    assert set(fixture.manifest.code_files) == {"driver.py", "model.py", "config.json"}
    assert {"history/prior.json", ".env"} <= set(fixture.prepared.manifest["files"])


@pytest.mark.parametrize("bad_rows", [
    [], [{"date": "2019-12-01", "observed_at": "2019-12-01T00:00:00+00:00", "value": float("nan")}],
    [{"date": "2019-12-01", "observed_at": "2019-12-01T00:00:00+00:00", "value": float("inf")}],
    [{"date": "2019-12-01T00:00:00Z", "observed_at": "2019-12-01T00:00:00+00:00", "value": 1.0}],
    [{"date": "2019-12-01", "observed_at": "2019-12-01T00:00:00", "value": 1.0}],
    [{"date": "2019-12-01", "observed_at": "2019-12-01T00:00:00+00:00", "value": True}],
])
def test_stdlib_fixture_qualification_rejects_invalid_sample_contract(local_protocol, bad_rows):
    fixture, driver, _ = local_protocol
    with pytest.raises(ValueError):
        driver["qualification_document"](fixture.manifest.model_dump(mode="json"), bad_rows)


@pytest.mark.skipif(platform.system() != "Linux" or not os.environ.get(PROFILE_ENV),
                    reason="Requires explicit operator runtime profile on the registered Linux 3070")
def test_actual_linux_adaptive_qualification_through_detached_worker():
    from quant_company.research.adaptive_report import validate_adaptive_bundle

    assert platform.node() == "DESKTOP-5T00NAF", "Only the explicitly registered 3070 is authorized"
    assert not git(ROOT, "status", "--porcelain", "--untracked-files=all"), "Stage committed clean code first"
    company_commit = git(ROOT, "rev-parse", "HEAD")
    runtime_path = Path(os.environ[PROFILE_ENV]).resolve(strict=True)
    runtime = read_json(runtime_path)
    profile_from_dict(runtime)
    evidence_root = ROOT / ".local" / "worker-qualification"
    run_root = evidence_root / "adaptive-linux" / uuid4().hex
    run_root.mkdir(parents=True, mode=0o700)
    summary_path = evidence_root / "adaptive-linux.json"
    run_receipt = run_root / "qualification-receipt.json"
    recorded = {
        "schema_version": 1, "kind": "synthetic-adaptive-linux-engineering-qualification",
        "status": "started", "fixture_only": True, "scientific_trials_added": 0,
        "started_at": utc_now(), "hostname": platform.node(), "implementation_commit": company_commit,
        "runtime_profile_path": str(runtime_path), "runtime_profile_sha256": sha_file(runtime_path),
        "run_directory": str(run_root), "http_transport": "httpx.MockTransport",
        "real_slack": False, "real_temporal": False, "host_check_mocked": False, "sandbox_mocked": False,
    }
    atomic_json(run_receipt, recorded)
    atomic_json(summary_path, recorded)
    workers = []
    fixture = None
    try:
        fixture = build_fixture(run_root, runtime, company_repo=ROOT, company_commit=company_commit)
        transport = Transport(fixture.assignment, fixture.prepared.bundle_path.read_bytes())
        transport.lose_upload = True
        host_net = os.readlink("/proc/self/ns/net")
        host_pid = os.readlink("/proc/self/ns/pid")
        with httpx.Client(base_url=fixture.config.api_url, transport=httpx.MockTransport(transport),
                          trust_env=False, follow_redirects=False) as client:
            lost_ack = False
            deadline = time.monotonic() + 90
            while not (fixture.job / "state.json").exists() or not read_json(fixture.job / "state.json").get("uploaded"):
                assert time.monotonic() < deadline, "Actual adaptive fixture did not finish within its engineering budget"
                worker = Worker(fixture.config, client)
                workers.append(worker)
                try:
                    worker.step()
                except httpx.ReadTimeout:
                    assert transport.uploads, "Only the post-upload response is deliberately lost"
                    assert not lost_ack, "Exactly one response ambiguity is injected"
                    lost_ack = True
                terminal = fixture.job / "result.json"
                if terminal.exists():
                    assert read_json(terminal)["state"] == "ready", read_json(terminal).get("reason", "missing-state")
                if read_json(fixture.job / "state.json").get("uncertain_reason"):
                    pytest.fail(read_json(fixture.job / "state.json")["uncertain_reason"])
                time.sleep(0.05)
            assert lost_ack and len(transport.uploads) == 2
            assert transport.uploads[0] == transport.uploads[1]
            assert len(transport.downloads) == 1
            process_before = sha_file(fixture.job / "process.json")
            intent_before = sha_file(fixture.job / "launch-intent.json")
            for _ in range(3):
                Worker(fixture.config, client).step()
            assert len(transport.uploads) == 2 and sha_file(fixture.job / "process.json") == process_before
            assert sha_file(fixture.job / "launch-intent.json") == intent_before
            assert sum(len(worker.children) for worker in workers) == 1
        archive = fixture.job / "artifact.zip"
        validated = validate_adaptive_bundle(archive, fixture.assignment, fixture.manifest,
                                              expected_company_commit=company_commit,
                                              execution_profile=fixture.public_profile)
        assert validated.receipt.fixture_only and validated.receipt.scientific_trials_added == 0
        assert validated.receipt.company_commit == company_commit
        assert validated.receipt.hostname == platform.node() and validated.receipt.gpu == "NVIDIA GeForce RTX 3070"
        assert validated.receipt.code_commit == fixture.prepared.commit
        assert validated.receipt.execution_count == 1
        assert validated.qualification.sample_count == 1 and validated.result.metrics.sample_count == 3
        isolation = {phase: read_json(fixture.job / phase / "isolation.json") for phase in ("qualify", "evaluate")}
        for phase, actual in isolation.items():
            assert actual["network_namespace"] != host_net and actual["pid_namespace"] != host_pid
            assert actual["patched_fixture_revision"] == 2
            assert all(actual[name] for name in (
                "history_hidden", "home_hidden", "parent_environment_hidden", "code_readonly", "inputs_readonly",
            ))
            expected = {"__contract__/manifest.json", "warmup/rows.json"}
            if phase == "evaluate":
                expected.add("dev/rows.json")
            assert set(actual["visible_inputs"]) == expected
        with zipfile.ZipFile(archive) as packet:
            assert "code/history/prior.json" not in packet.namelist() and "code/.env" not in packet.namelist()
            assert all(fixture.assignment.lease_token.encode() not in packet.read(name) for name in packet.namelist())
            members = {name: hashlib.sha256(packet.read(name)).hexdigest() for name in packet.namelist()}
        receipts = {
            name: {"path": str(fixture.job / name), "sha256": sha_file(fixture.job / name)}
            for name in ("launch-intent.json", "process.json", "result.json", "state.json", "execution-config.json")
        }
        recorded.update(
            status="passed", completed_at=utc_now(), archive_path=str(archive), archive_sha256=sha_file(archive),
            execution_company_commit=validated.receipt.company_commit, research_code_commit=fixture.prepared.commit,
            manifest_digest=fixture.assignment.manifest_digest, plan_digest=fixture.manifest.plan_digest,
            execution_profile_digest=digest_model(fixture.public_profile), source_bundle_sha256=fixture.prepared.bundle_sha256,
            gpu=validated.receipt.gpu, members=members, lifecycle_receipts=receipts,
            sandbox_receipts={phase: read_json(fixture.job / f"sandbox-{phase}.json") for phase in ("qualification", "evaluation")},
            isolation=isolation, assertions={
                "patched_commit_consumed": True, "real_bwrap_both_phases": True,
                "qualification_warmup_only": True, "history_and_sealed_not_mounted": True,
                "independent_bundle_consumer_passed": True, "lost_upload_ack_retried_identical_bytes": True,
                "poller_reconstructed_without_duplicate_executor": True, "same_launch_and_process_receipts": True,
                "actual_poller_process_kill_tested": False,
            },
        )
        atomic_json(run_receipt, recorded)
        atomic_json(summary_path, recorded)
    except BaseException as exc:
        recorded.update(status="failed", completed_at=utc_now(), failure_type=type(exc).__name__)
        if fixture is not None:
            recorded["job_directory"] = str(fixture.job)
            if (fixture.job / "result.json").exists():
                terminal = read_json(fixture.job / "result.json")
                recorded["executor_state"] = terminal.get("state")
                recorded["executor_reason"] = terminal.get("reason")
        atomic_json(run_receipt, recorded)
        atomic_json(summary_path, recorded)
        raise
    finally:
        # Clean up only this fixture's process group, never a production worker.
        if recorded["status"] != "passed" and fixture is not None and (fixture.job / "process.json").exists():
            identity = read_json(fixture.job / "process.json")["identity"]
            if identity_alive(identity):
                signal_group(identity, signal.SIGTERM)
        for worker in workers:
            for child in worker.children:
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    identity = process_identity(child.pid)
                    if identity is not None:
                        signal_group(identity, signal.SIGKILL)
                    child.wait(timeout=5)
