"""Slack post copy for trading signals. Pure text; the Analyst's prose only fills the reason and condition lines."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from ..briefing.schedule import KST
from .contracts import Direction, SignalProposal, Slot
from .snapshot import CATALOGUE

WEEKDAYS = "월화수목금토일"
LABELS = {"intraday": ("📈", "장중 신호"), "overnight": ("🌙", "오버나잇 신호")}
KIND_WORDS = {"intraday": "장중", "overnight": "오버나잇"}
DIRECTION_WORDS = {"long": "롱", "short": "숏"}
FALLBACK_HIT = {"intraday": "약 50%", "overnight": "약 59%"}
QUOTE_ORDER = {
    "intraday": ("es", "usdkrw", "ewy", "kospi"),
    "overnight": ("kospi", "es", "usdkrw", "n225"),
}
SCORECARD_FOOTER = "수수료·세금·호가 차이 제외. 숏은 KODEX 200 반대 방향으로 계산"
UNPARSED_FALLBACK = "Analyst 답 형식을 확인하지 못했습니다(원문은 위 글). 규칙 신호로 대신합니다."


def day_text(day: date) -> str:
    return f"{day.month}월 {day.day}일({WEEKDAYS[day.weekday()]})"


def clock(moment: datetime) -> str:
    return moment.astimezone(KST).strftime("%H:%M")


def window(slot: Slot) -> str:
    if slot.kind == "intraday":
        return f"시가 {clock(slot.entry_at)} 진입 → 종가 {clock(slot.exit_at)} 청산"
    return f"종가 {clock(slot.entry_at)} 진입 → {day_text(slot.exit_day)} 시가 {clock(slot.exit_at)} 청산"


def header(slot: Slot) -> str:
    emoji, label = LABELS[slot.kind]
    gap = "  " if slot.kind == "intraday" else " "
    return f"{emoji} {label} · {day_text(slot.day)}{gap}{window(slot)}"


def number(value) -> str:
    try:
        parsed = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    places = max(0, -parsed.as_tuple().exponent)
    return f"{parsed:,.{places}f}"


def quote_line(slot: Slot, snapshot: dict) -> str:
    parts = []
    for key in QUOTE_ORDER[slot.kind]:
        quote = snapshot["quotes"].get(key)
        if quote is None:
            continue
        label = CATALOGUE[key].label
        if CATALOGUE[key].live or quote.get("change_pct") is None:
            parts.append(f"{label} {number(quote['value'])}({clock(datetime.fromisoformat(quote['source_time']))})")
        else:
            parts.append(f"{label} {Decimal(quote['change_pct']):+.1f}%")
    head = f"기준 시세 {clock(datetime.fromisoformat(snapshot['observed_at']))}"
    return " · ".join([head, *parts])


def analyst_post(slot: Slot, proposal: SignalProposal, snapshot: dict, *,
                 late_at: datetime | None = None, fallback: Direction | None = None) -> str:
    emoji, label = LABELS[slot.kind]
    if late_at is None:
        first = [header(slot)]
    else:
        head = f"{emoji} {label} · 늦음 {clock(late_at)}"
        if fallback is not None:
            head += f" · 임시 신호와 {'같음' if proposal.direction == fallback else '반대'}"
        first = [head, window(slot)]
    lines = [*first, f"{DIRECTION_WORDS[proposal.direction]} · 확신도 {proposal.confidence}%", "근거"]
    lines += [f"{n}. {reason.text}" for n, reason in enumerate(proposal.reasons, 1)]
    lines += [f"틀렸다고 볼 조건: {proposal.invalidation}", quote_line(slot, snapshot)]
    return "\n".join(lines)


def fallback_post(slot: Slot, reason: str, *, unparsed: bool = False) -> str:
    """The model-free interim signal. After an unparsed answer no Analyst signal follows, so it promises none."""
    waiting = f"Analyst 신호가 늦어지고 있습니다(사유: {reason}). 나오는 대로 이어서 올립니다."
    return "\n".join([
        f"⏱️ 임시 신호 · {day_text(slot.day)} {KIND_WORDS[slot.kind]}  {window(slot)}",
        f"{DIRECTION_WORDS['long']} · Analyst 판단 아님 · 규칙 신호(항상 롱, 과거 적중 {FALLBACK_HIT[slot.kind]})",
        UNPARSED_FALLBACK if unparsed else waiting,
    ])


def unparsed_post(slot: Slot, say: str) -> str:
    emoji, label = LABELS[slot.kind]
    return "\n".join([f"{emoji} {label} · 형식 확인 실패 · 채점 제외", window(slot), say])


def scorecard_post(day: date, lines: list[str], summary: dict) -> str:
    return "\n".join([*lines, SCORECARD_FOOTER])
