from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator
from typing_extensions import TypedDict

from ..contracts import StrictModel
from ..web_fetch import public_url

BRIEFER = "market_brief"
Kind = Literal["am", "pm"]
Market = Literal["US", "KR"]

# Labels and units are service-owned, never chosen by a prose-generating model.
INSTRUMENTS = {
    "sp500": ("S&P 500", "pt", "US"), "nasdaq": ("나스닥 종합", "pt", "US"),
    "dow": ("다우", "pt", "US"), "sox": ("필라델피아 반도체", "pt", "US"),
    "vix": ("VIX", "pt", "US"), "ust2y": ("미 국채 2년", "%", "US"),
    "ust10y": ("미 국채 10년", "%", "US"), "kospi": ("코스피", "pt", "KR"),
    "kosdaq": ("코스닥", "pt", "KR"), "usdkrw": ("달러/원", "KRW/USD", "KR"),
    "kr_turnover": ("KRX 거래대금", "억원", "KR"),
    "kr_foreign": ("KRX 외국인 순매수", "억원", "KR"),
    "kr_institution": ("KRX 기관 순매수", "억원", "KR"),
    "wti": ("WTI 선물", "USD/bbl", "US"), "gold": ("금 현물", "USD/oz", "US"),
    "btc": ("비트코인", "USD", "US"), "eth": ("이더리움", "USD", "US"),
}


class SourceDocument(StrictModel):
    id: str
    url: str
    title: str
    publisher: str
    kind: Literal["official", "media", "calendar", "dataset"]
    origin_group: str = ""
    content: str = Field(min_length=1, max_length=12000)
    published_at: AwareDatetime | None
    retrieved_at: AwareDatetime
    sha256: str
    registration: str
    receipt: dict = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def https(cls, value):
        return public_url(value)


class Evidence(StrictModel):
    source_id: str
    quote: str = Field(min_length=10, max_length=650)


class SourceChoice(StrictModel):
    source_id: str
    reason: str = Field(min_length=10, max_length=240)


class SourcePlan(StrictModel):
    selections: list[SourceChoice] = Field(min_length=1, max_length=20)
    # Editorial priorities are questions for the writer, never additional evidence.
    priorities: list[str] = Field(min_length=1, max_length=6)


