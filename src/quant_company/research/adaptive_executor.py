"""Run one approved adaptive plan in an operator-profiled 3070 sandbox.

No economic strategy is implemented here. A provisioned, hash-bound evaluator
must implement the documented qualify/evaluate JSON and dated-return protocol.
"""

from __future__ import annotations

import csv
import io
import math
import os
import platform
import shutil
import stat
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .adaptive_contracts import (
    AdaptiveAssignment,
    AdaptiveExecutionProfile,
    AdaptiveExecutionReceipt,
    AdaptiveManifest,
    AdaptiveModel,
    AdaptiveQualification,
    AdaptiveResult,
    digest_model,
)
from .executor import MAX_ARCHIVE_BYTES, MAX_EXPANDED_BYTES, ExecutionBlocked, checked_path, verify_repository
from .mission_contracts import Path as RepoPath
from .policy_contracts import require_profile_policy, require_scope, scoped_kwargs
from .sandbox import InputMount, SandboxError, SandboxSpec, profile_from_dict, run_sandbox
from .worker import WorkerConfig, atomic_json, sha_file, sync_directory, utc_now
from .workspace import WorkspaceError, _git, canonical_path, prepare_workspace, snapshot_files

CONTRACT_INPUT = "__contract__/manifest.json"


class AdaptiveProfile(AdaptiveModel):
    """Worker-local operator configuration. Never accepted from a model or poll."""

    schema_version: Literal[1] = 1
    public_profile: AdaptiveExecutionProfile
    runtime: dict[str, Any]
    input_sources: dict[RepoPath, Path] = Field(min_length=1)
    allowed_input_roots: list[Path] = Field(min_length=1)

    @field_validator("input_sources")
    @classmethod
    def absolute_sources(cls, value):
        if any(not path.is_absolute() for path in value.values()):
            raise ValueError("operator-input-source-must-be-absolute")
        return value

    @field_validator("allowed_input_roots")
    @classmethod
    def absolute_roots(cls, value):
        if any(not path.is_absolute() for path in value):
            raise ValueError("operator-input-root-must-be-absolute")
        return value

    @model_validator(mode="after")
    def runtime_matches_public(self):
        runtime = profile_from_dict(self.runtime)
        profile = self.public_profile
        if (runtime.profile_id != profile.id or runtime.python_executable != profile.python_executable
                or runtime.python_sha256 != profile.python_sha256
                or [{"target": mount.target, "sha256": mount.sha256} for mount in runtime.mounts]
                != [mount.model_dump() for mount in profile.runtime_mounts]):
            raise ValueError("local-runtime-differs-from-approved-profile")
        if set(self.input_sources) != set(profile.evaluation_input_names):
            raise ValueError("operator-input-binding-mismatch")
        return self


def load_profile(config: WorkerConfig, manifest: AdaptiveManifest) -> AdaptiveProfile:
    name = manifest.spec.execution_profile
    path = config.adaptive_profiles.get(name)
    if path is None:
        raise ExecutionBlocked("adaptive-profile-not-provisioned")
    try:
        path = canonical_path(path)
        profile = AdaptiveProfile.model_validate_json(path.read_text())
    except (OSError, ValueError, WorkspaceError):
        raise ExecutionBlocked("adaptive-profile-unavailable") from None
    public = profile.public_profile
    if public.id != name or digest_model(public) != manifest.spec.execution_profile_digest:
        raise ExecutionBlocked("adaptive-profile-digest-mismatch")
    try:
        require_profile_policy(manifest.spec, public)
    except ValueError:
        raise ExecutionBlocked("adaptive-profile-policy-mismatch") from None
    if set(public.evaluation_input_names) != set(manifest.plan.input_files):
        raise ExecutionBlocked("adaptive-profile-input-scope-mismatch")
    if set(manifest.code_files) != set(public.code_paths):
        raise ExecutionBlocked("adaptive-code-outside-approved-closure")
    if not set(manifest.plan.changed_paths) <= set(public.code_paths):
        raise ExecutionBlocked("adaptive-changes-outside-code-closure")
    if manifest.code_files.get(public.entrypoint) != public.entrypoint_sha256:
        raise ExecutionBlocked("adaptive-evaluator-digest-mismatch")
    if any(path == protected or path.startswith(protected + "/") for path in manifest.plan.changed_paths
           for protected in public.protected_paths):
        raise ExecutionBlocked("adaptive-plan-changes-protected-path")
    return profile


