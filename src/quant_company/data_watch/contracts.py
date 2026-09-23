"""Bounded receipts and operator-owned publication calendars. No inferred holidays."""

from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from ..contracts import StrictModel
from ..research.contracts import Recipe
from ..research.recipes import recipe_digest
from ..research.worker import canonical_sha

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Dataset = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")]
METHOD = "etf-input-quality-v1"
MAX_INPUT_BYTES = 512 * 1024 * 1024


class ObjectVersion(StrictModel):
    uri: str = Field(max_length=1000)
    size_bytes: int = Field(ge=0)
    last_modified: AwareDatetime
    etag: str | None = Field(default=None, max_length=200)
    version_id: str | None = Field(default=None, max_length=1000)


class CatalogItem(ObjectVersion):
    dataset: Dataset


class DueDate(StrictModel):
    data_date: date
    expected_after: AwareDatetime


class FreshnessContract(StrictModel):
    dataset: Dataset
    date_column: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]{0,79}$")
    evidence: str = Field(min_length=10, max_length=1000)
    valid_until: AwareDatetime
    dates: list[DueDate] = Field(min_length=1, max_length=1500)

    @model_validator(mode="after")
    def ordered(self):
        if (len({d.data_date for d in self.dates}) != len(self.dates)
                or self.dates != sorted(self.dates, key=lambda d: d.data_date)
                or any(a.expected_after >= b.expected_after for a, b in zip(self.dates, self.dates[1:], strict=False))
                or self.valid_until <= self.dates[-1].expected_after):
            raise ValueError("publication_calendar_must_be_ordered_and_bounded")
        return self


def load_contracts(path: Path | None) -> dict[str, FreshnessContract]:
    import json

    if path is None:
        return {}
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("data_watch_contracts_too_large")
    items = [FreshnessContract.model_validate(item) for item in json.loads(path.read_text())]
    if len(items) > 200 or len({c.dataset for c in items}) != len(items):
        raise ValueError("duplicate_or_excess_data_contracts")
    return {c.dataset: c for c in items}


def freshness(contract, descriptor, at: datetime):
    if contract is None or at > contract.valid_until:
        return {"state": "unregistered", "label": "기준 미등록"}
    due = [d for d in contract.dates if d.expected_after <= at]
    if not due:
        return {"state": "unregistered", "label": "기준 미등록 (적용 기간 전)"}
    expected = due[-1].data_date
    bound = (descriptor or {}).get("data", {}).get("date_bounds", {}).get(contract.date_column, {})
    try:
        actual = date.fromisoformat(str(bound["max"])[:10]) if bound.get("statistics_complete") else None
    except (KeyError, ValueError):
        actual = None
    return {"state": "unchecked" if actual is None else "stale" if actual < expected else "fresh",
            "expected": str(expected), "actual": str(actual) if actual else None,
            "contract": canonical_sha(contract.model_dump(mode="json"))}


class CoreScope(StrictModel):
    recipe_id: Literal["kr-etf-p11-replay-v1"]
    recipe_digest: Digest
    lake_id: str = Field(max_length=300)
    input_files: dict[str, Digest] = Field(min_length=6, max_length=6)
    method: Literal["etf-input-quality-v1"] = METHOD

    @classmethod
    def from_recipe(cls, recipe: Recipe):
        return cls(recipe_id=recipe.id, recipe_digest=recipe_digest(recipe),
                   lake_id=recipe.lake_id, input_files=recipe.input_files)

    def digest(self):
        return canonical_sha(self.model_dump(mode="json"))


class CheckAssignment(StrictModel):
    id: UUID
    research_job_id: UUID
    revision: int = Field(ge=1)
    scope: CoreScope
    scope_digest: Digest
    lease_token: Digest


class FileCheck(StrictModel):
    sha256: Digest
    bytes: int = Field(ge=0, le=MAX_INPUT_BYTES)
    rows: int | None = Field(default=None, ge=0, le=4000000)
    columns: list[str] = Field(default_factory=list, max_length=100)
    nulls: dict[str, int] = Field(default_factory=dict, max_length=100)
    duplicate_keys: int = Field(default=0, ge=0)
    invalid_values: int = Field(default=0, ge=0)
    min_date: date | None = None
    max_date: date | None = None
    errors: list[Literal["missing_columns", "empty_input", "invalid_keys", "duplicate_keys",
                         "invalid_values", "outside_window"]] = Field(default_factory=list, max_length=6)


class CheckReceipt(StrictModel):
    check_id: UUID
    scope_digest: Digest
    method: Literal["etf-input-quality-v1"] = METHOD
    checked_at: AwareDatetime
    state: Literal["checked", "unavailable"]
    files: dict[str, FileCheck] = Field(default_factory=dict, max_length=6)
    error: Literal["input_identity_changed", "input_unavailable", "reader_unavailable", "read_limit",
                   "reader_timeout", "reader_failed", "checker_busy", "scope_not_registered"] | None = None
    scope: Literal["entire_frozen_input"] = "entire_frozen_input"
    scientific_trials_added: Literal[0] = 0
    sealed_read: Literal[False] = False

    @model_validator(mode="after")
    def coherent(self):
        if (self.state == "checked") != (len(self.files) == 6 and self.error is None):
            raise ValueError("incomplete_core_receipt")
        if self.state == "unavailable" and (self.files or not self.error):
            raise ValueError("unavailable_receipt_must_not_claim_checks")
        return self
