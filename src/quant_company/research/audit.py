"""Immutable company audit packages verified by operator-pinned qlab code.

This module verifies evidence and independence bindings. It does not make a leak
judgment, run candidate code, or accept a model's boolean as publication authority.
"""

from __future__ import annotations

import html
import json
import os
import re
import stat
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from ..contracts import StrictModel
from .adaptive_contracts import AdaptiveManifest, record_digest
from .adaptive_report import ReportHistory, ValidatedAdaptiveTrial, _model
from .contracts import Digest
from .mission_contracts import AuditPublication, EvidenceRef, MissionSpec, relative_path
from .report import MAX_EXPANDED_BYTES, MAX_MEMBER_BYTES, ValidationError, _json, _require, _sha256

REQUEST_ID = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
_VERIFIED = object()
AUDIT_API = "qlab.audits.record.parse_audit+validate_audit+qlab.audits.receipt.check_receipt"


class AuditModel(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AuditTrialBinding(AuditModel):
    trial_id: UUID
    plan_digest: Digest
    outcome_digest: Digest
    archive_sha256: Digest
    implementer_role: Literal["engineer"] = "engineer"
    implementer_request_id: REQUEST_ID


class AuditBinding(AuditModel):
    schema_version: Literal[1] = 1
    mission_id: UUID
    revision: int = Field(ge=1, strict=True)
    mission_digest: Digest
    validator_request_id: REQUEST_ID
    validator_role: Literal["validator"] = "validator"
    trials: Annotated[tuple[AuditTrialBinding, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def independent(self):
        if len({trial.trial_id for trial in self.trials}) != len(self.trials):
            raise ValueError("duplicate-audit-trial")
        if any(trial.implementer_request_id == self.validator_request_id for trial in self.trials):
            raise ValueError("validator-cannot-be-implementer")
        return self


@dataclass(frozen=True)
class QlabProfile:
    root: Path
    commit: str
    python_executable: Path


@dataclass(frozen=True)
class AuditPackage:
    root: Path
    binding: AuditBinding
    objective_digest: str
    scope_digest: str
    scope_files: Mapping[str, str]
    qlab_commit: str
    supplement_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerifiedAudit:
    package: AuditPackage
    audit_files: Mapping[str, str]
    verification: EvidenceRef
    _seal: object = field(repr=False, compare=False)

    def public_receipt(self) -> dict[str, Any]:
        _require(self._seal is _VERIFIED, "verified_audit_required")
        return {
            "api": AUDIT_API, "mission_id": str(self.package.binding.mission_id),
            "mission_digest": self.package.binding.mission_digest,
            "objective_digest": self.package.objective_digest, "scope_digest": self.package.scope_digest,
            "qlab_commit": self.package.qlab_commit,
            "trial_ids": [str(trial.trial_id) for trial in self.package.binding.trials],
            "audit_files": dict(self.audit_files), "verification": self.verification.model_dump(mode="json"),
        }


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _file(root: Path, relative: str, *, readonly: bool = True) -> Path:
    try:
        relative_path(relative)
        target = root
        _require(root.is_dir() and not root.is_symlink(), "unsafe_package_root")
        for part in Path(relative).parts:
            target = target / part
            _require(not target.is_symlink(), "symlink_in_audit_package")
        mode = target.stat().st_mode
        _require(stat.S_ISREG(mode), "audit_file_not_regular")
        _require(not readonly or not mode & 0o222, "audit_scope_not_readonly")
        _require(target.stat().st_size <= MAX_EXPANDED_BYTES, "audit_file_too_large")
        return target
    except (OSError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError("audit_file_unavailable") from None


def _write(root: Path, name: str, content: bytes) -> Path:
    relative_path(name)
    _require(root.is_dir() and not root.is_symlink(), "unsafe_package_root")
    directory = root
    for part in Path(name).parts[:-1]:
        directory = directory / part
        _require(not directory.is_symlink(), "symlink_in_audit_package")
        directory.mkdir(exist_ok=True)
    path = root / name
    if path.exists() or path.is_symlink():
        _require(_file(root, name).read_bytes() == content, "immutable_audit_file_conflict")
        return path
    with path.open("xb") as stream:
        stream.write(content)
    path.chmod(0o444)
    return path


def _git(profile: QlabProfile, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(profile.root), *args],
        env={"PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
             "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_REPLACE_OBJECTS": "1"},
        capture_output=True, text=True, timeout=20,
    )
    _require(result.returncode == 0, "trusted_qlab_git_unavailable")
    return result.stdout.strip()


def _check_profile(profile: QlabProfile) -> None:
    _require(isinstance(profile, QlabProfile) and re.fullmatch(r"[a-f0-9]{40}", profile.commit) is not None,
             "trusted_qlab_profile_required")
    _require(profile.root.is_absolute() and profile.python_executable.is_absolute()
             and profile.python_executable.is_file(), "trusted_qlab_profile_required")
    _require(_git(profile, "rev-parse", "HEAD") == profile.commit, "trusted_qlab_commit_mismatch")
    _require(not _git(profile, "status", "--porcelain", "--untracked-files=all", "--", "core/src"),
             "trusted_qlab_source_dirty")
    source = profile.root / "core/src/qlab"
    _require(source.is_dir() and not source.is_symlink(), "trusted_qlab_source_missing")
    _require(not any(path.is_symlink() for path in source.rglob("*")), "trusted_qlab_source_symlink")


# Only this fixed operator code runs. sys.path is isolated from the audit package,
# worker archive and caller environment. qlab owns its schema/parser/receipt APIs.
_QLAB_SCRIPT = r'''
import hashlib, json, sys
from pathlib import Path
payload = json.load(sys.stdin)
sys.path.insert(0, str(Path(payload['qlab_root']) / 'core/src'))
from qlab.audits.record import compute_scope_digest, parse_audit, validate_audit
from qlab.audits.receipt import check_receipt, read_receipt, receipt_path, write_receipt
from qlab.control.record import objective_digest
root = Path(payload['root'])
result = {'scope_digest': compute_scope_digest(root, payload['scope']),
          'objective_digest': objective_digest(root, 'scope/objective.json')}
if payload['operation'] != 'digest':
    audit = root / payload['audit']
    record = parse_audit(audit)
    result.update(judge=record.judge, target=record.target, verdict=record.verdict,
                  issued=record.issued, scope=list(record.scope),
                  audit_scope_digest=record.scope_digest,
                  audit_objective_digest=record.objective_digest,
                  findings=[{'severity': f.severity, 'location': f.location, 'claim': f.claim}
                            for f in record.findings],
                  supersedes=list(record.supersedes), conflicts_with=list(record.conflicts_with),
                  spawns_claim=list(record.spawns_claim), violations=validate_audit(record, root))
    if payload['operation'] == 'write_receipt':
        if result['violations']:
            raise ValueError('invalid audit cannot receive a report receipt')
        write_receipt(record, html_sha256=payload['html_sha256'],
                      rendered_at=payload['rendered_at'], renderer=1)
    if payload['operation'] in ('verify', 'write_receipt'):
        from dataclasses import asdict
        result['receipt_violations'] = check_receipt(record)
        result['receipt'] = asdict(read_receipt(receipt_path(audit)))
print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
'''


def _qlab(profile: QlabProfile, package: Path, scope: list[str], operation: str, **extra) -> dict[str, Any]:
    try:
        _check_profile(profile)
        result = subprocess.run(
            [str(profile.python_executable), "-I", "-B", "-c", _QLAB_SCRIPT],
            cwd=profile.root, env={"PATH": os.defpath, "PYTHONNOUSERSITE": "1"},
            input=_canonical({"qlab_root": str(profile.root), "root": str(package), "scope": scope,
                              "operation": operation, **extra}), capture_output=True, timeout=30,
        )
        _require(result.returncode == 0 and len(result.stdout) <= MAX_MEMBER_BYTES, "trusted_qlab_api_failed")
        value = _json(result.stdout, "\x00not-a-lease-token\x00")
        _require(isinstance(value, dict), "trusted_qlab_response_invalid")
        _check_profile(profile)
        return value
    except (OSError, subprocess.TimeoutExpired):
        raise ValidationError("trusted_qlab_api_unavailable") from None


def _match_trials(binding: AuditBinding, trials: tuple[ValidatedAdaptiveTrial, ...]) -> None:
    _require(len(trials) == len(binding.trials), "reported_trial_scope_mismatch")
    by_id = {trial.manifest.trial_id: trial for trial in trials}
    _require(len(by_id) == len(trials) and set(by_id) == {item.trial_id for item in binding.trials},
             "reported_trial_scope_mismatch")
    for item in binding.trials:
        trial = by_id[item.trial_id]
        _require(trial.manifest.mission_id == binding.mission_id
                 and trial.manifest.mission_digest == binding.mission_digest
                 and trial.manifest.plan_digest == item.plan_digest
                 and trial.manifest.plan.implementer == item.implementer_role
                 and trial.archive_sha256 == item.archive_sha256
                 and _sha256(trial.archive) == item.archive_sha256, "audit_trial_binding_mismatch")


def prepare_audit_package(
    destination: Path, trials: tuple[ValidatedAdaptiveTrial, ...], *, mission_spec: MissionSpec,
    binding: AuditBinding, qlab_profile: QlabProfile, history: ReportHistory,
    supplements: Mapping[str, bytes] | None = None,
) -> AuditPackage:
    """Copy validated immutable inputs; never create an audit or a pass verdict."""
    _match_trials(binding, trials)
    _require(record_digest(mission_spec) == binding.mission_digest
             and all(trial.manifest.spec == mission_spec for trial in trials), "audit_mission_scope_mismatch")
    destination = destination.absolute()
    _require(not destination.is_symlink(), "unsafe_package_root")
    destination.mkdir(parents=True, exist_ok=True)
    contents = {
        "scope/mission.json": _canonical(mission_spec.model_dump(mode="json")),
        "scope/objective.json": _canonical(mission_spec.objective.model_dump(mode="json")),
        "scope/binding.json": _canonical(binding.model_dump(mode="json")),
        "scope/history.json": _canonical(history.to_dict()),
    }
    for trial in trials:
        prefix = f"scope/trials/{trial.manifest.trial_id}/"
        contents.update({prefix + name: content for name, content in trial.contents.items()})
        contents[prefix + "archive.zip"] = trial.archive
    supplement_files = []
    for name, content in sorted((supplements or {}).items()):
        relative_path(name)
        _require(isinstance(content, bytes) and not name.startswith("scope/"),
                 "invalid_audit_supplement")
        scoped = "scope/supplements/" + name
        _require(scoped not in contents, "invalid_audit_supplement")
        contents[scoped] = content
        supplement_files.append(scoped)
    for name, content in sorted(contents.items()):
        _write(destination, name, content)
    files = {name: _sha256(content) for name, content in sorted(contents.items())}
    qlab = _qlab(qlab_profile, destination, list(files), "digest")
    metadata = {
        "schema_version": 2, "binding": binding.model_dump(mode="json"), "scope_files": files,
        "supplement_files": supplement_files,
        "objective_digest": qlab["objective_digest"], "scope_digest": qlab["scope_digest"],
        "qlab_commit": qlab_profile.commit,
    }
    _write(destination, "package.json", _canonical(metadata))
    return _load_package(destination, binding, qlab_profile)


def _load_package(root: Path, expected: AuditBinding, profile: QlabProfile) -> AuditPackage:
    metadata = _json(_file(root, "package.json").read_bytes(), "\x00not-a-lease-token\x00")
    base_keys = {"schema_version", "binding", "scope_files", "objective_digest", "scope_digest", "qlab_commit"}
    schema = metadata.get("schema_version") if isinstance(metadata, dict) else None
    expected_keys = base_keys if schema == 1 else base_keys | {"supplement_files"}
    _require(isinstance(metadata, dict) and set(metadata) == expected_keys
        and type(schema) is int and schema in {1, 2}
        and metadata["binding"] == expected.model_dump(mode="json")
        and metadata["qlab_commit"] == profile.commit, "audit_package_identity_mismatch")
    files = metadata["scope_files"]
    _require(isinstance(files, dict) and files, "audit_scope_missing")
    scope = root / "scope"
    _require(scope.is_dir() and not scope.is_symlink(), "audit_scope_missing")
    actual = {path.relative_to(root).as_posix() for path in scope.rglob("*") if not path.is_dir()}
    _require(actual == set(files), "audit_scope_inventory_mismatch")
    for name, digest in files.items():
        _require(name.startswith("scope/") and _sha256(_file(root, name).read_bytes()) == digest,
                 "audit_scope_content_changed")
    _require(_file(root, "scope/binding.json").read_bytes() == _canonical(expected.model_dump(mode="json")),
             "audit_binding_content_mismatch")
    spec = _model(MissionSpec, _file(root, "scope/mission.json").read_bytes(), "\x00not-a-lease-token\x00")
    _require(record_digest(spec) == expected.mission_digest
             and _file(root, "scope/objective.json").read_bytes() == _canonical(spec.objective.model_dump(mode="json")),
             "audit_objective_content_mismatch")
    expected_members = {"scope/mission.json", "scope/objective.json", "scope/binding.json", "scope/history.json"}
    for trial in expected.trials:
        prefix = f"scope/trials/{trial.trial_id}/"
        _require(files.get(prefix + "archive.zip") == trial.archive_sha256, "audit_archive_binding_mismatch")
        with zipfile_for_audit(_file(root, prefix + "archive.zip")) as archive:
            names = archive.namelist()
            _require(len(names) == len(set(names)), "duplicate_member")
            manifest = _model(AdaptiveManifest, archive.read("manifest.json"), "\x00not-a-lease-token\x00")
            _require(manifest.mission_id == expected.mission_id
                     and manifest.mission_digest == expected.mission_digest
                     and manifest.trial_id == trial.trial_id and manifest.plan_digest == trial.plan_digest
                     and manifest.plan.implementer == trial.implementer_role and manifest.spec == spec,
                     "audit_trial_binding_mismatch")
            for name in names:
                expected_members.add(prefix + name)
                _require(files.get(prefix + name) == _sha256(archive.read(name)), "audit_archive_member_mismatch")
        expected_members.add(prefix + "archive.zip")
    supplements = metadata.get("supplement_files", [])
    _require(isinstance(supplements, list) and len(supplements) == len(set(supplements))
             and all(isinstance(name, str) and name.startswith("scope/supplements/")
                     for name in supplements), "audit_supplement_inventory_mismatch")
    expected_members.update(supplements)
    _require(set(files) == expected_members, "reported_trial_scope_mismatch")
    values = _qlab(profile, root, list(files), "digest")
    _require(values == {"scope_digest": metadata["scope_digest"], "objective_digest": metadata["objective_digest"]},
             "audit_scope_digest_mismatch")
    return AuditPackage(root, expected, metadata["objective_digest"], metadata["scope_digest"],
                        MappingProxyType(files), profile.commit, tuple(supplements))


def zipfile_for_audit(path: Path):
    # Archive bytes have already passed the strict producer consumer validator.
    # This read only checks copied members against that exact hash-bound archive.
    import zipfile
    try:
        return zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        raise ValidationError("audit_archive_invalid") from None


def audit_context(package: AuditPackage, *, chunk_chars: int = 20000) -> tuple[dict[str, Any], ...]:
    """Complete file inventory and bounded text chunks; binary evidence stays hash-addressed."""
    _require(type(chunk_chars) is int and 1 <= chunk_chars <= 60000, "invalid_audit_chunk_size")
    chunks = []
    for name, digest in sorted(package.scope_files.items()):
        content = _file(package.root, name).read_bytes()
        _require(_sha256(content) == digest, "audit_scope_content_changed")
        try:
            text = content.decode("utf-8")
        except UnicodeError:
            chunks.append({"path": name, "sha256": digest, "encoding": "binary", "bytes": len(content)})
            continue
        for offset in range(0, max(1, len(text)), chunk_chars):
            chunks.append({"path": name, "sha256": digest, "encoding": "utf-8", "offset": offset,
                           "total_chars": len(text), "text": text[offset:offset + chunk_chars]})
    return tuple(chunks)


def _audit_identity(value: dict[str, Any], package: AuditPackage, *, require_pass: bool) -> None:
    _require(value.get("judge") == "leak-auditor" and value.get("target") == "."
             and value.get("scope") == sorted(package.scope_files)
             and value.get("audit_scope_digest") == package.scope_digest
             and value.get("audit_objective_digest") == package.objective_digest
             and not value.get("violations") and not value.get("supersedes")
             and not value.get("conflicts_with") and not value.get("spawns_claim"), "independent_audit_scope_mismatch")
    if require_pass:
        _require(value.get("verdict") == "pass"
                 and all(item["severity"] not in {"blocking", "major"} for item in value.get("findings", [])),
                 "independent_audit_not_passed")


def write_audit(
    package_root: Path, markdown: str, *, expected: AuditBinding, qlab_profile: QlabProfile,
    issued: date, rendered_at: datetime,
) -> Path:
    """Persist a separate validator's exact response and qlab report receipt, without judging it."""
    package = _load_package(package_root, expected, qlab_profile)
    _require(isinstance(markdown, str) and 0 < len(markdown.encode()) <= MAX_MEMBER_BYTES
             and rendered_at.tzinfo is not None and rendered_at.utcoffset() is not None,
             "invalid_audit_response")
    name = f"audits/AUDIT-leak-auditor-{issued:%Y%m%d}-{expected.validator_request_id.lower().replace('_', '-')}.md"
    audit = _write(package_root, name, markdown.encode())
    values = _qlab(qlab_profile, package_root, sorted(package.scope_files), "inspect", audit=name)
    _audit_identity(values, package, require_pass=False)
    _require(values["issued"].replace("-", "") == issued.strftime("%Y%m%d"), "audit_issued_mismatch")
    document = ('<!doctype html><html lang="ko"><meta charset="utf-8"><title>독립 감사</title>'
                '<body><h1>독립 감사 원문</h1><pre style="white-space:pre-wrap">'
                + html.escape(markdown) + '</pre></body></html>')
    _write(package_root, str(Path(name).with_suffix(".html")), document.encode())
    receipt_path = audit.with_suffix(".receipt.json")
    if not receipt_path.exists():
        _qlab(qlab_profile, package_root, sorted(package.scope_files), "write_receipt", audit=name,
              html_sha256=_sha256(document.encode()), rendered_at=rendered_at.isoformat())
        receipt_path.chmod(0o444)
    return audit


def verify_audit_package(
    package_root: Path, audit_path: Path, *, expected: AuditBinding, qlab_profile: QlabProfile,
) -> VerifiedAudit:
    package = _load_package(package_root, expected, qlab_profile)
    try:
        name = audit_path.absolute().relative_to(package_root.absolute()).as_posix()
    except ValueError:
        raise ValidationError("audit_path_outside_package") from None
    _require(name.startswith("audits/AUDIT-leak-auditor-") and name.endswith(".md"), "audit_path_mismatch")
    _require(name.endswith("-" + expected.validator_request_id.lower().replace("_", "-") + ".md"),
             "audit_request_filename_mismatch")
    audit_file = _file(package_root, name)
    receipt_name, html_name = str(Path(name).with_suffix(".receipt.json")), str(Path(name).with_suffix(".html"))
    receipt_content = _file(package_root, receipt_name).read_bytes()
    receipt = _json(receipt_content, "\x00not-a-lease-token\x00")
    document = _file(package_root, html_name).read_bytes()
    values = _qlab(qlab_profile, package_root, sorted(package.scope_files), "verify", audit=name)
    _audit_identity(values, package, require_pass=True)
    _require(not values.get("receipt_violations") and isinstance(receipt, dict)
             and receipt.get("audit") == audit_file.name
             and receipt.get("audit_sha256") == _sha256(audit_file.read_bytes())
             and receipt.get("scope_digest") == package.scope_digest and receipt.get("verdict") == "pass"
             and receipt.get("html_sha256") == _sha256(document), "independent_audit_receipt_mismatch")
    _load_package(package_root, expected, qlab_profile)
    audit_files = {name: _sha256(audit_file.read_bytes()), receipt_name: _sha256(receipt_content),
                   html_name: _sha256(document), "package.json": _sha256(_file(package_root, "package.json").read_bytes())}
    payload = {
        "schema_version": 1, "api": AUDIT_API, "binding": expected.model_dump(mode="json"),
        "scope_files": dict(package.scope_files), "scope_digest": package.scope_digest,
        "objective_digest": package.objective_digest, "qlab_commit": qlab_profile.commit,
        "audit_files": audit_files, "verdict": "pass", "violations": [], "receipt_violations": [],
    }
    verification_name = f"audits/{expected.validator_request_id}.verification.json"
    content = _canonical(payload)
    _write(package_root, verification_name, content)
    audit_files[verification_name] = _sha256(content)
    return VerifiedAudit(package, MappingProxyType(audit_files),
                         EvidenceRef(path=verification_name, sha256=_sha256(content)), _VERIFIED)


def assert_verified_trials(audit: VerifiedAudit, trials: tuple[ValidatedAdaptiveTrial, ...]) -> None:
    _require(isinstance(audit, VerifiedAudit) and audit._seal is _VERIFIED, "verified_audit_required")
    _match_trials(audit.package.binding, trials)
    for name, digest in {**audit.package.scope_files, **audit.audit_files}.items():
        _require(_sha256(_file(audit.package.root, name).read_bytes()) == digest, "verified_audit_expired")


def assert_verified_history(audit: VerifiedAudit, history: ReportHistory) -> None:
    _require(_file(audit.package.root, "scope/history.json").read_bytes() == _canonical(history.to_dict()),
             "reported_history_not_audited")


def audit_publication(
    audit: VerifiedAudit, trial_id: UUID, *, source_id: str, report: EvidenceRef, published_at: datetime,
) -> AuditPublication:
    """Internal conversion after the report and accessible source have been persisted."""
    _require(isinstance(audit, VerifiedAudit) and audit._seal is _VERIFIED, "verified_audit_required")
    match = next((item for item in audit.package.binding.trials if item.trial_id == trial_id), None)
    _require(match is not None, "publication_trial_not_audited")
    for name, digest in {**audit.package.scope_files, **audit.audit_files, report.path: report.sha256}.items():
        _require(_sha256(_file(audit.package.root, name).read_bytes()) == digest, "verified_audit_expired")
    return AuditPublication(
        trial_id=trial_id, mission_digest=audit.package.binding.mission_digest, outcome_digest=match.outcome_digest,
        source_id=source_id, audit_files=dict(audit.audit_files), verification=audit.verification, report=report,
        verifier_role="validator", verifier_commit=audit.package.qlab_commit, published_at=published_at,
    )
