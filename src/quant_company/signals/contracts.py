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
# Owner 2026-11-24: the confidence is computed, not picked. It is the past hit rate of the chosen direction (fixed with
# the baselines before any result) plus the points the Analyst gives each reason. Intraday long hit 48.6% (KOSPI) and
# 49.9% (KODEX 200), so even; overnight long hit 59.4% (KOSPI).
BASE_RATES = {"intraday": {"long": 50, "short": 50}, "overnight": {"long": 59, "short": 41}}
MAX_REASON_POINTS = 10


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
    # How many percentage points this reason moves the chance that the chosen direction is right; against it < 0.
    points: StrictInt = Field(ge=-MAX_REASON_POINTS, le=MAX_REASON_POINTS)
    source_ids: list[str] = Field(min_length=1, max_length=5)


class SignalProposal(StrictModel):
    """The Analyst's answer. It has no confidence field: the service computes it (base_rate + evidence_points), and
    a direction whose computed confidence is below 50 is a format error, since the other side is then likelier."""
    kind: SignalKind
    session_day: date
    direction: Direction
    reasons: list[SignalReason] = Field(min_length=1, max_length=3)
    invalidation: str = Field(min_length=1, max_length=200)
    source_ids: list[str] = Field(min_length=1, max_length=20)

    @property
    def base_rate(self) -> int:
        return BASE_RATES[self.kind][self.direction]

    @property
    def evidence_points(self) -> int:
        return sum(reason.points for reason in self.reasons)

    @property
    def confidence(self) -> int:
        return self.base_rate + self.evidence_points

    @model_validator(mode="after")
    def _likelier_side(self):
        if not 50 <= self.confidence <= 90:
            raise ValueError(f"confidence {self.confidence} = base {self.base_rate} {self.evidence_points:+d} points: "
                             "below 50 means the other direction is likelier; change the direction or the points")
        return self

    def stored(self) -> dict:
        """The proposal as trading_signals.proposal keeps it, with the computed base rate and confidence."""
        return {**self.model_dump(mode="json"), "base_rate": self.base_rate, "confidence": self.confidence}
