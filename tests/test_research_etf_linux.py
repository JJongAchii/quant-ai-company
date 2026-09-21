"""Opt-in actual 3070 sandbox qualification with the committed ETF evaluator and generated data."""

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from quant_company.research.adaptive_contracts import (
    AdaptiveAssignment,
    AdaptiveManifest,
    digest_model,
    record_digest,
)
from quant_company.research.adaptive_executor import AdaptiveProfile, execute_adaptive
from quant_company.research.adaptive_report import validate_adaptive_bundle
from quant_company.research.worker import WorkerConfig, atomic_json, sha_file, utc_now
from quant_company.research.workspace import TextPatch, prepare_workspace


@pytest.mark.skipif(sys.platform != "linux" or not os.environ.get("RESEARCH_ETF_TEST_REPO")
                    or not os.environ.get("RESEARCH_ETF_RUNTIME_PROFILE"),
                    reason="Opt-in real 3070, committed ETF checkout and prepared science runtime required")
def test_actual_etf_evaluator_roundtrips_parquet_in_both_sandbox_phases(monkeypatch):
    company = Path(__file__).resolve().parents[1]
    repo = Path(os.environ["RESEARCH_ETF_TEST_REPO"]).resolve()
    profile_path = Path(os.environ["RESEARCH_ETF_RUNTIME_PROFILE"]).resolve()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=company, text=True).strip()
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    assert not subprocess.check_output(["git", "diff", "HEAD", "--", "labs/company-autonomous-etf"], cwd=repo)
    root = company / ".local/worker-qualification/etf-linux" / uuid4().hex
    root.mkdir(parents=True)
    monkeypatch.syspath_prepend(str(repo / "core/src"))
    monkeypatch.syspath_prepend(str(repo / "labs/company-autonomous-etf/src"))
    fixture_path = repo / "labs/company-autonomous-etf/tests/conftest.py"
    module_spec = importlib.util.spec_from_file_location("committed_etf_fixture", fixture_path)
    fixtures = importlib.util.module_from_spec(module_spec)
    monkeypatch.setitem(sys.modules, module_spec.name, fixtures)
    module_spec.loader.exec_module(fixtures)
    generated = fixtures.stage_fixture(root / "generated", "A25")
    source = root / "base.bundle"
    subprocess.run(["git", "bundle", "create", str(source), "HEAD"], cwd=repo, check=True, capture_output=True)
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip() == base
    config_path = generated.manifest.config_path
    prepared = prepare_workspace(source, sha_file(source), base, root / "prepared", (config_path,),
        tuple(generated.profile.protected_paths),
        (TextPatch(config_path, (repo / config_path).read_text(), (generated.code / config_path).read_text()),))
    files = {name: prepared.manifest["files"][name] for name in generated.profile.code_paths}
    runtime = json.loads(profile_path.read_text())
    public = generated.profile.model_copy(update={
        "python_executable": runtime["python_executable"], "python_sha256": runtime["python_sha256"],
        "runtime_mounts": [{"target": mount["target"], "sha256": mount["sha256"]} for mount in runtime["mounts"]],
        "qualification_timeout_seconds": 120, "evaluation_timeout_seconds": 120,
    })
    public = type(public).model_validate(public.model_dump())
    spec = generated.manifest.spec.model_copy(update={"execution_profile_digest": digest_model(public)})
    plan = generated.manifest.plan.model_copy(update={
        "mission_digest": record_digest(spec), "code_commit": prepared.commit,
        "config_files": {config_path: files[config_path]}, "implementer": "engineer",
    })
    manifest = AdaptiveManifest.model_validate(generated.manifest.model_dump(mode="json") | {
        "spec": spec.model_dump(mode="json"), "plan": plan.model_dump(mode="json"),
        "mission_digest": record_digest(spec), "plan_digest": record_digest(plan),
        "bundle_sha256": prepared.bundle_sha256, "code_files": files, "company_commit": head,
    })
    operator = AdaptiveProfile(public_profile=public, runtime=runtime,
        input_sources={name: generated.inputs / name for name in public.evaluation_input_names},
        allowed_input_roots=[generated.inputs])
    operator_path = root / "operator-profile.json"
    atomic_json(operator_path, operator.model_dump(mode="json"))
    config = WorkerConfig(api_url="http://127.0.0.1:1", token_file=root / "unused-fixture-token",
        state_dir=root / "state", repo_source=repo, input_source=generated.inputs, evidence_repo=repo,
        research_python=Path(sys.executable), company_repo=company, company_commit=head,
        adaptive_profiles={public.id: operator_path})
    assignment = AdaptiveAssignment(job_id=uuid4(), project_id=uuid4(), revision=1,
        manifest_digest=digest_model(manifest), approval_event_id="synthetic-engineering-only",
        lease_token="f" * 64, action="run", manifest=manifest)
    job = root / "job"
    job.mkdir()
    shutil.copyfile(prepared.bundle_path, job / "source.bundle")
    archive_sha = execute_adaptive(config, assignment, manifest, job, utc_now())
    validated = validate_adaptive_bundle(job / "artifact.zip", assignment, manifest,
                                        expected_company_commit=head, execution_profile=public)
    assert validated.receipt.fixture_only and validated.receipt.scientific_trials_added == 0
    assert validated.qualification.performance_read is False
    assert set(validated.qualification.input_files) == set(public.qualification_input_names)
    record = {
        "schema_version": 1, "kind": "actual-3070-etf-synthetic-engineering-qualification",
        "company_commit": head, "research_base_commit": base, "research_code_commit": prepared.commit,
        "fixture_generator_sha256": sha_file(fixture_path), "runtime_profile_sha256": sha_file(profile_path),
        "execution_profile_digest": digest_model(public), "hostname": platform.node(),
        "gpu": validated.receipt.gpu, "sandbox_mocked": False, "host_check_mocked": False,
        "fixture_only": True, "scientific_trials_added": 0, "real_snapshot_reads": 0,
        "archive_path": str(job / "artifact.zip"), "archive_sha256": archive_sha,
        "qualification_sha256": validated.receipt.qualification_sha256,
        "sandbox_qualification_sha256": validated.receipt.sandbox_qualification_sha256,
        "sandbox_evaluation_sha256": validated.receipt.sandbox_evaluation_sha256,
        "both_phases_exit_zero": True, "strict_bundle_consumer_passed": True,
        "real_slack": False, "real_temporal": False, "completed_at": utc_now(),
    }
    atomic_json(company / ".local/worker-qualification/etf-linux.json", record)
