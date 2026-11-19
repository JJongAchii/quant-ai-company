"""Slack post copy for trading signals. Pure text; the Analyst's prose only fills the reason and condition lines."""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from ..briefing.schedule import KST
from .contracts import Direction, SignalProposal, Slot
from .grading import BASELINES, INSTRUMENTS, KINDS, outcome
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
# A signal committed after the entry may have seen the open: posted until the exit, never graded.
AFTER_ENTRY = "진입 뒤(채점 제외)"
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


def analyst_post(slot: Slot, proposal: SignalProposal, snapshot: dict, *, late_at: datetime | None = None,
                 fallback: Direction | None = None, after_entry: bool = False) -> str:
    """The Analyst's signal. A late one (late_at) names its time, how it compares with the interim post (fallback) and,
    when committed after the entry, that it is not graded."""
    emoji, label = LABELS[slot.kind]
    if late_at is None:
        first = [header(slot)]
    else:
        head = f"{emoji} {label} · 늦음 {clock(late_at)}"
        if fallback is not None:
            head += f" · 임시 신호와 {'같음' if proposal.direction == fallback else '반대'}"
        if after_entry:
            head += f" · {AFTER_ENTRY}"
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


INSTRUMENT_WORDS = {"KOSPI": "KOSPI", "KODEX200": "KODEX 200"}
BASELINE_WORDS = {"always_long": "항상 롱", "follow_us": "미국 따라가기"}
MOVE_WORDS = {"intraday": "시가→종가", "overnight": "종가→시가"}
MARKS = {"hit": "✅", "miss": "❌", "tie": "➖"}
SIGNIFICANT = 0.05


def percent(bp) -> str:
    value = (Decimal(str(bp)) / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return f"{value if value else Decimal('0.00'):+.2f}%"


def with_particle(word: str, with_final: str, without_final: str) -> str:
    """word plus the Korean particle that fits its last syllable (과 after a final consonant, else 와)."""
    code = ord(word[-1]) - 0xAC00
    return word + (with_final if 0 <= code < 11172 and code % 28 else without_final)


def result_line(kind: str, day: date, direction: str | None, interim: bool, returns: dict, *, show_date: bool,
                instruments: tuple[str, ...] = INSTRUMENTS) -> str:
    """One slot of the scorecard: the label, then the market move and the signal result per instrument. direction is
    the signal that was graded (None when there was none, then only the market move is shown); interim marks a rule
    signal the Analyst did not give."""
    parts = []
    if show_date or kind == "overnight":
        parts.append(f"{day.month}/{day.day}")
    if direction:
        parts += (["임시"] if interim else []) + [DIRECTION_WORDS[direction]]
    label = KIND_WORDS[kind] + (f"({' '.join(parts)})" if parts else "")
    segments = []
    for instrument in instruments:
        result, name = returns.get(instrument), INSTRUMENT_WORDS[instrument]
        if result is None or result["state"] == "pending":
            segments.append(f"{name} 채점 보류")
        elif result["state"] == "excluded":
            segments.append(f"{name} {result.get('detail') or '제외'} 제외")
        else:
            bp = Decimal(str(result["long_bp"]))
            move = f"{MOVE_WORDS[kind]} {percent(bp)}"
            if not direction:
                segments.append(f"{name} {move}")
                continue
            signal = f"신호 {percent(bp if direction == 'long' else -bp)} {MARKS[outcome(direction, bp)]}"
            segments.append(f"{name} {move} → {signal}" if instrument == "KOSPI" else f"{name} {signal}")
    return f"{label} {' · '.join(segments)}"


def record_stats(record: dict) -> str:
    """n, hits, hit rate, ties, mean bp and simple cumulative bp of one record, the same for every record."""
    text = f"{record['n']}전 {record['hits']}승"
    if record["n"]:
        text += f"({(Decimal(record['hits'] * 100) / record['n']).quantize(Decimal('1'), ROUND_HALF_UP)}%)"
    if record["ties"]:
        text += f" 무 {record['ties']}"
    if record["mean_bp"] is not None:
        text += f" 평균 {record['mean_bp']:+.2f}bp 누적 {record['sum_bp']:+.2f}bp"
    return text


def record_text(name: str, record: dict) -> str:
    return f"{name} {record_stats(record)}"


def verdict(name: str, versus: dict) -> str:
    """Only a sign test below 0.05 may call the Analyst better or worse than the baseline."""
    if versus["p"] < SIGNIFICANT:
        return f"{name}보다 {'낫다' if versus['wins'] > versus['losses'] else '못하다'}"
    return "아직 우연과 구분할 수 없음"


def summary_lines(summary: dict) -> list[str]:
    kinds = [kind for kind in KINDS if summary[kind]["slots"]]
    if not kinds:
        return []
    lines = []
    for kind in kinds:
        for instrument in INSTRUMENTS:
            entry = summary[kind]["instruments"][instrument]
            names = {"analyst": "Analyst 누적 " + KIND_WORDS[kind] + " " + INSTRUMENT_WORDS[instrument],
                     "always_long": BASELINE_WORDS["always_long"], "follow_us": BASELINE_WORDS["follow_us"]}
            text = " · ".join(record_text(names[key], entry[key]) for key in ("analyst", "always_long", "follow_us")
                              if key in entry)
            if entry["pending"]:
                text += f" · 채점 보류 {entry['pending']}"
            lines.append(text)
    for baseline in BASELINES:
        name = BASELINE_WORDS[baseline]
        for instrument in INSTRUMENTS:
            parts = []
            for kind in kinds:
                versus = summary[kind]["instruments"][instrument]["versus"].get(baseline)
                if versus and versus["wins"] + versus["losses"]:
                    days = versus["wins"] + versus["losses"]
                    parts.append(f"{KIND_WORDS[kind]} {days}일 중 {versus['wins']}일 맞음 → {verdict(name, versus)}")
            if parts:
                lines.append(f"{with_particle(name, '과', '와')} 다르게 건 날({INSTRUMENT_WORDS[instrument]}): "
                             + " · ".join(parts))
    for instrument in INSTRUMENTS:
        text = " · ".join(f"{KIND_WORDS[kind]} {record_stats(summary[kind]['instruments'][instrument]['received'])}"
                          for kind in kinds)
        lines.append(f"받으신 신호 기준 {INSTRUMENT_WORDS[instrument]} {text}")
    kinds_text = []
    for kind in kinds:
        counts = summary[kind]["counts"]
        text = f"{KIND_WORDS[kind]} 정시 {counts['on_time']} · 늦음 {counts['late']} · 임시 {counts['fallback']}"
        for key, word in (("after_entry", "진입 뒤"), ("unparsed", "형식 실패")):
            text += f" · {word} {counts[key]}" if counts[key] else ""
        kinds_text.append(text)
    lines.append("신호 구분 " + " / ".join(kinds_text))
    reasons = [f"{KIND_WORDS[kind]} {reason} {count}건" for kind in kinds
               for reason, count in summary[kind]["fallback_reasons"].items()]
    if reasons:
        lines.append("임시 신호 사유: " + ", ".join(reasons))
    return lines


def scorecard_post(day: date, lines: list[str], summary: dict) -> str:
    """The evening scorecard: today's results (lines), then the record of every slot since the first one."""
    return "\n".join([f"📊 신호 채점 · {day.month}월 {day.day}일", *lines, *summary_lines(summary), SCORECARD_FOOTER])
