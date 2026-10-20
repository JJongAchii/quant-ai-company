"""Typed planning records. Employees propose; the service validates, ranks and decides."""

from typing import Annotated, Literal, get_args
from uuid import UUID

from pydantic import Field, model_validator

from ..quant_feed.contracts import ResearchBrief
from .mission_contracts import MissionModel, MissionSpec, Objective, SourceIds, Text
from .program_contracts import ResearchTaskProposal, SourceCitation

Term = Annotated[str, Field(min_length=1, max_length=80)]
Short = Annotated[str, Field(min_length=1, max_length=300)]
Ref = Annotated[str, Field(min_length=1, max_length=200)]
TOPICS = get_args(ResearchBrief.model_fields["topic"].annotation)
Topic = Literal[TOPICS]
METRICS = get_args(Objective.model_fields["metric"].annotation)
Estimand = Literal[METRICS + ("other",)]
ExecutionTiming = Literal["prior_session_open_fill", "same_day_close_fill", "intraday", "not_applicable", "other"]
ImplementationScope = Literal["config_only", "new_feature", "new_model", "new_data", "unknown"]
ContributionKind = Literal["alpha_signal", "portfolio_method", "execution_method", "data_method", "validation_method"]
GapKind = Literal["data_packet", "evaluator", "execution_timing", "data_policy", "program_missing", "program_budget",
                  "lineage_budget", "source_currency", "source_coverage", "oversize", "read_budget", "output_contract"]
TriageCode = Literal["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9"]
TriageOutcome = Literal["eligible", "unbound", "invalid", "stale", "out_of_scope", "duplicate", "needs_program",
                        "needs_evaluator", "needs_data"]
# Most terminal first: an out-of-scope candidate is not merely waiting for data.
OUTCOME_PRECEDENCE = ("invalid", "stale", "out_of_scope", "duplicate", "needs_program", "needs_evaluator",
                      "needs_data")


class QuestionSpec(MissionModel):
    """What the owner wants answered. Undetermined mandates stay 'unknown'; nothing is invented."""

    schema_version: Literal[1] = 1
    question: Annotated[str, Field(min_length=8, max_length=1000)]
    decision_to_inform: Annotated[str, Field(min_length=8, max_length=1000)]
    market: Literal["kr_etf", "kr_stock", "any"]
    mandate: Annotated[str, Field(min_length=1, max_length=600)] = "unknown"
    search_terms_en: Annotated[list[Term], Field(max_length=12)] = []
    search_terms_ko: Annotated[list[Term], Field(max_length=12)] = []
    counter_terms: Annotated[list[Term], Field(max_length=12)] = []
    topics: Annotated[list[Topic], Field(max_length=6)] = []
    required_data_fields: Annotated[list[Term], Field(max_length=20)] = []
    exclusions: Annotated[list[Short], Field(max_length=20)] = []
    source_refs: Annotated[list[Ref], Field(max_length=20)] = []
    envelope_hint: Annotated[str, Field(min_length=1, max_length=61)] | None = None
    estimand_hint: Estimand | None = None

    @model_validator(mode="after")
    def bounded(self):
        if not (self.search_terms_en or self.search_terms_ko):
            raise ValueError("A research question needs English or Korean search terms")
        for values in (self.search_terms_en, self.search_terms_ko, self.counter_terms, self.topics,
                       self.required_data_fields, self.exclusions, self.source_refs):
            if len(set(values)) != len(values):
                raise ValueError("Question lists cannot repeat a value")
        return self


class ContradictionSearch(MissionModel):
    status: Literal["found", "searched_not_found", "not_searched"]
    refs: Annotated[list[SourceCitation], Field(max_length=10)] = []
    scope_note: Annotated[str, Field(max_length=600)] = ""

    @model_validator(mode="after")
    def refs_match_status(self):
        if (self.status == "found") != bool(self.refs):
            raise ValueError("Only a found contradiction carries original citations")
        return self


class LineageRelation(MissionModel):
    status: Literal["new", "extends", "repeats_negative", "unknown"]
    lineage_refs: Annotated[list[Ref], Field(max_length=20)] = []
    difference: Annotated[str, Field(max_length=1200)] = ""

    @model_validator(mode="after")
    def stated_difference(self):
        if self.status == "repeats_negative" and not self.difference.strip():
            raise ValueError("Repeating a negative lineage result requires the stated difference")
        return self