def verify_host(manifest: AdaptiveManifest) -> None:
    if sys.platform != "linux" or platform.node() != manifest.plan.hostname:
        raise ExecutionBlocked("execution-requires-approved-3070-host")
    executable = shutil.which("nvidia-smi") or "/usr/lib/wsl/lib/nvidia-smi"
    gpu = subprocess.run([executable, "--query-gpu=name", "--format=csv,noheader"],
                         capture_output=True, text=True, timeout=15)
    if gpu.returncode or gpu.stdout.strip() != manifest.plan.gpu:
        raise ExecutionBlocked("execution-gpu-mismatch")


def qualification_from_file(path: Path, manifest: AdaptiveManifest,
                            profile: AdaptiveExecutionProfile, *, producer: bool = False) -> AdaptiveQualification:
    value = AdaptiveQualification.model_validate_json(path.read_text())
    _producer_scope(value, manifest, producer)
    if (value.trial_id != manifest.trial_id or value.plan_digest != manifest.plan_digest
            or value.code_commit != manifest.plan.code_commit or value.config_files != manifest.plan.config_files
            or value.input_files != {name: manifest.plan.input_files[name]
                                     for name in profile.qualification_input_names}):
        raise ExecutionBlocked("adaptive-qualification-identity-mismatch")
    if value.primary_unit != manifest.spec.objective.unit:
        raise ExecutionBlocked("adaptive-qualification-unit-mismatch")
    if len(value.json_dates) != value.sample_count or len(value.json_datetimes) != value.sample_count:
        raise ExecutionBlocked("adaptive-qualification-sample-count-mismatch")
    if not {"date", "datetime"} <= set(value.typed_schema.values()):
        raise ExecutionBlocked("adaptive-qualification-date-types-missing")
    dates = [*value.json_dates, *(value.date() for value in value.json_datetimes)]
    if any(day >= manifest.spec.development.start or any(window.start <= day <= window.end
                                                        for window in manifest.spec.sealed) for day in dates):
        raise ExecutionBlocked("qualification-outside-warmup")
    return value


def read_return_series(content: bytes) -> list[tuple[date, float]]:
    """Strict public CSV convention. Interpretation and adoption remain separate."""
    try:
        reader = csv.reader(io.StringIO(content.decode("utf-8")))
        if next(reader, None) != ["date", "net_return"]:
            raise ValueError("return-series-header-mismatch")
        rows = []
        for row in reader:
            if len(row) != 2:
                raise ValueError("return-series-width-mismatch")
            day = date.fromisoformat(row[0])
            if day.isoformat() != row[0]:
                raise ValueError("return-series-date-not-canonical")
            value = float(row[1])
            if not math.isfinite(value) or value < -1 or (rows and day <= rows[-1][0]):
                raise ValueError("return-series-nonfinite-or-unordered")
            rows.append((day, value))
        if len(rows) < 2 or rows[0][1] != 0:
            raise ValueError("return-series-initial-capital-missing")
        return rows
    except (UnicodeError, csv.Error, ValueError):
        raise ExecutionBlocked("invalid-dated-return-series") from None


def _producer_scope(value, manifest: AdaptiveManifest, producer: bool) -> None:
    if producer and manifest.spec.research_scope is not None:
        if value.schema_version != 1 or value.research_scope is not None:
            raise ExecutionBlocked("protected-producer-cannot-declare-research-scope")
    else:
        try:
            require_scope(manifest.spec, value)
        except ValueError:
            raise ExecutionBlocked("adaptive-record-scope-mismatch") from None


def bind_producer_record(value, source: Path, manifest: AdaptiveManifest):
    """Trusted worker binds unchanged producer bytes to the approved scope."""
    _producer_scope(value, manifest, True)
    if manifest.spec.research_scope is None:
        return value
    return type(value).model_validate({
        **value.model_dump(mode="json"), **scoped_kwargs(manifest.spec), "producer_sha256": sha_file(source),
    })


