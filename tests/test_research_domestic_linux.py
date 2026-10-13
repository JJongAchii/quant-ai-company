"""Opt-in real 3070/bubblewrap qualification; generated data, no scientific claim."""

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from quant_company.research.adaptive_contracts import AdaptiveAssignment, digest_model
from quant_company.research.adaptive_executor import execute_adaptive
from quant_company.research.adaptive_report import validate_adaptive_bundle
from quant_company.research.worker import WorkerConfig, atomic_json, sha_file, utc_now

from .test_research_conditional import prepare_conditional_case
from .test_research_domestic_profile import prepare_case


@pytest.mark.skipif(sys.platform != "linux" or not os.environ.get("RESEARCH_DOMESTIC_RUNTIME_PROFILE"),
                    reason="Opt-in approved 3070 and verified runtime required")
@pytest.mark.parametrize("market", ["kr_stock", "kr_etf"])
@pytest.mark.parametrize("kind", ["strategy", "claim", "replication"])
def test_domestic_profiles_in_actual_linux_sandbox(market, kind):
    company = Path(__file__).resolve().parents[1]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=company, text=True).strip()
    profile_path = Path(os.environ["RESEARCH_DOMESTIC_RUNTIME_PROFILE"]).resolve()
    runtime = json.loads(profile_path.read_text())
    runtime["profile_id"] = market.replace("_", "-") + "-research-v2"
    root = company / ".local/worker-qualification/domestic-linux" / uuid4().hex
    root.mkdir(parents=True)
    case = prepare_case(root, market, kind, runtime, head)
    manifest = case.manifest
    public = case.server.public_profile
    config = WorkerConfig(api_url="http://127.0.0.1:1", token_file=root / "unused-fixture-token",
        state_dir=root / "state", repo_source=case.code, input_source=case.inputs, evidence_repo=case.code,
        research_python=Path(sys.executable), company_repo=company, company_commit=head,
        adaptive_profiles={public.id: case.destination / "worker-profile.json"})
    assignment = AdaptiveAssignment(job_id=uuid4(), project_id=uuid4(), revision=1,
        recipe_id="kr-research-python-v2", manifest_digest=digest_model(manifest),
        approval_event_id="synthetic-engineering-only", lease_token="f" * 64, action="run", manifest=manifest)
    job = root / "job"
    job.mkdir()
    shutil.copyfile(case.prepared.bundle_path, job / "source.bundle")
    archive_sha = execute_adaptive(config, assignment, manifest, job, utc_now())
    validated = validate_adaptive_bundle(job / "artifact.zip", assignment, manifest,
                                        expected_company_commit=head, execution_profile=public)
    assert validated.receipt.fixture_only and validated.receipt.scientific_trials_added == 0
    assert validated.qualification.performance_read is False
    assert set(validated.qualification.input_files) == {"warmup.json"}
    record = {
        "schema_version": 1, "kind": "actual-3070-domestic-synthetic-engineering-qualification",
        "market": market, "evaluation_kind": kind, "company_commit": head,
        "research_base_commit": manifest.spec.code.base_commit, "research_code_commit": case.prepared.commit,
        "runtime_profile_sha256": sha_file(profile_path), "execution_profile_digest": digest_model(public),
        "hostname": platform.node(), "gpu": validated.receipt.gpu,
        "sandbox_mocked": False, "host_check_mocked": False, "fixture_only": True,
        "scientific_trials_added": 0, "real_snapshot_reads": 0, "archive_path": str(job / "artifact.zip"),
        "archive_sha256": archive_sha, "qualification_sha256": validated.receipt.qualification_sha256,
        "sandbox_qualification_sha256": validated.receipt.sandbox_qualification_sha256,
        "sandbox_evaluation_sha256": validated.receipt.sandbox_evaluation_sha256,
        "both_phases_exit_zero": True, "strict_bundle_consumer_passed": True,
        "real_slack": False, "real_temporal": False, "completed_at": utc_now(),
    }
    atomic_json(company / f".local/worker-qualification/domestic-{market}-{kind}.json", record)


@pytest.mark.skipif(sys.platform != "linux" or not os.environ.get("RESEARCH_DOMESTIC_RUNTIME_PROFILE"),
                    reason="Opt-in approved 3070 and verified runtime required")
def test_conditional_protected_engine_in_actual_linux_sandbox():
    company = Path(__file__).resolve().parents[1]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=company, text=True).strip()
    profile_path = Path(os.environ["RESEARCH_DOMESTIC_RUNTIME_PROFILE"]).resolve()
    runtime = json.loads(profile_path.read_text())
    runtime["profile_id"] = "kr-etf-research-v2"
    root = company / ".local/worker-qualification/conditional-linux" / uuid4().hex
    root.mkdir(parents=True)
    case = prepare_conditional_case(root, runtime, head)
    manifest, public = case.manifest, case.server.public_profile
    config = WorkerConfig(api_url="http://127.0.0.1:1", token_file=root / "unused-fixture-token",
        state_dir=root / "state", repo_source=case.code, input_source=case.inputs, evidence_repo=case.code,
        research_python=Path(sys.executable), company_repo=company, company_commit=head,
        adaptive_profiles={public.id: case.destination / "worker-profile.json"})
    assignment = AdaptiveAssignment(job_id=uuid4(), project_id=uuid4(), revision=1, recipe_id=manifest.id,
        manifest_digest=digest_model(manifest), approval_event_id="synthetic-engineering-only",
        lease_token="f" * 64, action="run", manifest=manifest)
    job = root / "job"
    job.mkdir()
    shutil.copyfile(case.prepared.bundle_path, job / "source.bundle")
    archive_sha = execute_adaptive(config, assignment, manifest, job, utc_now())
    validated = validate_adaptive_bundle(job / "artifact.zip", assignment, manifest,
                                        expected_company_commit=head, execution_profile=public)
    scope = manifest.spec.research_scope
    assert all(value.research_scope == scope for value in (validated.receipt, validated.qualification, validated.result))
    assert set(validated.qualification.input_files) == {"warmup.json"}
    assert validated.receipt.fixture_only and validated.receipt.scientific_trials_added == 0
    assert validated.qualification.producer_sha256 == sha_file(job / "producer-qualification.json")
    assert validated.result.producer_sha256 == sha_file(job / "producer-result.json")
    atomic_json(company / ".local/worker-qualification/conditional-etf-strategy.json", {
        "schema_version": 1, "kind": "actual-3070-conditional-synthetic-engineering-qualification",
        "company_commit": head, "runtime_profile_sha256": sha_file(profile_path),
        "execution_profile_digest": digest_model(public), "research_scope": scope.model_dump(mode="json"),
        "protected_engine_sha256": public.entrypoint_sha256,
        "hostname": platform.node(), "gpu": validated.receipt.gpu, "sandbox_mocked": False,
        "host_check_mocked": False, "fixture_only": True, "scientific_trials_added": 0, "real_snapshot_reads": 0,
        "archive_path": str(job / "artifact.zip"), "archive_sha256": archive_sha,
        "qualification_sha256": validated.receipt.qualification_sha256, "result_sha256": validated.receipt.result_sha256,
        "producer_qualification_sha256": validated.qualification.producer_sha256,
        "producer_result_sha256": validated.result.producer_sha256,
        "sandbox_qualification_sha256": validated.receipt.sandbox_qualification_sha256,
        "sandbox_evaluation_sha256": validated.receipt.sandbox_evaluation_sha256,
        "both_phases_exit_zero": True, "strict_bundle_consumer_passed": True, "real_slack": False,
        "real_temporal": False, "protected_engine_unchanged": True, "completed_at": utc_now(),
    })
