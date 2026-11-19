"""Frozen public quote snapshot the Analyst cites. Dates use each exchange's own timezone."""

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple
from zoneinfo import ZoneInfo

from ..briefing.data_reader import CHART_POLL_SECONDS, CHART_WORKERS, chart_number, fetch_chart
from ..briefing.schedule import KST, utcnow
from .contracts import Direction, SignalKind

DELAY_LIMIT = timedelta(minutes=20)


class Instrument(NamedTuple):
    symbol: str
    label: str
    kinds: frozenset[str]
    live: bool


BOTH, OVERNIGHT = frozenset({"intraday", "overnight"}), frozenset({"overnight"})
CATALOGUE = {
    "es": Instrument("ES=F", "S&P 500 선물", BOTH, True),
    "nq": Instrument("NQ=F", "나스닥 100 선물", BOTH, True),
    "spy": Instrument("SPY", "SPY", BOTH, False),
    "ewy": Instrument("EWY", "EWY", BOTH, False),
    "usdkrw": Instrument("KRW=X", "원/달러", BOTH, True),
    "kospi": Instrument("^KS11", "코스피", BOTH, False),
    "kodex200": Instrument("069500.KS", "KODEX 200", BOTH, False),
    "kosdaq": Instrument("^KQ11", "코스닥", BOTH, False),
    "n225": Instrument("^N225", "닛케이 225", OVERNIGHT, False),
    "hsi": Instrument("^HSI", "항셍", OVERNIGHT, False),
    "shanghai": Instrument("000001.SS", "상하이종합", OVERNIGHT, False),
}
# In the overnight snapshot the Asian cash markets trade while the Analyst reads them.
OVERNIGHT_LIVE = frozenset({"kospi", "kodex200", "kosdaq", "n225", "hsi", "shanghai"})


def is_live(key: str, kind: str) -> bool:
    return CATALOGUE[key].live or (kind == "overnight" and key in OVERNIGHT_LIVE)


def optional_number(value, key):
    try:
        return chart_number(value, key)
    except ValueError:
        return None


def parse_quote(payload: dict, symbol: str) -> dict:
    try:
        result = payload["chart"]["result"][0]
        meta = result["meta"]
        if str(meta.get("symbol", "")).upper() != symbol.upper():
            raise ValueError("chart_symbol_mismatch")
        zone = ZoneInfo(meta["exchangeTimezoneName"])
        value = chart_number(meta["regularMarketPrice"], symbol)
        source_time = datetime.fromtimestamp(int(meta["regularMarketTime"]), UTC)
        session_day = source_time.astimezone(zone).date()
        stamps = result.get("timestamp") or []
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        closes, opens = quote.get("close") or [], quote.get("open") or []
        if len(stamps) != len(closes) or (opens and len(opens) != len(stamps)):
            raise ValueError("chart_schema_rejected")
        days = [datetime.fromtimestamp(int(t), UTC).astimezone(zone).date() for t in stamps]
        prior = [(d, c) for d, c in zip(days, closes, strict=True) if c is not None and d < session_day]
        previous_close = chart_number(max(prior, key=lambda x: x[0])[1], symbol) if prior else (
            chart_number(meta["previousClose"], symbol) if meta.get("previousClose") is not None else None)
        session_open = next((optional_number(o, symbol) for d, o in zip(days, opens, strict=False)
                             if d == session_day and o is not None), None)
    except (KeyError, IndexError, TypeError, OverflowError, OSError):
        raise ValueError("chart_schema_rejected") from None
    except ValueError as exc:
        # ZoneInfo raises ValueError/ZoneInfoNotFoundError subclasses; keep fixed codes only.
        raise ValueError(str(exc) if str(exc) in {"chart_symbol_mismatch", "chart_value_invalid"}
                         else "chart_schema_rejected") from None
    change = ((value/previous_close-1)*100).quantize(Decimal("0.01"), ROUND_HALF_UP) if previous_close else None
    return {"symbol": symbol, "value": str(value), "source_time": source_time.isoformat(),
            "session_day": session_day, "session_open": str(session_open) if session_open is not None else None,
            "previous_close": str(previous_close) if previous_close is not None else None,
            "change_pct": str(change) if change is not None else None}


def read_snapshot(kind: SignalKind, fetch=fetch_chart, *, clock=utcnow) -> dict:
    items = {key: i for key, i in CATALOGUE.items() if kind in i.kinds}
    pool = ThreadPoolExecutor(max_workers=min(8, CHART_WORKERS))
    futures = {key: pool.submit(fetch, i.symbol) for key, i in items.items()}
    wait(futures.values(), timeout=CHART_POLL_SECONDS)
    pool.shutdown(wait=False, cancel_futures=True)
    observed = clock()
    quotes, errors, receipts, raw = {}, {}, {}, {}
    for key, instrument in items.items():
        future = futures[key]
        try:
            receipt = future.result(timeout=0) if future.done() else {"ok": False, "error": "poll_deadline"}
        except Exception:
            receipt = {"ok": False, "error": "chart_fetch_failed"}
        if not receipt.get("ok"):
            errors[key] = receipt.get("error", "chart_fetch_failed")
            continue
        # Original response and its receipt are kept even when the payload is rejected.
        raw[key] = receipt.get("payload")
        if receipt.get("sha256"):
            receipts[key] = receipt["sha256"]
        try:
            quote = parse_quote(receipt["payload"], instrument.symbol)
        except ValueError as exc:
            errors[key] = str(exc)
            continue
        age = observed-datetime.fromisoformat(quote["source_time"])
        quote["session_day"] = str(quote["session_day"])
        quote["delayed"] = is_live(key, kind) and age > DELAY_LIMIT
        quotes[key] = quote
    return {"kind": kind, "observed_at": observed.isoformat(), "quotes": quotes, "errors": errors,
            "receipts": receipts, "raw": raw}


def snapshot_text(snapshot: dict) -> str:
    lines = []
    for key, quote in snapshot["quotes"].items():
        at = datetime.fromisoformat(quote["source_time"]).astimezone(KST).strftime("%H:%M")
        change = Decimal(quote["change_pct"]) if quote.get("change_pct") is not None else None
        text = f"{change:+.2f}%" if change is not None else "없음"
        lines.append(f"{CATALOGUE[key].label} | {quote['value']} | 원천 {at} KST | 전일 대비 {text}"
                     + (" | 지연" if quote.get("delayed") else ""))
    if snapshot["errors"]:
        lines.append("빠짐: "+", ".join(f"{k}({c})" for k, c in snapshot["errors"].items()))
    return "\n".join(lines)


def follow_us(snapshot: dict) -> Direction | None:
    quote = snapshot["quotes"].get("spy")
    try:
        move = Decimal(quote["value"])/Decimal(quote["previous_close"])-1
    except (TypeError, KeyError, ArithmeticError):
        return None
    return "long" if move > 0 else "short" if move < 0 else None