def validate_result_files(root: Path, manifest: AdaptiveManifest, *, producer: bool = False) -> AdaptiveResult:
    path = checked_path(root, "result.json")
    result = AdaptiveResult.model_validate_json(path.read_text())
    _producer_scope(result, manifest, producer)
    if (result.trial_id != manifest.trial_id or result.plan_digest != manifest.plan_digest
            or result.code_commit != manifest.plan.code_commit
            or result.metrics.sample_window != manifest.plan.development):
        raise ExecutionBlocked("adaptive-result-identity-mismatch")
    for name, digest in result.output_files.items():
        artifact = checked_path(root, name)
        if not artifact.is_file() or sha_file(artifact) != digest:
            raise ExecutionBlocked("adaptive-result-artifact-mismatch")
    if result.observations is not None:
        from .evaluation import evaluate_observations

        measured = evaluate_observations(checked_path(root, result.observations).read_bytes(),
                                        manifest.spec.evaluation, manifest.plan.development)
        if (result.metrics.primary.model_dump(exclude={"value"}) != manifest.spec.objective.model_dump()
                or not math.isclose(measured["value"], result.metrics.primary.value, rel_tol=0, abs_tol=1e-12)
                or measured["sample_count"] != result.metrics.sample_count):
            raise ExecutionBlocked("adaptive-scientific-metric-mismatch")
        return result
    base = read_return_series(checked_path(root, result.base_returns.path).read_bytes())
    stress = read_return_series(checked_path(root, result.stress_returns.path).read_bytes())
    if [row[0] for row in base] != [row[0] for row in stress] or len(base) != result.metrics.sample_count:
        raise ExecutionBlocked("adaptive-return-dates-or-count-mismatch")
    if any(not manifest.plan.development.start <= day <= manifest.plan.development.end for day, _ in base):
        raise ExecutionBlocked("adaptive-return-outside-development")
    if result.base_returns.frequency == "monthly" and len({day.strftime("%Y-%m") for day, _ in base}) != len(base):
        raise ExecutionBlocked("adaptive-return-duplicate-month")
    return result


def _check_stop(job: Path) -> None:
    if (job / "stop.json").exists():
        raise ExecutionBlocked("execution-stop-requested")


def _sandbox_spec(profile: AdaptiveProfile, manifest: AdaptiveManifest, checkout: Path,
                  job: Path, action: Literal["qualify", "evaluate"]) -> SandboxSpec:
    public = profile.public_profile
    names = public.qualification_input_names if action == "qualify" else public.evaluation_input_names
    output = job / action
    output.mkdir(mode=0o700)
    inputs = tuple(InputMount(profile.input_sources[name], name, manifest.plan.input_files[name]) for name in names)
    inputs += (InputMount(job / "manifest.json", CONTRACT_INPUT, sha_file(job / "manifest.json")),)
    return SandboxSpec(
        profile=profile_from_dict(profile.runtime), code_root=checkout, code_commit=manifest.plan.code_commit,
        code_files=dict(manifest.code_files), input_mounts=inputs,
        allowed_input_roots=(*profile.allowed_input_roots, job), allowed_job_root=job, output_dir=output,
        entrypoint=public.entrypoint,
        argv=(action, "--config", "/code/" + manifest.config_path, "--manifest", "/inputs/" + CONTRACT_INPUT,
              "--output", "/output"),
        timeout_seconds=(public.qualification_timeout_seconds if action == "qualify"
                         else public.evaluation_timeout_seconds), fixture_only=public.fixture_only,
    )


def _public_sandbox_receipt(receipt, action: str, spec: SandboxSpec, manifest: AdaptiveManifest) -> dict:
    value = receipt.to_dict()
    # Host mount roots and output paths are deliberately absent from the package.
    scope = manifest.spec.research_scope
    return {**value, **({"research_scope": scope.model_dump(mode="json")} if scope else {}),
            "stdout_path": action + "/stdout.log", "stderr_path": action + "/stderr.log",
            "action": action, "execution_profile_digest": manifest.spec.execution_profile_digest,
            "manifest_digest": digest_model(manifest),
            "input_files": {mount.target: mount.sha256 for mount in spec.input_mounts
                            if mount.target != CONTRACT_INPUT},
            "entrypoint": spec.entrypoint, "argv": list(spec.argv),
            "timeout_seconds": spec.timeout_seconds, "fixture_only": spec.fixture_only}


