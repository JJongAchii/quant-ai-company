"""Grade a signal slot: per-instrument long returns in bp on the official KOSPI open/close and on KODEX 200 daily bars.
Only the exact sessions of the slot count; a missing or suspicious price leaves the return pending, never a nearby day."""

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")
KODEX_PARAMS = "interval=1d&range=1mo&events=div"
NO_OFFICIAL = "공식 자료 없음"
SUSPICIOUS = "의심 일봉"
MISSING_BAR = "일봉 없음"
FETCH_FAILED = "시세 조회 실패"
EX_DIVIDEND = "분배락"


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