class PlanningCandidate(MissionModel):
    # Same names and meaning as ResearchTaskProposal; a bound run registers these fields verbatim.
    title: Text
    question: Text
    mode: Literal["exact_replication", "market_transfer", "novel_hypothesis"]
    original_claim: Text
    method: Text
    departures: Text
    falsification: Text
    source_ids: SourceIds
    citations: Annotated[list[SourceCitation], Field(min_length=1, max_length=50)]
    predecessor_mission_ids: list[UUID]
    envelope: Text | None
    # Planning only; never projected into the program task.
    contribution_kind: ContributionKind
    mechanism: Text
    baseline_comparator: Text
    estimand: Estimand
    execution_timing: ExecutionTiming
    implementation_scope: ImplementationScope
    required_data_fields: Annotated[list[Term], Field(max_length=20)] = []
    unknowns: Annotated[list[Short], Field(max_length=10)] = []
    contradiction_search: ContradictionSearch
    lineage_relation: LineageRelation


def task_proposal(candidate: PlanningCandidate) -> ResearchTaskProposal:
    """The exact program task a bound candidate becomes; there is no re-proposal step."""
    if candidate.envelope is None:
        raise ValueError("A candidate without an envelope has no experiment path")
    return ResearchTaskProposal.model_validate(
        candidate.model_dump(mode="json", include=set(ResearchTaskProposal.model_fields)))


class GapRequest(MissionModel):
    kind: GapKind
    subject: Annotated[str, Field(min_length=1, max_length=300)]
    resolver: Literal["employee", "operator", "owner"]
    required_evidence: Annotated[str, Field(min_length=1, max_length=600)]
    recheck_on: Annotated[str, Field(min_length=1, max_length=300)]


class TriageCheck(MissionModel):
    code: TriageCode
    status: Literal["pass", "fail", "not_applicable", "not_declared"]
    outcome: TriageOutcome | None = None
    message: Annotated[str, Field(max_length=600)] = ""
    gap: GapRequest | None = None

    @model_validator(mode="after")
    def failure_shape(self):
        if (self.status == "fail") != (self.outcome is not None):
            raise ValueError("Only a failed check carries an outcome")
        if self.gap is not None and self.status != "fail":
            raise ValueError("Only a failed check opens a gap")
        return self


class TriageResult(MissionModel):
    schema_version: Literal[1] = 1
    outcome: TriageOutcome
    checks: Annotated[list[TriageCheck], Field(min_length=9, max_length=9)]
    basis: dict[str, str | int | None]

    @model_validator(mode="after")
    def ordered(self):
        if [check.code for check in self.checks] != [f"T{n}" for n in range(1, 10)]:
            raise ValueError("Triage records every check once, in order")
        return self


def summarize(checks, *, bound):
    failed = {check.outcome for check in checks if check.status == "fail"}
    for outcome in OUTCOME_PRECEDENCE:
        if outcome in failed:
            return outcome
    return "eligible" if bound else "unbound"


class EngineRuleCard(MissionModel):
    id: str
    version: int
    decision_information: str
    fill: str
    returns: str
    supported_execution_timings: list[ExecutionTiming]
    basis: list[str]


ENGINE_RULE_CARDS = {card.id: card for card in (
    EngineRuleCard(
        id="kr-research-python-v2", version=1,
        decision_information="Decisions use information available before 09:00 KST: the prior session and earlier.",
        fill="Same-day open.", returns="Open to next open, after the approved cost model.",
        supported_execution_timings=["prior_session_open_fill"],
        basis=["docs/adr/0037-literature-grounded-research-programs.md",
               "docs/project/evidence/research-programs-20260929/etf-data-evidence-note.json"]),
)}


def engine_card_key(spec: MissionSpec) -> str:
    # Same transport selection as worker job binding in MissionStore.
    return "kr-research-python-v2" if spec.schema_version >= 2 else spec.execution_profile


def engine_card(spec: MissionSpec) -> EngineRuleCard | None:
    return ENGINE_RULE_CARDS.get(engine_card_key(spec))


# Policy defaults from the design (14.2). Changing them is a policy revision.
PLANNING_LIMITS = {"slate_turns": 36, "critique_turns": 28, "decision_turns": 12, "attempts": 3,
                   "run_requests": 90, "owner_day_requests": 120, "owner_concurrent_runs": 1}