def build_adaptive_archive(job: Path, manifest: AdaptiveManifest, profile: AdaptiveExecutionProfile,
                           receipt: AdaptiveExecutionReceipt, checkout: Path, result: AdaptiveResult) -> str:
    members = {name: job / name for name in (
        "receipt.json", "manifest.json", "qualification.json", "runtime.json", "profile.json",
        "sandbox-qualification.json", "sandbox-evaluation.json",
    )}
    members["result.json"] = job / "adaptive-result.json"
    if manifest.spec.research_scope is not None:
        members.update({name: job / name for name in ("producer-qualification.json", "producer-result.json")})
    members.update({"code/" + name: checked_path(checkout, name) for name in manifest.code_files})
    members.update({"outputs/" + name: checked_path(job / "evaluate", name) for name in result.output_files})
    atomic_json(job / "receipt.json", receipt.model_dump(mode="json"))
    atomic_json(job / "profile.json", profile.model_dump(mode="json"))
    if sum(path.stat().st_size for path in members.values()) > MAX_EXPANDED_BYTES:
        raise ExecutionBlocked("artifact-expanded-size-limit")
    temporary = job / "artifact.zip.tmp"
    with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in sorted(members.items()):
            if path.is_symlink() or not path.is_file():
                raise ExecutionBlocked("artifact-not-regular-file")
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o444) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    if temporary.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ExecutionBlocked("artifact-compressed-size-limit")
    with temporary.open("rb") as stream:
        os.fsync(stream.fileno())
    os.replace(temporary, job / "artifact.zip")
    sync_directory(job)
    return sha_file(job / "artifact.zip")


