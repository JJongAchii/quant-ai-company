"""Grade a signal slot: per-instrument long returns in bp on the official KOSPI open/close and on KODEX 200 daily bars.
Only the exact sessions of the slot count; a missing or suspicious price leaves the return pending, never a nearby day."""

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from math import comb
from typing import Literal
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")
KODEX_PARAMS = "interval=1d&range=1mo&events=div"
NO_OFFICIAL = "공식 자료 없음"
SUSPICIOUS = "의심 일봉"
MISSING_BAR = "일봉 없음"
FETCH_FAILED = "시세 조회 실패"
EX_DIVIDEND = "분배락"
# A return still pending this many calendar days after its grading day is excluded for good.
EXPIRE_DAYS = 14
NO_DATA = "자료 없음"
INSTRUMENTS = ("KOSPI", "KODEX200")
KINDS = ("intraday", "overnight")
BASELINES = ("always_long", "follow_us")


def long_return_bp(entry: Decimal, exit: Decimal) -> Decimal:
    return ((exit / entry - 1) * 10000).quantize(Decimal("0.01"), ROUND_HALF_UP)


def outcome(direction: str, long_bp: Decimal) -> Literal["hit", "miss", "tie"]:
    if long_bp == 0:
        return "tie"
    return "hit" if (long_bp > 0) == (direction == "long") else "miss"


def _day(stamp) -> date:
    return datetime.fromtimestamp(int(stamp), UTC).astimezone(SEOUL).date()


def kodex_bars(payload: dict) -> tuple[dict[date, dict], set[date] | None]:
    """Bars by Seoul date ({open, close, volume} as Decimal) and the ex-dividend dates (None without an events key)."""
    result = payload["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    bars = {}
    for i, stamp in enumerate(result.get("timestamp") or []):
        try:
            values = {key: quote[key][i] for key in ("open", "close", "volume")}
        except (KeyError, IndexError):
            continue
        if any(v is None for v in values.values()):
            continue
        bars[_day(stamp)] = {key: Decimal(str(v)) for key, v in values.items()}
    events = result.get("events")
    if events is None:
        return bars, None
    return bars, {_day(item["date"]) for item in (events.get("dividends") or {}).values() if "date" in item}


def _row(signal_id, instrument, state, receipt, *, entry=None, exit=None, detail=None):
    bp = long_return_bp(entry, exit) if state == "final" else None
    return {"signal_id": signal_id, "instrument": instrument, "state": state, "entry_price": entry,
            "exit_price": exit, "long_bp": bp, "detail": detail, "receipt": receipt}


def _kospi(row: dict, lake: dict | None) -> dict:
    receipt = (lake or {}).get("source")
    if not lake or not lake.get("ok"):
        return _row(row["id"], "KOSPI", "pending", receipt, detail=NO_OFFICIAL)
    index = {item["date"]: item for item in lake["rows"]}
    first_day, first_key = row["session_day"], ("open" if row["kind"] == "intraday" else "close")
    last_day, last_key = row["exit_day"], ("close" if row["kind"] == "intraday" else "open")
    entry_row, exit_row = index.get(str(first_day)), index.get(str(last_day))
    if not entry_row or not exit_row:
        return _row(row["id"], "KOSPI", "pending", receipt, detail=NO_OFFICIAL)
    return _row(row["id"], "KOSPI", "final", receipt, entry=Decimal(entry_row[first_key]),
                exit=Decimal(exit_row[last_key]))


def _kodex(row: dict, bars: dict, dividends: set | None, receipt) -> dict:
    """Yahoo omits `events` when the window has no distribution, so None means none (R18)."""
    entry_bar, exit_bar = bars.get(row["session_day"]), bars.get(row["exit_day"])
    if not entry_bar or not exit_bar:
        return _row(row["id"], "KODEX200", "pending", receipt, detail=MISSING_BAR)
    for bar in (entry_bar, exit_bar):
        if any(not bar[key].is_finite() or bar[key] <= 0 for key in ("open", "close")) or (
                bar["open"] == bar["close"] and bar["volume"] > 0):
            return _row(row["id"], "KODEX200", "pending", receipt, detail=SUSPICIOUS)
    intraday = row["kind"] == "intraday"
    entry = entry_bar["open" if intraday else "close"]
    exit = exit_bar["close" if intraday else "open"]
    if not intraday and row["exit_day"] in (dividends or set()):
        return _row(row["id"], "KODEX200", "excluded", receipt, entry=entry, exit=exit, detail=EX_DIVIDEND)
    return _row(row["id"], "KODEX200", "final", receipt, entry=entry, exit=exit)


def grade_slot(row: dict, lake: dict | None, chart: dict | None) -> list[dict]:
    """The KOSPI and KODEX200 returns of one due slot. lake is the reader result, chart the fetch_chart result."""
    failed = {"error": (chart or {}).get("error", "chart_unavailable")}
    if not chart or not chart.get("ok"):
        return [_kospi(row, lake), _row(row["id"], "KODEX200", "pending", failed, detail=FETCH_FAILED)]
    try:
        bars, dividends = kodex_bars(chart["payload"])
    except (KeyError, IndexError, TypeError, ValueError, ArithmeticError):
        failed["error"] = "chart_parse_or_policy_error"
        return [_kospi(row, lake), _row(row["id"], "KODEX200", "pending", failed, detail=FETCH_FAILED)]
    return [_kospi(row, lake), _kodex(row, bars, dividends, chart.get("sha256"))]


def grading_day(row: dict) -> date:
    """The day the slot's trade closes: the session day for intraday, the exit day for overnight."""
    return row["session_day"] if row["kind"] == "intraday" else row["exit_day"]


def expire(row: dict, returns: list[dict], today: date) -> list[dict]:
    """From EXPIRE_DAYS calendar days after the grading day (KST today), an instrument still pending, or not graded at
    all, becomes excluded with detail 자료 없음. That takes the slot off the due list for good, so it no longer widens
    the lake read. A final or excluded return stays as it is."""
    if today < grading_day(row) + timedelta(days=EXPIRE_DAYS):
        return returns
    graded = {r["instrument"]: r for r in returns if r["state"] != "pending"}
    receipts = {r["instrument"]: r["receipt"] for r in returns}
    return [graded.get(instrument) or _row(row["id"], instrument, "excluded", receipts.get(instrument), detail=NO_DATA)
            for instrument in INSTRUMENTS]


def sign_test_p(wins: int, losses: int) -> float:
    """Two-sided exact binomial sign test against 50/50 on the days that count."""
    n = wins + losses
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(comb(n, k) for k in range(min(wins, losses) + 1)) / 2 ** n)