class PlanningSlate(MissionModel):
    schema_version: Literal[1] = 1
    candidates: Annotated[list[PlanningCandidate], Field(max_length=3)]
    no_candidate_reason: Annotated[str, Field(min_length=1, max_length=1200)] | None = None
    reading_notes: Annotated[str, Field(min_length=1, max_length=3000)]

    @model_validator(mode="after")
    def reasoned(self):
        if bool(self.candidates) == (self.no_candidate_reason is not None):
            raise ValueError("Give candidates, or no candidates with a no_candidate_reason")
        return self


Score = Literal[0, 1, 2, 3, "unknown"]
Route = Literal["experiment", "literature_only", "remediation", "awaiting_program", "defer", "reject"]


class CandidateReview(MissionModel):
    candidate_ordinal: Annotated[int, Field(ge=1, le=3)]
    verdict: Literal["support", "weak", "reject"]
    economic_rationale: Text
    alternative_explanations: Text
    decisive_test: Text
    cost_and_capacity_concerns: Text
    counter_evidence: Annotated[list[SourceCitation], Field(max_length=10)] = []
    evidence_quality: Score
    decision_value: Score
    novelty: Literal["duplicate", "extension", "distinct", "unknown"]
    required_checks: Annotated[list[Short], Field(max_length=10)] = []
    blockers: Annotated[list[GapRequest], Field(max_length=5)] = []


class PlanningCritique(MissionModel):
    schema_version: Literal[1] = 1
    reviews: Annotated[list[CandidateReview], Field(min_length=1, max_length=3)]

    @model_validator(mode="after")
    def one_review_each(self):
        ordinals = [item.candidate_ordinal for item in self.reviews]
        if len(set(ordinals)) != len(ordinals):
            raise ValueError("Review each candidate exactly once")
        return self


class RouteChoice(MissionModel):
    candidate_ordinal: Annotated[int, Field(ge=1, le=3)]
    route: Route
    rationale: Text


class PlanningDecision(MissionModel):
    schema_version: Literal[1] = 1
    order: Annotated[list[Annotated[int, Field(ge=1, le=3)]], Field(min_length=1, max_length=3)]
    selected_ordinal: Annotated[int, Field(ge=1, le=3)] | None = None
    routes: Annotated[list[RouteChoice], Field(min_length=1, max_length=3)]
    override_reason: Annotated[str, Field(min_length=1, max_length=1200)] | None = None
    follow_up_questions: Annotated[list[QuestionSpec], Field(max_length=2)] = []
    owner_summary: Annotated[str, Field(min_length=1, max_length=1200)]

    @model_validator(mode="after")
    def consistent(self):
        routed = [item.candidate_ordinal for item in self.routes]
        if len(set(self.order)) != len(self.order) or sorted(self.order) != sorted(routed) or len(set(routed)) != len(routed):
            raise ValueError("order and routes must cover each candidate once")
        experiments = [item.candidate_ordinal for item in self.routes if item.route == "experiment"]
        if len(experiments) > 1 or experiments != ([self.selected_ordinal] if self.selected_ordinal else []):
            raise ValueError("selected_ordinal must be the single experiment route")
        return self


TRIAGE_CLASS = {"eligible": 0, "unbound": 1, "needs_program": 1, "needs_evaluator": 1, "needs_data": 1,
                "duplicate": 2, "out_of_scope": 2, "stale": 2, "invalid": 2}
VERDICT_RANK = {"support": 0, "weak": 1, "reject": 2}
SCORE_RANK = {3: 0, 2: 1, 1: 2, 0: 3, "unknown": 4}
SCOPE_RANK = {"config_only": 0, "new_feature": 1, "new_model": 2, "new_data": 3, "unknown": 4}
NOVELTY_RANK = {"distinct": 0, "extension": 1, "unknown": 2, "duplicate": 3}


def service_order(candidates, triage, reviews):
    """Design 11: the same candidates, reviews and policy always give the same order."""
    return sorted(candidates, key=lambda ordinal: (
        TRIAGE_CLASS[triage[ordinal]], VERDICT_RANK[reviews[ordinal].verdict],
        SCORE_RANK[reviews[ordinal].evidence_quality], SCORE_RANK[reviews[ordinal].decision_value],
        SCOPE_RANK[candidates[ordinal].implementation_scope], NOVELTY_RANK[reviews[ordinal].novelty], ordinal))