def execute_adaptive(config: WorkerConfig, assignment: AdaptiveAssignment, manifest: AdaptiveManifest,
                     job: Path, started_at: str) -> str:
    # Even fixture profiles use the approved 3070 through the actual worker. Unit
    # fixture tests inject a sandbox operation explicitly and are labelled as such.
    verify_host(manifest)
    if manifest != assignment.manifest or manifest.company_commit != config.company_commit:
        raise ExecutionBlocked("adaptive-company-or-manifest-mismatch")
    if not Path(__file__).resolve().is_relative_to(config.company_repo / "src"):
        raise ExecutionBlocked("company-import-outside-exact-checkout")
    verify_repository(config.company_repo, config.company_commit)
    profile = load_profile(config, manifest)
    _check_stop(job)
    try:
        prepared = prepare_workspace(
            source_snapshot=job / "source.bundle", expected_snapshot_sha256=manifest.bundle_sha256,
            base_commit=manifest.plan.code_commit, destination=job / "workspace",
            allowed_paths=tuple(manifest.spec.code.write_paths),
            protected_paths=tuple(profile.public_profile.protected_paths), patches=(),
        )
        if (prepared.commit != manifest.plan.code_commit
                or {name: prepared.manifest["files"].get(name) for name in manifest.code_files} != manifest.code_files):
            raise ExecutionBlocked("adaptive-code-manifest-mismatch")
        checkout = prepared.worktree
        _git(checkout, "merge-base", "--is-ancestor", manifest.spec.code.base_commit, manifest.plan.code_commit)
        changed = {path.decode() for path in _git(checkout, "diff", "--name-only", "-z", "--no-ext-diff",
                                                "--no-textconv", manifest.spec.code.base_commit,
                                                manifest.plan.code_commit, "--").split(b"\0") if path}
        if changed != set(manifest.plan.changed_paths):
            raise ExecutionBlocked("adaptive-actual-changes-differ-from-plan")
        atomic_json(job / "manifest.json", manifest.model_dump(mode="json"))
        atomic_json(job / "runtime.json", {
            **({"research_scope": manifest.spec.research_scope.model_dump(mode="json")}
               if manifest.spec.research_scope is not None else {}),
            "schema_version": 1, "execution_profile_digest": manifest.spec.execution_profile_digest,
            "worker_id": "worker", "hostname": platform.node(), "gpu": manifest.plan.gpu,
            "code_commit": manifest.plan.code_commit, "company_commit": config.company_commit,
            "python_executable": profile.public_profile.python_executable,
            "python_sha256": profile.public_profile.python_sha256,
            "runtime_mounts": [mount.model_dump() for mount in profile.public_profile.runtime_mounts],
        })
        for action, label in (("qualify", "qualification"), ("evaluate", "evaluation")):
            _check_stop(job)
            sandbox_spec = _sandbox_spec(profile, manifest, checkout, job, action)
            sandbox_receipt = run_sandbox(sandbox_spec)
            atomic_json(job / f"sandbox-{label}.json",
                        _public_sandbox_receipt(sandbox_receipt, action, sandbox_spec, manifest))
            _check_stop(job)
            if sandbox_receipt.exit_code or sandbox_receipt.timed_out:
                raise ExecutionBlocked(f"adaptive-{label}-failed")
            if action == "qualify":
                source = checked_path(job / action, "qualification.json")
                value = qualification_from_file(source, manifest, profile.public_profile, producer=True)
                if manifest.spec.research_scope is None:
                    shutil.copyfile(source, job / "qualification.json")
                else:
                    shutil.copyfile(source, job / "producer-qualification.json")
                    atomic_json(job / "qualification.json", bind_producer_record(value, source, manifest).model_dump(
                        mode="json"))
                    qualification_from_file(job / "qualification.json", manifest, profile.public_profile)
        producer = validate_result_files(job / "evaluate", manifest, producer=True)
        source = checked_path(job / "evaluate", "result.json")
        result = bind_producer_record(producer, source, manifest)
        if manifest.spec.research_scope is None:
            shutil.copyfile(source, job / "adaptive-result.json")
        else:
            shutil.copyfile(source, job / "producer-result.json")
            atomic_json(job / "adaptive-result.json", result.model_dump(mode="json"))
        actual_files = snapshot_files(checkout, manifest.plan.code_commit)
        if {name: actual_files.get(name) for name in manifest.code_files} != manifest.code_files:
            raise ExecutionBlocked("adaptive-code-changed-after-execution")
        # Recheck approved input bytes after execution; a path alone is not a snapshot identity.
        for name, digest in manifest.plan.input_files.items():
            if sha_file(canonical_path(profile.input_sources[name])) != digest:
                raise ExecutionBlocked("adaptive-input-changed-after-execution")
        receipt = AdaptiveExecutionReceipt(
            **assignment.model_dump(exclude={"action", "lease_token", "manifest"}),
            **scoped_kwargs(manifest.spec),
            mission_id=manifest.mission_id, mission_digest=manifest.mission_digest, trial_id=manifest.trial_id,
            plan_digest=manifest.plan_digest, execution_profile_digest=manifest.spec.execution_profile_digest,
            worker_id="worker", hostname=manifest.plan.hostname, gpu=manifest.plan.gpu,
            code_commit=manifest.plan.code_commit, company_commit=config.company_commit,
            input_files=manifest.plan.input_files, config_files=manifest.plan.config_files,
            output_files=result.output_files, qualification_sha256=sha_file(job / "qualification.json"),
            result_sha256=sha_file(job / "adaptive-result.json"),
            sandbox_qualification_sha256=sha_file(job / "sandbox-qualification.json"),
            sandbox_evaluation_sha256=sha_file(job / "sandbox-evaluation.json"),
            runtime_sha256=sha_file(job / "runtime.json"), started_at=started_at, completed_at=utc_now(),
            execution_count=1, qualification_passed=True, sealed_read=False,
            scientific_trials_added=0 if profile.public_profile.fixture_only else 1,
            fixture_only=profile.public_profile.fixture_only,
        )
        return build_adaptive_archive(job, manifest, profile.public_profile, receipt, checkout, result)
    except (WorkspaceError, SandboxError):
        raise ExecutionBlocked("adaptive-sandbox-or-workspace-rejected") from None
