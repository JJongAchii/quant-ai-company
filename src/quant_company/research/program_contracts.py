"""Bounded owner authority and evidence-based task selection. No model can approve these."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from .contracts import Digest
from .mission_contracts import MissionModel, MissionSpec, SourceIds, Text


class ProgramEnvelope(MissionModel):
    """Exact data, code, evaluator and search policy; a task only specializes its question."""

    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{1,60}$")]
    market: Literal["kr_etf", "kr_stock"]
    template: MissionSpec

    @model_validator(mode="after")
    def market_contract(self):
        if self.market != (self.template.market or "kr_etf"):
            raise ValueError("Envelope market differs from its frozen evaluation contract")
        return self


class ResearchProgram(MissionModel):
    schema_version: Literal[1] = 1
    title: Text
    objective: Text
    envelopes: Annotated[list[ProgramEnvelope], Field(min_length=1, max_length=12)]
    max_total_trials: int = Field(gt=0, strict=True)
    max_compute_seconds: int = Field(gt=0, strict=True)
    max_missions: int = Field(gt=0, strict=True)
    max_parallel_missions: int = Field(default=1, ge=1, le=4, strict=True)
    source_ids: SourceIds
    include_quant_feed: bool = True

    @model_validator(mode="after")
    def bounded(self):
        if len({e.name for e in self.envelopes}) != len(self.envelopes):
            raise ValueError("Program envelope names must be unique")
        if self.max_parallel_missions > self.max_missions:
            raise ValueError("Concurrent missions exceed total mission allowance")
        for envelope in self.envelopes:
            if envelope.template.search.max_total_trials is None:
                raise ValueError("Each program task needs a finite scientific scope")
            if envelope.template.search.max_total_trials > self.max_total_trials:
                raise ValueError("Task scientific scope exceeds program allowance")
        return self


class SourceCitation(MissionModel):
    source_id: Text
    location: Text
    quote: Annotated[str, Field(min_length=8, max_length=600)]


class ResearchTaskProposal(MissionModel):
    title: Text
    envelope: Text
    question: Text
    mode: Literal["exact_replication", "market_transfer", "novel_hypothesis"]
    original_claim: Text
    method: Text
    departures: Text
    falsification: Text
    source_ids: SourceIds
    citations: Annotated[list[SourceCitation], Field(min_length=1, max_length=50)]
    predecessor_mission_ids: list[UUID]


class TaskDecision(MissionModel):
    decision: Literal["accept", "revise", "wait"]
    rationale: Text


class DataAssessment(MissionModel):
    decision: Literal["ready", "exploratory_only", "blocked"]
    rationale: Text
    source_ids: SourceIds
    point_in_time: bool
    coverage: bool
    executable_prices: bool
    original_conditions: bool
    data_policy_digest: Digest | None = Field(default=None, exclude_if=lambda v: v is None)
    evaluation_prices: bool | None = Field(default=None, strict=True, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def ready(self):
        if self.decision == "ready" and not all((self.point_in_time, self.coverage, self.executable_prices)):
            raise ValueError("Ready requires PIT, coverage and execution evidence")
        if self.decision == "exploratory_only" and (
            not self.coverage or self.evaluation_prices is not True or self.data_policy_digest is None
            or self.point_in_time or self.executable_prices or self.original_conditions
        ):
            raise ValueError("Exploration requires verified coverage/evaluation prices, a policy digest and unverified historical flags")
        return self


class ChallengeResponse(MissionModel):
    challenge_id: UUID
    disposition: Literal["revise", "test", "reject"]
    rationale: Text
    source_ids: SourceIds
    test_plan: Text | None = None

    @model_validator(mode="after")
    def testing(self):
        if self.disposition == "test" and self.test_plan is None:
            raise ValueError("A test response requires an executable test plan")
        return self


class ReviewDecision(MissionModel):
    decision: Literal["execute", "revise"]
    rationale: Text
    responses: Annotated[list[ChallengeResponse], Field(min_length=1)]


class TestAssessment(MissionModel):
    challenge_id: UUID
    conclusion: Literal["addressed", "unresolved"]
    evidence_paths: list[Text]
    rationale: Text

    @model_validator(mode="after")
    def evidence_required(self):
        if self.conclusion == "addressed" and not self.evidence_paths:
            raise ValueError("An addressed test needs actual artifact paths")
        return self


class MeaningReview(MissionModel):
    trial_id: UUID
    outcome_digest: Digest
    conclusion: Literal["supported", "not_supported", "inconclusive"]
    rationale: Text
    multiple_testing: Text
    execution_costs: Text
    alternative_explanations: Text
    unresolved: list[Text]
    tests: list[TestAssessment]

    @model_validator(mode="after")
    def unresolved_cannot_pass(self):
        if (self.unresolved or any(t.conclusion == "unresolved" for t in self.tests)) and self.conclusion == "supported":
            raise ValueError("Unresolved evidence cannot establish support")
        return self
