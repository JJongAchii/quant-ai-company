"""Actual pinned qlab API checks over synthetic Git/file fixtures, not research verdicts."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError as ModelValidationError

from quant_company.research.adaptive_report import build_adaptive_report
from quant_company.research.audit import (
    AuditBinding,
    AuditTrialBinding,
    QlabProfile,
    audit_context,
    audit_publication,
    prepare_audit_package,
    verify_audit_package,
    write_audit,
)
from quant_company.research.causal_evidence import _validated_reference
from quant_company.research.mission_contracts import EvidenceRef
from quant_company.research.report import ValidationError

from .test_research_adaptive_report import history_for, producer, sha, validate


@pytest.fixture
def qlab_profile():
    root = Path(os.environ.get("QUANT_TEST_QLAB_ROOT", ".local/trusted-qlab"))
    if not (root / "core/src/qlab/audits/record.py").is_file():
        pytest.skip("Real pinned qlab checkout required: QUANT_TEST_QLAB_ROOT")
    local_python = Path(".local/qlab-venv/bin/python")
    python = Path(os.environ.get("QUANT_TEST_QLAB_PYTHON", str(local_python if local_python.exists() else sys.executable)))
    commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    return QlabProfile(root.resolve(), commit, python.absolute())


def binding_for(trial):
    return AuditBinding(
        mission_id=trial.manifest.mission_id, revision=2, mission_digest=trial.manifest.mission_digest,
        validator_request_id="synthetic-validator-request-1", trials=(AuditTrialBinding(
            trial_id=trial.manifest.trial_id, plan_digest=trial.manifest.plan_digest,
            outcome_digest="8" * 64, archive_sha256=trial.archive_sha256,
            implementer_request_id="synthetic-engineer-request-1",
        ),),
    )


def make_package(tmp_path, qlab_profile):
    trial = validate(producer(tmp_path))
    binding = binding_for(trial)
    package = prepare_audit_package(tmp_path / "package", (trial,), mission_spec=trial.manifest.spec,
                                    binding=binding, qlab_profile=qlab_profile, history=history_for(trial))
    return trial, binding, package


def markdown_for(package, **changes):
    fields = {
        "judge": "leak-auditor", "target": ".", "verdict": "pass", "issued": "2026-09-21",
        "scope": sorted(package.scope_files), "scope_digest": package.scope_digest,
        "objective_digest": package.objective_digest, "findings": [],
    }
    fields.update(changes)
    # JSON is a strict YAML subset; qlab remains the only audit parser.
    return "---\n" + json.dumps(fields, indent=2) + "\n---\n# Synthetic engineering fixture\n\nNo research judgment.\n"


def write(package, binding, profile, **changes):
    return write_audit(package.root, markdown_for(package, **changes), expected=binding, qlab_profile=profile,
                       issued=date(2026, 9, 21), rendered_at=datetime(2026, 9, 21, tzinfo=UTC))


def test_qualification_actual_git_files_qlab_to_verified_publication(tmp_path, qlab_profile):
    trial, binding, package = make_package(tmp_path, qlab_profile)
    assert len(package.objective_digest) == len(package.scope_digest) == 12
    assert package.qlab_commit == qlab_profile.commit
    assert all((package.root / name).stat().st_mode & 0o222 == 0 for name in package.scope_files)
    assert not (tmp_path / "executed-candidate").exists()
    chunks = audit_context(package, chunk_chars=73)
    textual = {entry["path"] for entry in chunks if entry["encoding"] == "utf-8"}
    assert "scope/mission.json" in textual and "scope/objective.json" in textual
    assert f"scope/trials/{trial.manifest.trial_id}/code/evaluator.py" in textual
    assert all(len(entry.get("text", "")) <= 73 for entry in chunks)
    assert any(entry["encoding"] == "binary" for entry in chunks)
    audit = write(package, binding, qlab_profile)
    verified = verify_audit_package(package.root, audit, expected=binding, qlab_profile=qlab_profile)
    result = build_adaptive_report((trial,), audit=verified, history=history_for(trial))
    assert result["summary"]["performance_visible"] is True
    assert result["summary"]["audit"]["qlab_commit"] == qlab_profile.commit
    assert result["summary"]["trials"][0]["selection"] == "best / last"
    assert result["summary"]["trials"][0]["stress"]["absolute_cagr"] == trial.stress.absolute_cagr
    assert result["html"].count("<svg ") == 2
    assert "-6.00%" in result["html"] and "<script>" not in result["html"]
    assert "2024-02-29" in result["html"] and "벤치마크·초과수익은 미측정" in result["html"]
    json.dumps(result["summary"], allow_nan=False)
    content = result["html"].encode()
    (package.root / "report.html").write_bytes(content)
    (package.root / "report.html").chmod(0o444)
    publication = audit_publication(
        verified, trial.manifest.trial_id, source_id="fixture:published-report",
        report=EvidenceRef(path="report.html", sha256=sha(content)), published_at=datetime.now(UTC),
    )
    assert publication.verifier_role == "validator"
    assert publication.outcome_digest == binding.trials[0].outcome_digest
    assert publication.audit_files[publication.verification.path] == publication.verification.sha256
    assert publication.model_validate_json(publication.model_dump_json(), strict=True) == publication
    # Same immutable package, audit and receipt replay is idempotent.
    assert prepare_audit_package(package.root, (trial,), mission_spec=trial.manifest.spec,
                                binding=binding, qlab_profile=qlab_profile, history=history_for(trial)) == package
    assert write(package, binding, qlab_profile) == audit
    assert verify_audit_package(package.root, audit, expected=binding, qlab_profile=qlab_profile).public_receipt() == verified.public_receipt()


def test_operator_supplements_are_immutable_hash_bound_scope(tmp_path, qlab_profile):
    trial = validate(producer(tmp_path))
    binding = binding_for(trial)
    package = prepare_audit_package(
        tmp_path / "package", (trial,), mission_spec=trial.manifest.spec, binding=binding,
        qlab_profile=qlab_profile, history=history_for(trial),
        supplements={"source/contract.json": b'{"causal":true}\n'},
    )
    name = "scope/supplements/source/contract.json"
    assert package.supplement_files == (name,)
    assert package.scope_files[name] == sha(b'{"causal":true}\n')
    assert (package.root / name).stat().st_mode & 0o222 == 0
    metadata = json.loads((package.root / "package.json").read_text())
    assert metadata["schema_version"] == 2 and metadata["supplement_files"] == [name]
    target = package.root / name
    target.chmod(0o644)
    target.write_text('{"causal":false}\n')
    target.chmod(0o444)
    with pytest.raises(ValidationError, match="audit_scope_content_changed"):
        verify_audit_package(package.root, package.root / "missing.md", expected=binding,
                             qlab_profile=qlab_profile)


def test_committed_krx_causality_receipt_is_self_bound_and_contains_no_performance_values():
    evidence, receipt = _validated_reference()
    assert set(evidence) == {
        "krx-etf-source-contract.json", "check-krx-etf-causality.py",
        "krx-etf-causality-receipt.json",
    }
    assert receipt["checks"]["performance_values_recorded"] is False
    assert not {"returns", "metrics", "cagr", "drawdown", "score"}.intersection(receipt)


def test_independence_requires_distinct_requests_and_registered_role(tmp_path):
    trial = validate(producer(tmp_path))
    payload = binding_for(trial).model_dump()
    payload["validator_request_id"] = payload["trials"][0]["implementer_request_id"]
    with pytest.raises(ModelValidationError, match="validator-cannot-be-implementer"):
        AuditBinding.model_validate(payload)
    payload["validator_request_id"] = "separate"
    payload["validator_role"] = "engineer"
    with pytest.raises(ModelValidationError):
        AuditBinding.model_validate(payload)


def test_bool_or_missing_audit_cannot_authorize_metrics(tmp_path, qlab_profile):
    trial, binding, package = make_package(tmp_path, qlab_profile)
    with pytest.raises(ValidationError, match="verified_audit_required"):
        build_adaptive_report((trial,), audit=True, history=history_for(trial))
    with pytest.raises(ValidationError, match="audit_file_unavailable"):
        verify_audit_package(package.root, package.root / "audits/AUDIT-leak-auditor-20260921-synthetic-validator-request-1.md",
                             expected=binding, qlab_profile=qlab_profile)


@pytest.mark.parametrize("change", [
    {"verdict": "unverified", "findings": [{"severity": "minor", "location": "fixture.py:1", "claim": "not verified"}]},
    {"verdict": "fail", "findings": [{"severity": "blocking", "location": "fixture.py:1", "claim": "synthetic failure"}]},
    {"findings": [{"severity": "major", "location": "fixture.py:1", "claim": "synthetic unresolved concern"}]},
])
def test_actual_nonpass_or_major_audit_withholds_metrics(tmp_path, qlab_profile, change):
    _, binding, package = make_package(tmp_path, qlab_profile)
    audit = write(package, binding, qlab_profile, **change)
    with pytest.raises(ValidationError, match="independent_audit_not_passed"):
        verify_audit_package(package.root, audit, expected=binding, qlab_profile=qlab_profile)


@pytest.mark.parametrize("change", ["scope", "objective", "target", "judge", "issued"])
def test_validator_cannot_change_scope_objective_or_identity(tmp_path, qlab_profile, change):
    _, binding, package = make_package(tmp_path, qlab_profile)
    fields = {
        "scope": {"scope": sorted(package.scope_files)[1:]}, "objective": {"objective_digest": "0" * 12},
        "target": {"target": "scope"}, "judge": {"judge": "overfitting-skeptic"},
        "issued": {"issued": "2026-09-20"},
    }[change]
    with pytest.raises(ValidationError):
        write(package, binding, qlab_profile, **fields)


def test_scope_change_invalidates_even_previously_verified_object(tmp_path, qlab_profile):
    trial, binding, package = make_package(tmp_path, qlab_profile)
    audit = write(package, binding, qlab_profile)
    verified = verify_audit_package(package.root, audit, expected=binding, qlab_profile=qlab_profile)
    target = package.root / "scope/objective.json"
    target.chmod(0o644)
    target.write_bytes(b"{}")
    target.chmod(0o444)
    with pytest.raises(ValidationError, match="verified_audit_expired"):
        build_adaptive_report((trial,), audit=verified, history=history_for(trial))
    with pytest.raises(ValidationError, match="audit_scope_content_changed"):
        verify_audit_package(package.root, audit, expected=binding, qlab_profile=qlab_profile)


@pytest.mark.parametrize("part", ["receipt", "html", "symlink", "extra_scope", "wrong_request"])
def test_actual_files_and_request_binding_are_required(tmp_path, qlab_profile, part):
    _, binding, package = make_package(tmp_path, qlab_profile)
    audit = write(package, binding, qlab_profile)
    if part == "wrong_request":
        binding = binding.model_copy(update={"validator_request_id": "different-authenticated-request"})
    elif part == "extra_scope":
        (package.root / "scope/unscoped.json").write_bytes(b"{}")
    else:
        target = audit.with_suffix(".receipt.json" if part == "receipt" else ".html")
        target.chmod(0o644)
        if part == "receipt":
            payload = json.loads(target.read_text())
            payload["html_sha256"] = "a" * 64
            target.write_text(json.dumps(payload))
        elif part == "html":
            target.write_text("different report")
        else:
            target.unlink()
            target.symlink_to(tmp_path / "repo/evaluator.py")
        if part != "symlink":
            target.chmod(0o444)
    with pytest.raises(ValidationError):
        verify_audit_package(package.root, audit, expected=binding, qlab_profile=qlab_profile)


def test_pinned_qlab_commit_is_verified_before_import(tmp_path, qlab_profile):
    trial = validate(producer(tmp_path))
    wrong = QlabProfile(qlab_profile.root, "a" * 40, qlab_profile.python_executable)
    with pytest.raises(ValidationError, match="trusted_qlab_commit_mismatch"):
        prepare_audit_package(tmp_path / "package", (trial,), mission_spec=trial.manifest.spec,
                             binding=binding_for(trial), qlab_profile=wrong, history=history_for(trial))


def test_all_reported_trial_versions_and_plan_must_be_bound(tmp_path, qlab_profile):
    trial = validate(producer(tmp_path))
    binding = binding_for(trial)
    wrong = binding.model_copy(update={"trials": (binding.trials[0].model_copy(update={"trial_id": uuid4()}),)})
    with pytest.raises(ValidationError, match="reported_trial_scope_mismatch"):
        prepare_audit_package(tmp_path / "package", (trial,), mission_spec=trial.manifest.spec,
                             binding=wrong, qlab_profile=qlab_profile, history=history_for(trial))
    wrong = binding.model_copy(update={"trials": (binding.trials[0].model_copy(update={"plan_digest": "a" * 64}),)})
    with pytest.raises(ValidationError, match="audit_trial_binding_mismatch"):
        prepare_audit_package(tmp_path / "package", (trial,), mission_spec=trial.manifest.spec,
                             binding=wrong, qlab_profile=qlab_profile, history=history_for(trial))


def test_report_history_cannot_change_after_audit(tmp_path, qlab_profile):
    trial, binding, package = make_package(tmp_path, qlab_profile)
    audit = write(package, binding, qlab_profile)
    verified = verify_audit_package(package.root, audit, expected=binding, qlab_profile=qlab_profile)
    with pytest.raises(ValidationError, match="reported_history_not_audited"):
        build_adaptive_report((trial,), audit=verified,
                              history=replace(history_for(trial), cumulative_scientific_trials=100))
