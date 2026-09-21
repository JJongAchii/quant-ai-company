"""Versioned proposals for bounded discovery; none of these types grants execution authority."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, ConfigDict, Field, FiniteFloat, field_validator, model_validator

from ..contracts import StrictModel
from .contracts import Commit, Digest


def relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or str(path) != value or value == "."
            or ".." in path.parts or "\\" in value or ":" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("A normalized repository-relative path is required")
    return value


Path = Annotated[str, AfterValidator(relative_path)]
Text = Annotated[str, Field(min_length=1, max_length=6000)]
SourceIds = Annotated[list[str], Field(min_length=1, max_length=50)]
FileMap = Annotated[dict[Path, Digest], Field(min_length=1)]
ChangeAxis = Literal["features", "model", "portfolio", "implementation"]
RiskMetric = Literal["max_drawdown", "annual_turnover", "gross_exposure"]


class MissionModel(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DateWindow(MissionModel):
    start: date
    end: date

    @field_validator("start", "end", mode="before")
    @classmethod
    def dates_only(cls, value):
        if isinstance(value, datetime) or (isinstance(value, str) and ("T" in value or " " in value)):
            raise ValueError("A date, not a timestamp, is required")
        return value

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("Date window is reversed")
        return self


class Objective(MissionModel):
    metric: Literal["stress-net-absolute-cagr"]
    direction: Literal["maximize"]
    unit: Literal["fraction-per-year"]


class RiskConstraint(MissionModel):
    metric: RiskMetric
    maximum: Annotated[FiniteFloat, Field(ge=0)]
    unit: Literal["fractional-loss", "multiple"]

    @model_validator(mode="after")
    def units(self):
        expected = "fractional-loss" if self.metric == "max_drawdown" else "multiple"
        if self.unit != expected:
            raise ValueError("Risk metric unit mismatch")
        return self


class DataScope(MissionModel):
    lake_id: Text
    input_files: FileMap


class CodeScope(MissionModel):
    repository: Literal["quant-lab"]
    base_commit: Commit
    write_paths: Annotated[list[Path], Field(min_length=1)]


class ResourcePolicy(MissionModel):
    worker_id: Literal["worker"]
    priority: Literal["owner", "autonomous"]


class SearchPolicy(MissionModel):
    max_trials_per_cycle: int = Field(ge=1, strict=True)
    patience: int = Field(ge=1, strict=True)
    min_improvement: Annotated[FiniteFloat, Field(ge=0)]
    continuous: bool = Field(strict=True)

    @model_validator(mode="after")
    def bounded_patience(self):
        if self.patience > self.max_trials_per_cycle:
            raise ValueError("Patience exceeds the approved cycle budget")
        return self


class MissionSpec(MissionModel):
    schema_version: Literal[1] = 1
    title: Text
    kind: Literal["strategy", "model", "claim"]
    objective: Objective
    base_cost_bps: Annotated[FiniteFloat, Field(ge=0)]
    stress_cost_bps: Annotated[FiniteFloat, Field(ge=0)]
    risk_constraints: list[RiskConstraint]
    development: DateWindow
    sealed: list[DateWindow]
    data: DataScope
    code: CodeScope
    allowed_changes: Annotated[list[ChangeAxis], Field(min_length=1)]
    execution_profile: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{1,79}$")]
    execution_profile_digest: Digest
    resources: ResourcePolicy
    search: SearchPolicy
    baseline_source_ids: SourceIds

    @model_validator(mode="after")
    def scope_consistency(self):
        if self.stress_cost_bps < self.base_cost_bps:
            raise ValueError("Stress cost must be at least the base cost")
        if len({risk.metric for risk in self.risk_constraints}) != len(self.risk_constraints):
            raise ValueError("Risk constraints must be unique")
        if len(set(self.allowed_changes)) != len(self.allowed_changes):
            raise ValueError("Change axes must be unique")
        for window in self.sealed:
            if window.start <= self.development.end and window.end >= self.development.start:
                raise ValueError("Development and sealed windows overlap")
        return self


class HypothesisProposal(MissionModel):
    schema_version: Literal[1] = 1
    id: UUID
    author: Text
    hypothesis: Text
    expected_effect: Text
    falsification: Text
    comparison: Text
    change_axes: Annotated[list[ChangeAxis], Field(min_length=1)]
    source_ids: SourceIds
    predecessor_trial_ids: list[UUID]
    supersedes_proposal_id: UUID | None = None


class Challenge(MissionModel):
    schema_version: Literal[1] = 1
    id: UUID
    proposal_id: UUID
    reviewer: Text
    concern: Text
    test: Text
    source_ids: SourceIds


class EvidenceRef(MissionModel):
    path: Path
    sha256: Digest


class TrialPlan(MissionModel):
    schema_version: Literal[1] = 1
    trial_id: UUID
    proposal_id: UUID
    mission_digest: Digest
    execution_profile: str
    implementer: Text
    repository: Literal["quant-lab"]
    code_commit: Commit
    changed_paths: Annotated[list[Path], Field(min_length=1)]
    config_files: FileMap
    input_files: FileMap
    lake_id: Text
    development: DateWindow
    worker_id: Literal["worker"]
    hostname: Literal["DESKTOP-5T00NAF"]
    gpu: Literal["NVIDIA GeForce RTX 3070"]


class MetricValue(Objective):
    value: FiniteFloat


class TrialMetrics(MissionModel):
    primary: MetricValue
    risks: dict[RiskMetric, Annotated[FiniteFloat, Field(ge=0)]]
    sample_count: int = Field(gt=0, strict=True)
    sample_window: DateWindow


class TrialOutcome(MissionModel):
    schema_version: Literal[1] = 1
    id: UUID
    plan: TrialPlan
    status: Literal["result", "technical_failure"]
    started_at: datetime
    finished_at: datetime
    evidence_files: FileMap
    qualification: EvidenceRef | None = None
    result: EvidenceRef | None = None
    metrics: TrialMetrics | None = None
    failure_code: Annotated[str, Field(pattern=r"^[a-z_]{1,80}$")] | None = None
    resume_condition: Text | None = None

    @field_validator("started_at", "finished_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Execution timestamps must have a timezone")
        return value

    @model_validator(mode="after")
    def complete_evidence(self):
        if self.finished_at < self.started_at:
            raise ValueError("Execution timestamps are reversed")
        if self.status == "result":
            if not self.qualification or not self.result or not self.metrics:
                raise ValueError("A qualified result requires artifacts and nonempty typed metrics")
            if self.failure_code is not None or self.resume_condition is not None:
                raise ValueError("A result cannot contain a technical failure")
            for reference in (self.qualification, self.result):
                if self.evidence_files.get(reference.path) != reference.sha256:
                    raise ValueError("Producer artifact is missing from the evidence map")
            if self.metrics.sample_window != self.plan.development:
                raise ValueError("Result date window differs from the submitted plan")
        elif (not self.failure_code or not self.resume_condition or self.metrics is not None
              or self.qualification is not None or self.result is not None):
            raise ValueError("Technical failure requires a resume condition and no scientific result")
        return self


class Interpretation(MissionModel):
    schema_version: Literal[1] = 1
    trial_id: UUID
    author: Text
    outcome_digest: Digest
    conclusion: Text
    next_hypothesis: Text
    source_ids: SourceIds


class AuditPublication(MissionModel):
    """Only an internal verifier may produce this after checking the actual audit files."""

    schema_version: Literal[1] = 1
    trial_id: UUID
    mission_digest: Digest
    outcome_digest: Digest
    source_id: Text
    audit_files: FileMap
    verification: EvidenceRef
    report: EvidenceRef
    verifier_role: Text
    verifier_commit: Commit
    published_at: datetime

    @field_validator("published_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Publication timestamp must have a timezone")
        return value
