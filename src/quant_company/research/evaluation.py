"""Predeclared, deterministic scientific evaluation; no model-authored pass verdicts."""

from typing import Literal

from pydantic import Field, FiniteFloat, model_validator

from ..contracts import StrictModel


class EvaluationSpec(StrictModel):
    schema_version: Literal[1] = 1
    kind: Literal["replication", "claim", "strategy"]
    metric: Literal["stress-net-absolute-cagr", "absolute-replication-error", "mean-effect"]
    target: FiniteFloat | None = None
    tolerance: FiniteFloat | None = Field(default=None, ge=0)
    minimum_effect: FiniteFloat = 0
    minimum_blocks: int = Field(default=30, ge=30, strict=True)
    block: Literal["calendar-month"] = "calendar-month"
    confidence: Literal[0.95] = 0.95
    scope: Literal["development"] = "development"

    @model_validator(mode="after")
    def metric_matches(self):
        if self.metric != {"strategy": "stress-net-absolute-cagr", "replication": "absolute-replication-error",
                           "claim": "mean-effect"}[self.kind]:
            raise ValueError("Evaluation kind and metric disagree")
        if self.kind == "replication" and (self.target is None or self.tolerance is None):
            raise ValueError("Replication requires a fixed reference and tolerance")
        if self.kind != "replication" and (self.target is not None or self.tolerance is not None):
            raise ValueError("Only replication has a reference target")
        return self


def evaluate_observations(content, evaluation, window):
    from .reference.domestic_statistics import evaluate_observations as measure

    if evaluation is None:
        raise ValueError("A preregistered evaluation contract is required")
    return measure(content, evaluation.model_dump(), window.model_dump(mode="json"))