class Supported(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    evidence: list[Evidence] = Field(min_length=1, max_length=4)


class Claim(Supported):
    text: str = Field(min_length=1, max_length=500)
    kind: Literal["fact", "interpretation", "condition"] = "fact"


class ConditionPatch(StrictModel):
    replacements: list[Claim] = Field(min_length=1, max_length=6)


class FactAddition(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    text: str = Field(min_length=1, max_length=240)
    evidence: list[Evidence] = Field(default_factory=list, max_length=3)


class MaterialFactPatch(StrictModel):
    additions: list[FactAddition] = Field(min_length=1, max_length=6)


class ClaimEdit(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    text: str = Field(min_length=1, max_length=500)
    evidence: list[Evidence] = Field(default_factory=list, max_length=3)


class StoryContext(Claim):
    text: str = Field(min_length=1, max_length=300)
    kind: Literal["fact", "interpretation"] = "fact"


class StoryContextAddition(StrictModel):
    issue_fact_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    claim: StoryContext


class EditorialPatch(StrictModel):
    source_notes: list["SourceAssessment"] | None = Field(default=None, max_length=24)
    edits: list[ClaimEdit] = Field(default_factory=list, max_length=12)
    context_additions: list[StoryContextAddition] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.edits and not self.context_additions:
            raise ValueError("editorial_patch_requires_change")
        return self


class SourceNotesPatch(StrictModel):
    # Only the failed originals' inventory and related prose can be repaired.
    # Unaffected notes, quotes, prices and sections remain server-owned.
    source_notes: list["SourceAssessment"] = Field(min_length=1, max_length=24)
    edits: list[ClaimEdit] = Field(default_factory=list, max_length=8)
    context_additions: list[StoryContextAddition] = Field(default_factory=list, max_length=2)


class MarketObservation(Supported):
    instrument: str
    value: Decimal
    unit: str
    session_date: date
    as_of: AwareDatetime
    basis: Literal["close", "intraday", "after_hours", "provisional", "rolling_24h"]
    venue: str = Field(min_length=1, max_length=100)
    previous_value: Decimal | None = None
    previous_session_date: date | None = None
    reported_change: Decimal | None = None
    change_unit: Literal["%", "bp", "pt"] | None = None

    @field_validator("value", "previous_value", "reported_change")
    @classmethod
    def finite(cls, value):
        if value is not None and (not value.is_finite() or abs(value) > Decimal("1e15")):
            raise ValueError("finite_bounded_market_value_required")
        return value

    @model_validator(mode="after")
    def instrument_units(self):
        if self.instrument not in INSTRUMENTS or self.unit != INSTRUMENTS[self.instrument][1]:
            raise ValueError("registered_instrument_unit_required")
        if (self.previous_value is None) != (self.previous_session_date is None):
            raise ValueError("comparison_requires_both_value_and_session")
        if (self.reported_change is None) != (self.change_unit is None):
            raise ValueError("reported_change_requires_unit")
        if self.change_unit == "bp" and self.unit != "%":
            raise ValueError("basis_points_only_for_rates")
        return self


class CalendarEvent(Supported):
    title: str = Field(min_length=1, max_length=140)
    at: AwareDatetime | None
    source_timezone: str = Field(min_length=1, max_length=80)
    status: Literal["scheduled", "changed", "cancelled", "time_unconfirmed"]
    note: str = Field(default="", max_length=250)

    @model_validator(mode="after")
    def confirmed_time(self):
        if (self.at is None) != (self.status == "time_unconfirmed"):
            raise ValueError("unknown_event_time_must_be_explicit")
        return self


class AnalystAnalysis(StrictModel):
    horizon: Literal["session", "days_weeks", "months"]
    causal_basis: Literal["reported_explanation", "conditional_hypothesis", "unresolved"]
    mechanism: Claim
    alternative: Claim


class Issue(StrictModel):
    headline: str = Field(min_length=1, max_length=100)
    fact: Claim
    interpretation: Claim
    next_check: Claim
    analysis: AnalystAnalysis
    counterpoint: Claim | None = None
    context: list[StoryContext] = Field(default_factory=list, max_length=2)


class WatchResult(Supported):
    watch_id: str
    outcome: Literal["confirmed", "mixed", "pending"]
    explanation: str = Field(min_length=1, max_length=250)


class BriefProposal(StrictModel):
    # A private, source-bound fact inventory precedes prose. It is not a review
    # verdict and never appears in the reader's briefing or independent critic.
    source_notes: list["SourceAssessment"] = Field(default_factory=list, max_length=24)
    summary: list[Claim] = Field(max_length=3)
    overview: list[Claim] = Field(default_factory=list, max_length=3)
    observations: list[MarketObservation] = Field(max_length=18)
    issues: list[Issue] = Field(max_length=6)
    internals: list[Claim] = Field(max_length=3)
    watchpoints: list[Claim] = Field(max_length=3)
    watch_results: list[WatchResult] = Field(max_length=3)
    calendar: list[CalendarEvent] = Field(max_length=6)
    limitations: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def bounded_story_context(self):
        if sum(len(issue.context) for issue in self.issues) > 4:
            raise ValueError("brief_story_context_limit")
        return self


REVIEW_CHECKS = {"numbers", "sources", "timing", "causality", "materiality", "counterevidence",
                 "transmission", "alternatives", "falsifiability", "coverage", "depth", "readability"}


class ReviewChecks(TypedDict):
    numbers: bool
    sources: bool
    timing: bool
    causality: bool
    materiality: bool
    counterevidence: bool
    transmission: bool
    alternatives: bool
    falsifiability: bool
    coverage: bool
    depth: bool
    readability: bool


class FactAssessment(StrictModel):
    fact: str = Field(min_length=5, max_length=240)
    quote: str = Field(min_length=10, max_length=400)
    main_item_ids: list[str] = Field(max_length=6)


class SourceAssessment(StrictModel):
    source_id: str
    treatment: Literal["covered", "background", "not_material"]
    reason: str = Field(min_length=10, max_length=300)
    item_ids: list[str] = Field(default_factory=list, max_length=12)
    # An empty list is appropriate only when no distinct material fact needs coverage.
    material_facts: list[FactAssessment] = Field(max_length=6)


class FactQualifier(StrictModel):
    kind: Literal["comparison", "basis", "status", "scope", "driver", "counterevidence"]
    text: str = Field(min_length=2, max_length=80)


class InventoryFact(StrictModel):
    fact: str = Field(min_length=5, max_length=240)
    quote: str = Field(min_length=10, max_length=400)
    qualifiers: list[FactQualifier] = Field(default_factory=list, max_length=6)


class InventorySource(StrictModel):
    source_id: str
    treatment: Literal["covered", "background", "not_material"]
    reason: str = Field(min_length=10, max_length=300)
    material_facts: list[InventoryFact] = Field(max_length=6)


class FactInventory(StrictModel):
    sources: list[InventorySource] = Field(min_length=1, max_length=24)


class FactPlacement(StrictModel):
    fact_id: str = Field(pattern=r"^f[1-9][0-9]*$")
    main_item_ids: list[str] = Field(min_length=1, max_length=6)


class BriefComposition(BriefProposal):
    # The service restores the previously committed inventory; the writer only
    # supplies placements, never another copy that can quietly omit a fact.
    source_notes: list[SourceAssessment] = Field(default_factory=list, max_length=0)
    fact_placements: list[FactPlacement] = Field(max_length=144)
    supplemental_source_notes: list[SourceAssessment] = Field(default_factory=list, max_length=4)


class BriefReview(StrictModel):
    verdict: Literal["publish", "reduce", "withhold"]
    # Each criterion must be considered explicitly, never inferred from an empty rejection list.
    checks: ReviewChecks
    source_assessments: list[SourceAssessment] = Field(default_factory=list, max_length=24)
    source_requests: list[SourceChoice] = Field(default_factory=list, max_length=4)
    rejected_ids: list[str] = Field(default_factory=list, max_length=80)
    concerns: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def explicit_review(self):
        if set(self.checks) != REVIEW_CHECKS:
            raise ValueError("all_editorial_checks_required")
        if self.verdict == "publish" and not all(self.checks.values()):
            raise ValueError("failed_review_cannot_publish_as_full")
        if not all(self.checks.values()) and not self.concerns:
            raise ValueError("failed_review_requires_explanation")
        return self


class BriefEdition(StrictModel):
    id: str
    day: date
    kind: Kind
    due_at: AwareDatetime
    starts_at: AwareDatetime
    cutoff: AwareDatetime
    expires_at: AwareDatetime
    us_session: date | None
    kr_session: date | None
    previous_us_session: date | None
    previous_kr_session: date | None
    weekly: Literal["outlook", "review"] | None = None


class CalendarOverride(StrictModel):
    market: Market
    day: date
    closed: bool = False
    close_at: AwareDatetime | None = None
    source_url: str

    @field_validator("source_url")
    @classmethod
    def public_source(cls, value):
        return public_url(value)

    @model_validator(mode="after")
    def session(self):
        if self.closed == (self.close_at is not None):
            raise ValueError("override_requires_closure_or_close_time")
        return self


def item_map(proposal):
    items = [*proposal.summary, *proposal.overview, *proposal.observations, *proposal.internals,
             *proposal.watchpoints, *proposal.watch_results, *proposal.calendar]
    for issue in proposal.issues:
        items.extend([issue.fact, issue.interpretation, issue.next_check,
                      issue.analysis.mechanism, issue.analysis.alternative, *issue.context])
        if issue.counterpoint:
            items.append(issue.counterpoint)
    if len({item.id for item in items}) != len(items):
        raise ValueError("brief_item_ids_must_be_unique")
    return {item.id: item for item in items}
