from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from pydantic import Field, StrictInt, model_validator

from ..contracts import StrictModel

SIGNAL_KIND = "trading_signal"
SignalKind = Literal["intraday", "overnight"]
Direction = Literal["long", "short"]
Timing = Literal["on_time", "late", "after_entry"]

MAX_TOOL_TURNS = 3
MAX_SEARCHES = 2
MAX_READS = 4
MAX_FORMAT_RETRIES = 2
MAX_FAULT_RETRIES = 2
FAULT_RETRY_MINUTES = 5
FALLBACK_DIRECTION = "long"
# Owner 2026-11-24: the call and its confidence come from the day's situation and evidence, never from past hit rates.
# The confidence is the chance that today's call is right: even (50) plus the points the Analyst gives each of today's
# reasons, computed by the service so it is never a picked number.
EVEN_CONFIDENCE = 50
MAX_REASON_POINTS = 15
MAX_CONFIDENCE = 90


@dataclass(frozen=True)
class Slot:
    day: date
    kind: SignalKind
    snapshot_at: datetime
    snapshot_latest: datetime
    tools_until: datetime
    fallback_at: datetime
    send_at: datetime
    entry_at: datetime
    exit_at: datetime
    exit_day: date


class SignalReason(StrictModel):
    text: str = Field(min_length=1, max_length=200)
    # How many percentage points this reason of today moves the chance that the call is right; against the call < 0.
    points: StrictInt = Field(ge=-MAX_REASON_POINTS, le=MAX_REASON_POINTS)
    source_ids: list[str] = Field(min_length=1, max_length=5)


class SignalProposal(StrictModel):
    """The Analyst's answer. It has no confidence field: the service computes it (50 + evidence_points). Points that
    sum below 0 favour the other direction, and above +40 the confidence would pass 90: both are format errors."""
    kind: SignalKind
    session_day: date
    direction: Direction
    reasons: list[SignalReason] = Field(min_length=1, max_length=3)
    invalidation: str = Field(min_length=1, max_length=200)
    source_ids: list[str] = Field(min_length=1, max_length=20)

    @property
    def evidence_points(self) -> int:
        return sum(reason.points for reason in self.reasons)

    @property
    def confidence(self) -> int:
        return EVEN_CONFIDENCE + self.evidence_points

    @model_validator(mode="after")
    def _evidence_supports_the_call(self):
        if self.evidence_points < 0:
            raise ValueError(f"points sum {self.evidence_points:+d}: the reasons favour the other direction; change the "
                             "direction or the points")
        if self.confidence > MAX_CONFIDENCE:
            raise ValueError(f"points sum {self.evidence_points:+d}: at most +{MAX_CONFIDENCE - EVEN_CONFIDENCE} "
                             f"(confidence {MAX_CONFIDENCE})")
        return self

    def stored(self) -> dict:
        """The proposal as trading_signals.proposal keeps it, with the computed confidence."""
        return {**self.model_dump(mode="json"), "confidence": self.confidence}
