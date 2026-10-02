"""Explicit conditional scope. Unknown gaps and legacy contracts cannot opt into it."""

import hashlib
import json
from pathlib import PurePosixPath
from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from ..contracts import StrictModel
from .contracts import Digest

DataMode = Literal["historical_point_in_time", "frozen_vintage_retrospective"]
ResultScope = Literal["historical_development", "conditional_retrospective_development"]
ScopeGapCode = Literal[
    "historical_publication_unverified", "historical_revision_vintage_unverified",
    "original_preparation_bytes_unavailable",
]
SCOPE_GAPS = frozenset({
    "historical_publication_unverified", "historical_revision_vintage_unverified",
    "original_preparation_bytes_unavailable",
})


def record_digest(value):
    """Same UTF-8/default separators as Company.fingerprint; no encoding migration."""
    payload = value.model_dump(mode="json") if isinstance(value, StrictModel) else value
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class PolicyModel(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PolicyEvidenceRef(PolicyModel):
    name: Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}$")]
    sha256: Digest


class ResearchDataPolicy(PolicyModel):
    schema_version: Literal[1] = 1
    mode: DataMode
    input_files: Annotated[dict[str, Digest], Field(min_length=1)]
    evidence_refs: Annotated[list[PolicyEvidenceRef], Field(min_length=1, max_length=12)]
    availability_status: Literal["verified_historical", "unverified_historical"]
    assumed_available_at_rule: Literal["trade_day_T23:59:00+09:00"] | None
    result_scope: ResultScope
    acknowledged_gap_codes: list[ScopeGapCode]

    @field_validator("input_files")
    @classmethod
    def paths(cls, value):
        for name in value:
            path = PurePosixPath(name)
            if (not name or path.is_absolute() or str(path) != name or name == "."
                    or ".." in path.parts or "\\" in name or ":" in name
                    or any(ord(char) < 32 or ord(char) == 127 for char in name)):
                raise ValueError("Policy input names must be normalized relative paths")
        return value

    @model_validator(mode="after")
    def scope(self):
        if len(set(self.acknowledged_gap_codes)) != len(self.acknowledged_gap_codes):
            raise ValueError("Duplicate acknowledged gap")
        if len({ref.name for ref in self.evidence_refs}) != len(self.evidence_refs):
            raise ValueError("Duplicate policy evidence reference")
        if self.mode == "frozen_vintage_retrospective":
            if (self.availability_status != "unverified_historical"
                    or self.assumed_available_at_rule != "trade_day_T23:59:00+09:00"
                    or self.result_scope != "conditional_retrospective_development"
                    or not {"historical_publication_unverified", "historical_revision_vintage_unverified"}
                    <= set(self.acknowledged_gap_codes)):
                raise ValueError("Conditional research must retain its unverified historical availability")
        elif (self.availability_status != "verified_historical" or self.assumed_available_at_rule is not None
              or self.result_scope != "historical_development" or self.acknowledged_gap_codes):
            raise ValueError("Historical research cannot waive historical evidence")
        return self


class OriginatingTaskRef(PolicyModel):
    program_id: UUID
    task_id: UUID
    task_digest: Digest


class ScientificLineageAuthority(PolicyModel):
    id: UUID
    max_total_trials: int = Field(gt=0, strict=True)
    history_digest: Digest
    originating_task_refs: Annotated[list[OriginatingTaskRef], Field(max_length=20)]

    @model_validator(mode="after")
    def unique(self):
        if len({ref.task_id for ref in self.originating_task_refs}) != len(self.originating_task_refs):
            raise ValueError("Duplicate scientific origin task")
        return self


class ResearchScope(PolicyModel):
    data_policy_digest: Digest
    data_mode: DataMode
    result_scope: ResultScope
    scientific_lineage_id: UUID
    acknowledged_gap_codes: list[ScopeGapCode]

    @model_validator(mode="after")
    def scope(self):
        if len(set(self.acknowledged_gap_codes)) != len(self.acknowledged_gap_codes):
            raise ValueError("Duplicate scope limitation")
        conditional = self.data_mode == "frozen_vintage_retrospective"
        if conditional != (self.result_scope == "conditional_retrospective_development"):
            raise ValueError("Data mode and result scope disagree")
        if not conditional and self.acknowledged_gap_codes:
            raise ValueError("Historical scope cannot acknowledge evidence gaps")
        return self


class ScopedRecord(PolicyModel):
    research_scope: ResearchScope | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def scope_version(self):
        if (self.schema_version == 2) != (self.research_scope is not None):
            raise ValueError("Scoped records require version 2; legacy records cannot carry conditional scope")
        return self


def scoped_kwargs(spec):
    scope = spec.research_scope
    return {} if scope is None else {"schema_version": 2, "research_scope": scope}


def require_scope(spec, value):
    if value.research_scope != spec.research_scope:
        raise ValueError("Research policy, scientific lineage or result scope differs from the approved mission")


def require_profile_policy(spec, profile):
    scope = spec.research_scope
    expected = (None, None) if scope is None else (scope.data_policy_digest, scope.result_scope)
    if (profile.data_policy_digest, profile.result_scope) != expected:
        raise ValueError("Execution profile has a different data policy or result scope")
