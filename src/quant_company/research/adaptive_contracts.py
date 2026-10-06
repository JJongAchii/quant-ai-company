"""Typed adaptive transport and evidence; no field grants research approval.

The fixed P11 replay contract lives unchanged in contracts.py. Adaptive execution
binds a separate approved mission, plan and operator profile to measured files.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, FiniteFloat, field_validator, model_validator

from ..contracts import StrictModel
from .contracts import Assignment, Commit, Digest
from .mission_contracts import FileMap, MissionSpec, Path, TrialMetrics, TrialPlan
from .policy_contracts import ResultScope, ScopedRecord, require_scope

ADAPTIVE_RECIPE = "kr-etf-monthly-python-v1"
RESEARCH_RECIPE = "kr-research-python-v2"
AdaptiveRecipe = Literal["kr-etf-monthly-python-v1", "kr-research-python-v2"]
MAX_BUNDLE_BYTES = 256 * 1024 * 1024
MAX_OUTPUT_FILES = 64


def digest_model(value: StrictModel) -> str:
    return hashlib.sha256(json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def record_digest(value: StrictModel) -> str:
    """Existing Company/MissionStore record encoding (default JSON separators)."""
    return hashlib.sha256(json.dumps(value.model_dump(mode="json"), sort_keys=True,
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-required")
    return value


def _date_only(value: Any) -> Any:
    if isinstance(value, datetime) or (isinstance(value, str) and ("T" in value or " " in value)):
        raise ValueError("date-not-datetime-required")
    return value


def _guest_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value.startswith("/") or value == "/" or str(path) != value or ".." in path.parts:
        raise ValueError("normalized-absolute-guest-path-required")
    if "\\" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("invalid-guest-path")
    return value


class AdaptiveModel(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuntimeMountIdentity(AdaptiveModel):
    target: str
    sha256: Digest

    _target = field_validator("target")(_guest_path)


class AdaptiveExecutionProfile(AdaptiveModel):
    """Public, hash-bound operator policy. Local filesystem paths are separate."""

    schema_version: Literal[1, 2] = 1
    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{1,79}$")]
    protocol: Literal["quant-company-adaptive-v1"] = "quant-company-adaptive-v1"
    return_series_contract: Literal["initial-zero-calendar-cagr-v1"] = "initial-zero-calendar-cagr-v1"
    entrypoint: Path
    entrypoint_sha256: Digest
    protected_paths: Annotated[list[Path], Field(min_length=1)]
    code_paths: Annotated[list[Path], Field(min_length=1)]
    python_executable: str
    python_sha256: Digest
    runtime_mounts: Annotated[list[RuntimeMountIdentity], Field(min_length=1)]
    qualification_input_names: Annotated[list[Path], Field(min_length=1)]
    evaluation_input_names: Annotated[list[Path], Field(min_length=1)]
    qualification_timeout_seconds: Annotated[FiniteFloat, Field(gt=0)]
    evaluation_timeout_seconds: Annotated[FiniteFloat, Field(gt=0)]
    fixture_only: bool = Field(strict=True)
    data_policy_digest: Digest | None = Field(default=None, exclude_if=lambda v: v is None)
    result_scope: ResultScope | None = Field(default=None, exclude_if=lambda v: v is None)

    _python = field_validator("python_executable")(_guest_path)

    @model_validator(mode="after")
    def bounded(self):
        if self.schema_version == 2:
            if self.data_policy_digest is None or self.result_scope is None:
                raise ValueError("Policy profiles require a policy digest and result scope")
        elif self.data_policy_digest is not None or self.result_scope is not None:
            raise ValueError("Legacy profiles cannot carry a new data policy")
        if not self.entrypoint.endswith(".py"):
            raise ValueError("python-entrypoint-required")
        if not any(self.entrypoint == path or self.entrypoint.startswith(path + "/")
                   for path in self.protected_paths):
            raise ValueError("entrypoint-must-be-protected")
        for names in (self.protected_paths, self.code_paths, self.qualification_input_names, self.evaluation_input_names):
            if len(names) != len(set(names)):
                raise ValueError("duplicate-profile-path")
        if self.entrypoint not in self.code_paths:
            raise ValueError("entrypoint-outside-code-closure")
        if not set(self.qualification_input_names) < set(self.evaluation_input_names):
            raise ValueError("qualification-must-use-strict-input-subset")
        if len({mount.target for mount in self.runtime_mounts}) != len(self.runtime_mounts):
            raise ValueError("duplicate-runtime-target")
        if any(name.startswith("__contract__/") for name in self.evaluation_input_names):
            raise ValueError("reserved-protocol-input-path")
        return self


class AdaptiveManifest(AdaptiveModel):
    schema_version: Literal[1] = 1
    id: AdaptiveRecipe = ADAPTIVE_RECIPE
    kind: Literal["adaptive_discovery"] = "adaptive_discovery"
    mission_id: UUID
    mission_digest: Digest
    trial_id: UUID
    plan_digest: Digest
    plan: TrialPlan
    spec: MissionSpec
    code_files: FileMap
    bundle_sha256: Digest
    config_path: Path
    company_commit: Commit

    @model_validator(mode="after")
    def identities(self):
        if self.id == ADAPTIVE_RECIPE and self.spec.kind != "strategy":
            raise ValueError("only-strategy-missions-executable")
        if (self.id == RESEARCH_RECIPE) != (self.spec.schema_version >= 2):
            raise ValueError("adaptive-profile-version-mismatch")
        require_scope(self.spec, self.plan)
        if self.mission_digest != record_digest(self.spec) or self.plan_digest != record_digest(self.plan):
            raise ValueError("adaptive-manifest-digest-mismatch")
        if self.plan.mission_digest != self.mission_digest or self.plan.trial_id != self.trial_id:
            raise ValueError("adaptive-plan-identity-mismatch")
        if (self.plan.execution_profile != self.spec.execution_profile
                or self.plan.input_files != self.spec.data.input_files
                or self.plan.lake_id != self.spec.data.lake_id
                or self.plan.development != self.spec.development
                or self.plan.repository != self.spec.code.repository):
            raise ValueError("adaptive-plan-outside-mission")
        if self.config_path not in self.plan.config_files:
            raise ValueError("adaptive-config-not-registered")
        if any(self.code_files.get(path) != digest for path, digest in self.plan.config_files.items()):
            raise ValueError("adaptive-config-code-mismatch")
        if any(not any(path == allowed or path.startswith(allowed + "/")
                       for allowed in self.spec.code.write_paths) for path in self.plan.changed_paths):
            raise ValueError("adaptive-code-outside-mission")
        if any(part.casefold() == ".git" for path in self.code_files for part in PurePosixPath(path).parts):
            raise ValueError("git-metadata-in-code-map")
        return self


class AdaptiveAssignment(AdaptiveModel):
    job_id: UUID
    project_id: UUID
    revision: int = Field(ge=1)
    recipe_id: AdaptiveRecipe = ADAPTIVE_RECIPE
    kind: Literal["adaptive_discovery"] = "adaptive_discovery"
    manifest_digest: Digest
    approval_event_id: str = Field(min_length=1)
    lease_token: Digest
    action: Literal["run", "reconcile", "cancel"]
    manifest: AdaptiveManifest

    @model_validator(mode="after")
    def manifest_identity(self):
        if self.manifest_digest != digest_model(self.manifest) or self.recipe_id != self.manifest.id:
            raise ValueError("adaptive-assignment-manifest-mismatch")
        return self


def parse_assignment(value: dict[str, Any]) -> Assignment | AdaptiveAssignment:
    if value.get("kind") == "adaptive_discovery" or value.get("recipe_id") in {ADAPTIVE_RECIPE, RESEARCH_RECIPE}:
        return AdaptiveAssignment.model_validate(value)
    return Assignment.model_validate(value)


class AdaptiveQualification(ScopedRecord):
    schema_version: Literal[1, 2] = 1
    kind: Literal["adaptive_qualification"] = "adaptive_qualification"
    trial_id: UUID
    plan_digest: Digest
    code_commit: Commit
    config_files: FileMap
    input_files: FileMap
    sample_count: int = Field(gt=0, strict=True)
    json_dates: Annotated[list[date], Field(min_length=1)]
    json_datetimes: Annotated[list[datetime], Field(min_length=1)]
    typed_schema: Annotated[dict[str, Literal["date", "datetime", "float", "integer", "string", "boolean"]],
                            Field(min_length=1)]
    primary_unit: Literal["fraction-per-year", "fraction"]
    empty_sample_rejected: Literal[True]
    non_finite_rejected: Literal[True]
    json_roundtrip_passed: Literal[True]
    performance_read: Literal[False]
    sealed_read: Literal[False]
    producer_sha256: Digest | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def producer_binding(self):
        if (self.schema_version == 2) != (self.producer_sha256 is not None):
            raise ValueError("Scoped qualification requires exact protected producer bytes")
        return self

    @field_validator("json_dates", mode="before")
    @classmethod
    def dates_only(cls, value):
        for item in value:
            _date_only(item)
        return value

    @field_validator("json_datetimes")
    @classmethod
    def aware_dates(cls, value):
        return [_aware(item) for item in value]


class ReturnSeriesRef(AdaptiveModel):
    path: Path
    sha256: Digest
    frequency: Literal["daily", "monthly"]
    unit: Literal["fraction-per-period"]

    @model_validator(mode="after")
    def csv(self):
        if not self.path.endswith(".csv"):
            raise ValueError("return-series-csv-required")
        return self


class AdaptiveResult(ScopedRecord):
    schema_version: Literal[1, 2] = 1
    kind: Literal["adaptive_result"] = "adaptive_result"
    trial_id: UUID
    plan_digest: Digest
    code_commit: Commit
    metrics: TrialMetrics
    base_returns: ReturnSeriesRef | None = Field(default=None, exclude_if=lambda v: v is None)
    stress_returns: ReturnSeriesRef | None = Field(default=None, exclude_if=lambda v: v is None)
    observations: Path | None = Field(default=None, exclude_if=lambda v: v is None)
    output_files: Annotated[FileMap, Field(max_length=MAX_OUTPUT_FILES)]
    producer_sha256: Digest | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def series_contract(self):
        if (self.schema_version == 2) != (self.producer_sha256 is not None):
            raise ValueError("Scoped results require exact protected producer bytes")
        if self.metrics.sample_count < 2:
            raise ValueError("dated-return-series-needs-two-observations")
        if self.metrics.primary.metric != "stress-net-absolute-cagr":
            if (self.base_returns is not None or self.stress_returns is not None or self.observations is None
                    or not self.observations.endswith(".csv") or self.observations not in self.output_files):
                raise ValueError("scientific-result-requires-observations-without-invented-returns")
            return self
        if self.base_returns is None or self.stress_returns is None or self.observations is not None:
            raise ValueError("strategy-result-requires-return-series")
        if self.base_returns.path == self.stress_returns.path:
            raise ValueError("base-and-stress-artifacts-must-be-distinct")
        if self.base_returns.frequency != self.stress_returns.frequency:
            raise ValueError("return-series-frequency-mismatch")
        for ref in (self.base_returns, self.stress_returns):
            if self.output_files.get(ref.path) != ref.sha256:
                raise ValueError("return-series-not-in-output-map")
        for path in self.output_files:
            if not path.endswith((".csv", ".json")) or path in {"result.json", "qualification.json"}:
                raise ValueError("unsupported-output-artifact")
        return self


class AdaptiveExecutionReceipt(ScopedRecord):
    schema_version: Literal[1, 2] = 1
    kind: Literal["adaptive_discovery"] = "adaptive_discovery"
    job_id: UUID
    project_id: UUID
    revision: int = Field(ge=1)
    recipe_id: AdaptiveRecipe = ADAPTIVE_RECIPE
    manifest_digest: Digest
    approval_event_id: str
    mission_id: UUID
    mission_digest: Digest
    trial_id: UUID
    plan_digest: Digest
    execution_profile_digest: Digest
    worker_id: Literal["worker"]
    hostname: Literal["DESKTOP-5T00NAF"]
    gpu: Literal["NVIDIA GeForce RTX 3070"]
    code_commit: Commit
    company_commit: Commit
    input_files: FileMap
    config_files: FileMap
    output_files: FileMap
    qualification_sha256: Digest
    result_sha256: Digest
    sandbox_qualification_sha256: Digest
    sandbox_evaluation_sha256: Digest
    runtime_sha256: Digest
    started_at: datetime
    completed_at: datetime
    execution_count: Literal[1]
    qualification_passed: Literal[True]
    sealed_read: Literal[False]
    scientific_trials_added: Literal[0, 1]
    fixture_only: bool = Field(strict=True)

    _started = field_validator("started_at")(_aware)
    _completed = field_validator("completed_at")(_aware)

    @model_validator(mode="after")
    def ordered(self):
        if self.completed_at < self.started_at:
            raise ValueError("execution-timestamps-reversed")
        if self.scientific_trials_added != (0 if self.fixture_only else 1):
            raise ValueError("fixture-cannot-add-a-scientific-trial")
        return self
