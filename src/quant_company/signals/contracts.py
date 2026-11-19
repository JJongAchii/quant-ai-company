from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from pydantic import Field, StrictInt

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
    source_ids: list[str] = Field(min_length=1, max_length=5)


class SignalProposal(StrictModel):
    kind: SignalKind
    session_day: date
    direction: Direction
    confidence: StrictInt = Field(ge=50, le=90)
    reasons: list[SignalReason] = Field(min_length=1, max_length=3)
    invalidation: str = Field(min_length=1, max_length=200)
    source_ids: list[str] = Field(min_length=1, max_length=20)