def _record(items: list[tuple[str, Decimal]]) -> dict:
    """items: (direction, long_bp). n counts hits and misses; ties are counted apart and add 0 to the sums."""
    tally = {"hit": 0, "miss": 0, "tie": 0}
    total = Decimal(0)
    for direction, bp in items:
        tally[outcome(direction, bp)] += 1
        total += bp if direction == "long" else -bp
    graded = len(items)
    return {"n": tally["hit"] + tally["miss"], "hits": tally["hit"], "misses": tally["miss"], "ties": tally["tie"],
            "mean_bp": (total / graded).quantize(Decimal("0.01"), ROUND_HALF_UP) if graded else None, "sum_bp": total}


def _final_bp(slot: dict, instrument: str) -> Decimal | None:
    result = (slot.get("returns") or {}).get(instrument)
    if not result or result["state"] != "final" or result["long_bp"] is None:
        return None
    return Decimal(str(result["long_bp"]))


def analyst_counts(slot: dict) -> bool:
    return bool(slot["analyst"]) and slot["timing"] in ("on_time", "late") and not slot["unparsed"]


def mean_confidence(slots: list[dict], instrument: str) -> int | None:
    """The Analyst's mean computed confidence over the slots its record counts as a hit or a miss on instrument."""
    values = [slot["confidence"] for slot in slots if analyst_counts(slot) and slot.get("confidence") is not None
              and (bp := _final_bp(slot, instrument)) is not None and bp != 0]
    if not values:
        return None
    return int((Decimal(sum(values)) / len(values)).quantize(Decimal("1"), ROUND_HALF_UP))


def _versus(slots: list[dict], instrument: str, baseline: str) -> dict:
    wins = losses = 0
    for slot in slots:
        bp, direction = _final_bp(slot, instrument), (slot["baselines"] or {}).get(baseline)
        if bp is None or bp == 0 or direction is None or not analyst_counts(slot) or direction == slot["analyst"]:
            continue
        if outcome(slot["analyst"], bp) == "hit":
            wins += 1
        else:
            losses += 1
    return {"wins": wins, "losses": losses, "p": sign_test_p(wins, losses)}


def summarize(slots: list[dict]) -> dict:
    """The record of every slot since the first, per kind and instrument. Only final returns count; ties, pending and
    excluded returns are left out of the hit counts. The Analyst's record has timing on_time or late only."""
    summary = {}
    for kind in KINDS:
        mine = [slot for slot in slots if slot["kind"] == kind]
        counts = {"on_time": 0, "late": 0, "fallback": 0, "after_entry": 0, "unparsed": 0, "pending": 0}
        reasons: dict[str, int] = {}
        for slot in mine:
            if slot["analyst"] and slot["timing"] in ("on_time", "late", "after_entry"):
                counts[slot["timing"]] += 1
            counts["unparsed"] += bool(slot["unparsed"])
            if slot["fallback"]:
                counts["fallback"] += 1
                reasons[slot["fallback_reason"]] = reasons.get(slot["fallback_reason"], 0) + 1
            counts["pending"] += sum(r["state"] == "pending" for r in (slot.get("returns") or {}).values())
        instruments = {}
        for instrument in INSTRUMENTS:
            def record(pick, instrument=instrument, mine=mine):
                items = [(pick(slot), _final_bp(slot, instrument)) for slot in mine]
                return _record([(direction, bp) for direction, bp in items if direction and bp is not None])
            entry = {"analyst": {**record(lambda s: s["analyst"] if analyst_counts(s) else None),
                                 "mean_confidence": mean_confidence(mine, instrument)},
                     "received": record(lambda s: s["received"]),
                     "always_long": record(lambda s: (s["baselines"] or {}).get("always_long")),
                     "pending": sum(slot["returns"][instrument]["state"] == "pending" for slot in mine
                                    if instrument in (slot.get("returns") or {})),
                     "versus": {"always_long": _versus(mine, instrument, "always_long")}}
            if kind == "intraday":
                entry["follow_us"] = record(lambda s: (s["baselines"] or {}).get("follow_us"))
                entry["versus"]["follow_us"] = _versus(mine, instrument, "follow_us")
            instruments[instrument] = entry
        summary[kind] = {"slots": len(mine), "counts": counts, "fallback_reasons": reasons, "instruments": instruments}
    return summary
