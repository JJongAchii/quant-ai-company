"""Fixed, bounded qdata API queries and public US close chart reads. No model code, lake internals, or collector writes."""

import hashlib
import http.client
import json
import os
import re
import ssl
import sys
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib.parse import quote

from .contracts import BriefEdition
from .schedule import KST, NY, close, previous, utcnow

DATASETS = ("krx_index", "prices", "fred", "ecos")
MAX_OBJECT_BYTES = 4 * 1024 * 1024
MAX_ROWS = 100000
ETFS = ("SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "HYG", "GLD", "DBC")
# Post-close Yahoo chart reads (BRIEFING_US_CLOSE_ENABLED). Polls are recorded in the edition and
# summarized like a lake dataset; a value is confirmed only by two identical post-close polls.
US_CLOSE = "us_close"
US_CLOSE_REGISTRATION = "yahoo.chart:"+US_CLOSE
CHART_PROVIDER, CHART_HOST, CHART_MAX_BYTES = "yahoo_chart", "query1.finance.yahoo.com", 262144
FIRST_POLL_MINUTES, LAST_POLL_MINUTES, POLL_MINUTES = 5, 35, 2
POLL_SLOTS = (LAST_POLL_MINUTES-FIRST_POLL_MINUTES)//POLL_MINUTES+1
CHART_REQUEST_SECONDS, CHART_POLL_SECONDS, CHART_WORKERS = 6, 25, 8
# VIX is calculated until 16:15 ET, not the equity close.
SETTLE_MINUTES = {"vix": 15}
CLOSE_INDICES = {"sp500": "^GSPC", "nasdaq": "^IXIC", "dow": "^DJI", "sox": "^SOX", "vix": "^VIX"}
# Confirmed groups shown to the writer as table rows; every poll stays in the receipts.
TABLE_GROUPS = ("index",)
# Continuously traded: futures, crypto and the ^TNX yield (Treasuries trade after the equity close and
# regularMarketTime proves no settlement). Timestamped quotes only, never locked as closes.
UNSETTLED = {"ust10y": "^TNX", "wti": "CL=F", "gold": "GC=F", "btc": "BTC-USD", "eth": "ETH-USD"}
# Regular-session KRX close snapshot (BRIEFING_KR_CLOSE_ENABLED, PM editions only). qdata writes these clean
# tables latest session first in small row groups, so a bounded inspect_dataset sample of exactly one session's
# rows is that session. Each table: (dataset, sampled rows = one session, sampled columns, contract schema).
KRX_CLOSE = "krx_close"
KRX_CLOSE_REGISTRATION = "qdata.api:"+KRX_CLOSE
KRX_CLOSE_TABLES = {
    "index": ("krx_close_index", 2,
              ["date", "index", "close", "prev_close", "chg_pct", "volume", "value", "collected_at"],
              {"date": "time", "index": "text", "open": "number", "high": "number", "low": "number",
               "close": "number", "prev_close": "number", "chg_pct": "number", "volume": "number",
               "value": "number", "collected_at": "text", "basis": "text"}),
    "prices": ("krx_close_prices", 20,
               ["date", "ticker", "market", "close", "chg_pct", "volume", "value", "collected_at"],
               {"date": "time", "ticker": "text", "market": "text", "open": "number", "high": "number",
                "low": "number", "close": "number", "chg_pct": "number", "volume": "number", "value": "number",
                "collected_at": "text", "basis": "text", "close_basis": "text"}),
    "flows": ("krx_close_flows", 8, ["date", "market", "investor", "net_value", "collected_at"],
              {"date": "time", "market": "text", "investor": "text", "net_value": "number",
               "collected_at": "text", "basis": "text"}),
}
KRX_MARKETS = {"KOSPI": ("kospi", "코스피"), "KOSDAQ": ("kosdaq", "코스닥")}
KRX_INVESTORS = {"indiv": "개인", "frgn": "외국인", "inst": "기관", "corp": "기타법인"}
# Only the KOSPI (유가증권시장) foreign/institution totals are registered instruments; the rest are display lines.
KRX_FLOW_INSTRUMENTS = {"frgn": "kr_foreign", "inst": "kr_institution"}
STOCK_MASTER = "krx_stock_master"
TOP_STOCKS = 5
# load_krx_stock_master reads the whole vintage table (~2,700 rows per trading day since 2026-08-20) into the
# reader subprocess of the 384 MiB data worker; 300k rows measured about +100 MiB peak. Beyond the budget only
# the top-stock lines are omitted (diagnostic), until qdata offers a bounded name lookup.
MASTER_MAX_BYTES, MASTER_MAX_ROWS = 8 * 1024 * 1024, 300000
# KRX's after-market session starts at 16:00 KST; from then on the per-stock close and the day's volume,
# value and flows are no longer the regular-session values.
AFTER_MARKET_START = time(16)
CLOSE_LABEL = "정규장 종가 기준"
# Field result 2026-10-08: the KRX index quote lags about 20 minutes (15:45 equals the 15:24:50 tick), so a
# 15:40 index level, change, volume or value is not the official close. Writer-facing label of those values.
INDEX_LABEL = "지수 시세 지연 가능 · 마감 보도와 일치할 때만 종가로 사용"
SESSION_LABEL = "장 마감 기준 · 시간외(16:00~20:00) 미포함"


def scalar(value):
    result = Decimal(str(value))
    if not result.is_finite() or abs(result) > Decimal("1e15"):
        raise ValueError("invalid_market_value")
    return str(result)


def records(frame, columns):
    if len(frame) > 400:
        raise ValueError("brief_market_row_limit")
    return [{k: str(v.date()) if k == "date" else str(v) if k in {"index", "ticker", "series"}
             else scalar(v) for k, v in row.items()}
            for row in frame[columns].to_dict("records")]


def read_snapshot(definition, api, *, clock=utcnow, krx_close=False):
    edition = BriefEdition.model_validate(definition)
    started = clock()
    if started > edition.cutoff:
        return {"ok": False, "error": "data_cutoff_passed", "observed_at": started.isoformat()}
    start, end = str(edition.day-timedelta(days=45)), str(edition.day)
    result = {"ok": True, "qdata_code_commit": os.environ.get("QDATA_CODE_COMMIT", "unqualified"),
              "datasets": {}, "errors": [], "started_at": started.isoformat()}
    for name in DATASETS:
        try:
            meta = api.inspect_dataset(name)
            source = meta["source"]
            if (source["size_bytes"] > MAX_OBJECT_BYTES or meta["row_count"] > MAX_ROWS
                    or len(meta["columns"]) > 12):
                raise ValueError("dataset_exceeds_briefing_budget")
            if name == "krx_index":
                frame = api.load_krx_index(start=start, end=end)
                rows = records(frame, ["date", "index", "close", "value"])
            else:
                if name == "prices":
                    frame = api.load_prices(list(ETFS), start=start, end=end, fields=("adj_close",))["adj_close"]
                elif name == "fred":
                    frame = api.load_fred(["VIXCLS", "BAMLH0A0HYM2"], start=start, end=end)
                else:
                    frame = api.load_ecos(["base_rate", "ktb_3y", "usdkrw"], start=start, end=end)
                if len(frame) > 46 or not frame.index.is_unique:
                    raise ValueError("invalid_daily_series")
                rows = [{"date": str(index.date()), "series": str(series), "value": scalar(value)}
                        for series in frame.columns for index, value in frame[series].dropna().items()]
            after = api.inspect_dataset(name)
            if after["source"] != source:
                raise ValueError("source_changed_during_read")
            observed = clock()
            if observed > edition.cutoff:
                raise ValueError("data_cutoff_passed")
            result["datasets"][name] = {"source": source, "date_bounds": meta["date_bounds"],
                                         "observed_at": observed.isoformat(), "rows": rows}
        except (FileNotFoundError, KeyError):
            result["errors"].append({"dataset": name, "error": "dataset_or_series_unavailable"})
        except ValueError as exc:
            # Only our fixed errors, never provider messages containing credential details.
            code = str(exc) if str(exc) in {"invalid_market_value", "brief_market_row_limit",
                "dataset_exceeds_briefing_budget", "invalid_daily_series", "source_changed_during_read",
                "data_cutoff_passed"} else "data_schema_or_budget_rejected"
            result["errors"].append({"dataset": name, "error": code})
        except Exception:
            result["errors"].append({"dataset": name, "error": "data_access_unavailable"})
    if krx_close and edition.kind == "pm" and edition.kr_session:
        read_krx_close(edition, api, result, clock=clock)
    result["observed_at"] = clock().isoformat()
    return result


def krx_close_type(value, kind):
    """Arrow type families of the contract columns; anything else is a schema mismatch."""
    value = str(value or "")
    if kind == "time":
        return value.startswith(("timestamp", "date"))
    if kind == "text":
        return value in {"string", "large_string", "utf8", "large_utf8"}
    return bool(re.fullmatch(r"u?int(8|16|32|64)|float|double|halffloat|decimal(32|64|128|256)?\(.*\)", value))


def krx_close_row(row, columns):
    """One sampled row as reader-owned strings; a wrong type or shape rejects the whole table."""
    if not isinstance(row, dict) or set(row) != set(columns):
        raise ValueError("krx_close_schema_mismatch")
    out = {}
    for key in columns:
        value = row[key]
        if key == "date":
            if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T00:00(?::00(?:\.0+)?)?)?", value):
                raise ValueError("krx_close_schema_mismatch")
            out[key] = str(date.fromisoformat(value[:10]))
        elif key == "collected_at":
            stamp = datetime.fromisoformat(value) if isinstance(value, str) and len(value) <= 40 else None
            if stamp is None or stamp.tzinfo is None:
                raise ValueError("krx_close_schema_mismatch")
            out[key] = stamp.astimezone(UTC).isoformat()
        elif key in {"index", "market"}:
            if value not in KRX_MARKETS:
                raise ValueError("krx_close_schema_mismatch")
            out[key] = value
        elif key == "investor":
            if value not in KRX_INVESTORS:
                raise ValueError("krx_close_schema_mismatch")
            out[key] = value
        elif key == "ticker":
            if not isinstance(value, str) or not re.fullmatch(r"[0-9A-Z]{6}", value):
                raise ValueError("krx_close_schema_mismatch")
            out[key] = value
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("krx_close_schema_mismatch")
            out[key] = scalar(value)
    return out


def read_krx_close(edition, api, result, *, clock=utcnow):
    """Bounded first-row samples of the three krx_close tables plus names for the sampled tickers.
    Shape, schema and object identity are checked here; the session window is checked in summarize."""
    rows, sources, bounds, names, master = {}, {}, {}, None, None
    for table, (name, limit, columns, schema) in KRX_CLOSE_TABLES.items():
        try:
            meta = api.inspect_dataset(name, sample_rows=limit, columns=list(columns))
            types = {c["name"]: c["type"] for c in meta["columns"]}
            sample = meta.get("sample") or {}
            if (any(not krx_close_type(types.get(c), kind) for c, kind in schema.items())
                    or list(sample.get("columns") or []) != list(columns)
                    or not isinstance(sample.get("rows"), list) or len(sample["rows"]) > limit):
                raise ValueError("krx_close_schema_mismatch")
            parsed = [krx_close_row(row, columns) for row in sample["rows"]]
            if api.inspect_dataset(name)["source"] != meta["source"]:
                raise ValueError("source_changed_during_read")
            if clock() > edition.cutoff:
                raise ValueError("data_cutoff_passed")
            rows[table], sources[table] = parsed, meta["source"]
            bounds[table] = (meta.get("date_bounds") or {}).get("date")
        except (FileNotFoundError, KeyError):
            result["errors"].append({"dataset": name, "error": "dataset_or_series_unavailable"})
        except RuntimeError as exc:
            # The pinned inspect_dataset refuses an object that changed while it was being read.
            result["errors"].append({"dataset": name, "error": "source_changed_during_read"
                                     if str(exc).startswith("Source changed") else "data_access_unavailable"})
        except ValueError as exc:
            code = str(exc) if str(exc) in {"invalid_market_value", "krx_close_schema_mismatch",
                "source_changed_during_read", "data_cutoff_passed"} else "krx_close_schema_mismatch"
            result["errors"].append({"dataset": name, "error": code})
        except Exception:
            result["errors"].append({"dataset": name, "error": "data_access_unavailable"})
    if not rows:
        return
    if rows.get("prices"):
        try:
            names, master = read_stock_names(edition, api, {r["ticker"] for r in rows["prices"]})
            if clock() > edition.cutoff:
                raise ValueError("data_cutoff_passed")
        except Exception as exc:
            # Unreadable master: only the top-stock lines are omitted.
            code = str(exc) if isinstance(exc, ValueError) and str(exc) in {
                "dataset_exceeds_briefing_budget", "source_changed_during_read", "data_cutoff_passed",
                "krx_close_schema_mismatch"} else ("dataset_or_series_unavailable"
                if isinstance(exc, (FileNotFoundError, KeyError)) else "data_access_unavailable")
            names, master = None, None
            result["errors"].append({"dataset": STOCK_MASTER, "error": code})
    result["datasets"][KRX_CLOSE] = {"source": sources, "date_bounds": bounds, "observed_at": clock().isoformat(),
                                     "rows": rows, "names": names, "master": master}


def read_stock_names(edition, api, tickers):
    """Names from the latest master vintage on or before the day before the edition (never today's)."""
    asof = str(edition.day-timedelta(days=1))
    meta = api.inspect_dataset(STOCK_MASTER)
    if meta["source"]["size_bytes"] > MASTER_MAX_BYTES or meta["row_count"] > MASTER_MAX_ROWS:
        raise ValueError("dataset_exceeds_briefing_budget")
    frame = api.load_krx_stock_master(asof=asof)
    if api.inspect_dataset(STOCK_MASTER)["source"] != meta["source"]:
        raise ValueError("source_changed_during_read")
    if not {"asof", "ticker", "name"} <= set(frame.columns) or frame["ticker"].duplicated().any():
        raise ValueError("krx_close_schema_mismatch")
    names = {}
    for ticker, name in zip(frame["ticker"], frame["name"], strict=True):
        # Shown to readers: plain short text only; anything else counts as a missing name.
        if (str(ticker) in tickers and isinstance(name, str)
                and re.fullmatch(r"[^\x00-\x1f\x7f*_~`|<>]{1,40}", name.strip())):
            names[str(ticker)] = name.strip()
    vintage = frame["asof"].max() if len(frame) else None
    return names, {"source": meta["source"], "asof": asof,
                   "vintage": str(vintage.date()) if hasattr(vintage, "date") else None}


def chart_catalogue(stocks):
    items = {key: [symbol, "index"] for key, symbol in CLOSE_INDICES.items()}
    items.update({key: [symbol, "unsettled"] for key, symbol in UNSETTLED.items()})
    items.update({symbol: [symbol, "etf"] for symbol in ETFS})
    items.update({symbol: [symbol, "stock"] for symbol in stocks if symbol not in items})
    return items


def fetch_chart(symbol, *, connection_factory=None):
    """One bounded public read; failures become fixed codes, never provider text."""
    from ..web_fetch import PublicConnection

    connection = (connection_factory or PublicConnection)(CHART_HOST, timeout=CHART_REQUEST_SECONDS,
                                                          context=ssl.create_default_context())
    try:
        connection.request("GET", f"/v8/finance/chart/{quote(symbol, safe='')}?interval=1d&range=5d", headers={
            "User-Agent": "QuantCompanyBriefing/1.0", "Accept": "application/json", "Accept-Encoding": "identity"})
        response = connection.getresponse()
        if response.status != 200:
            return {"ok": False, "error": "http_status", "http_status": response.status}
        if response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise ValueError("unsupported_chart_encoding")
        raw = response.read(CHART_MAX_BYTES+1)
        if len(raw) > CHART_MAX_BYTES:
            raise ValueError("chart_too_large")
        return {"ok": True, "payload": json.loads(raw), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    except ssl.SSLCertVerificationError:
        return {"ok": False, "error": "chart_tls_verification_failed"}
    except (ValueError, UnicodeError):
        return {"ok": False, "error": "chart_parse_or_policy_error"}
    except (OSError, http.client.HTTPException):
        return {"ok": False, "error": "chart_network_unavailable"}
    finally:
        connection.close()


def chart_number(value, key):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("chart_value_invalid") from None
    if isinstance(value, bool) or not result.is_finite() or result <= 0 or result > Decimal("1e9"):
        raise ValueError("chart_value_invalid")
    if key == "ust10y" and result > 20:
        # Legacy CBOE quoting is ten times the yield; Yahoo currently quotes percent.
        result /= 10
    # Provider floats carry binary noise; keep the published precision.
    return result.quantize(Decimal("0.001" if key == "ust10y" else "0.01"), ROUND_HALF_UP)


def parse_chart(payload, symbol, key):
    try:
        result = payload["chart"]["result"][0]
        meta = result["meta"]
        if str(meta.get("symbol", "")).upper() != symbol.upper():
            raise ValueError("chart_symbol_mismatch")
        value = chart_number(meta["regularMarketPrice"], key)
        source_time = datetime.fromtimestamp(int(meta["regularMarketTime"]), UTC)
        previous_close, basis, session = None, None, None
        # range=5d makes chartPreviousClose the close before the window; use the prior daily bar.
        stamps = result.get("timestamp") or []
        closes = (result.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
        if len(stamps) != len(closes):
            raise ValueError("chart_schema_rejected")
        prior = [(datetime.fromtimestamp(int(t), UTC).astimezone(NY).date(), c)
                 for t, c in zip(stamps, closes, strict=True) if c is not None]
        prior = [(d, c) for d, c in prior if d < source_time.astimezone(NY).date()]
        if prior:
            session, last = max(prior)
            previous_close, basis, session = chart_number(last, key), "prior_daily_bar", str(session)
        elif meta.get("previousClose") is not None:
            previous_close, basis = chart_number(meta["previousClose"], key), "meta.previousClose"
    except (KeyError, IndexError, TypeError, OverflowError, OSError):
        raise ValueError("chart_schema_rejected") from None
    return {"symbol": symbol, "value": str(value), "source_time": source_time.isoformat(),
            "previous_close": str(previous_close) if previous_close is not None else None,
            "previous_basis": basis, "previous_session": session}


def close_slot(close_at, at):
    """Two-minute slot from C+5 through C+35; None outside the window."""
    start = close_at+timedelta(minutes=FIRST_POLL_MINUTES)
    if not start <= at < close_at+timedelta(minutes=LAST_POLL_MINUTES+POLL_MINUTES):
        return None
    return min(POLL_SLOTS-1, int((at-start).total_seconds()//(POLL_MINUTES*60)))


def read_us_close(definition, items, index, fetch, *, clock=utcnow):
    """Read every symbol once for one slot. Like read_snapshot, nothing is read or kept after the cutoff."""
    edition = BriefEdition.model_validate(definition)
    started = clock()
    if started >= edition.cutoff:
        return {"ok": False, "error": "data_cutoff_passed", "observed_at": started.isoformat()}
    quotes, errors = {}, []
    pool = ThreadPoolExecutor(max_workers=CHART_WORKERS)
    futures = {key: pool.submit(fetch, symbol) for key, (symbol, _) in items.items()}
    wait(futures.values(), timeout=CHART_POLL_SECONDS)
    pool.shutdown(wait=False, cancel_futures=True)
    for key, (symbol, _) in items.items():
        future = futures[key]
        try:
            receipt = future.result(timeout=0) if future.done() else {"ok": False, "error": "poll_deadline"}
        except Exception:
            receipt = {"ok": False, "error": "chart_fetch_failed"}
        if not receipt.get("ok"):
            errors.append({"key": key, "symbol": symbol, "error": receipt.get("error", "chart_fetch_failed"),
                           **({"http_status": receipt["http_status"]} if "http_status" in receipt else {})})
            continue
        try:
            quotes[key] = {**parse_chart(receipt["payload"], symbol, key), "sha256": receipt.get("sha256")}
        except ValueError as exc:
            code = str(exc) if str(exc) in {"chart_symbol_mismatch", "chart_value_invalid"} else "chart_schema_rejected"
            errors.append({"key": key, "symbol": symbol, "error": code})
    observed = clock()
    if observed >= edition.cutoff:
        return {"ok": False, "error": "data_cutoff_passed", "observed_at": observed.isoformat()}
    return {"ok": True, "slot": index, "started_at": started.isoformat(), "observed_at": observed.isoformat(),
            "quotes": quotes, "errors": errors}


def confirm_us_close(polls, items, close_at):
    """Pure function of the recorded polls: a value is confirmed by two consecutive identical polls whose
    source time is at or after the close (VIX: close + 15 minutes). Unsettled quotes are never confirmed."""
    polls = sorted(polls, key=lambda p: p["slot"])
    confirmed, unsettled, errors = {}, {}, []
    for key, (symbol, group) in items.items():
        if group == "unsettled":
            latest = next((p for p in reversed(polls) if key in p["quotes"]), None)
            if latest:
                q = latest["quotes"][key]
                unsettled[key] = {"symbol": symbol, "value": q["value"], "source_time": q["source_time"],
                                  "observed_at": latest["observed_at"]}
            continue
        settle = close_at+timedelta(minutes=SETTLE_MINUTES.get(key, 0))
        last, result, seen, early = None, None, False, False
        for p in polls:
            q = p["quotes"].get(key)
            source = datetime.fromisoformat(q["source_time"]) if q else None
            if not q or source < settle or source > datetime.fromisoformat(p["observed_at"])+timedelta(minutes=1):
                early = early or bool(q and source < settle)
                last = None
                continue
            seen = True
            current = (q["value"], q.get("previous_close"))
            if result and current != (result["value"], result["previous_close"]):
                errors.append({"key": key, "error": "value_changed_after_confirmation",
                               "confirmed": result["value"], "observed": q["value"], "slot": p["slot"]})
                result = None
            if result is None and last and (last["value"], last.get("previous_close")) == current:
                result = {"symbol": symbol, "group": group, "value": q["value"], "previous_close": q.get("previous_close"),
                          "previous_session": q.get("previous_session"), "source_time": q["source_time"],
                          "confirmed_at": p["observed_at"], "slots": [last["slot"], p["slot"]]}
            last = {**q, "slot": p["slot"]}
        if result:
            confirmed[key] = result
        else:
            errors.append({"key": key, "error": "awaiting_second_identical_poll" if seen
                           else "source_time_before_close" if early else "no_quote"})
    return {"confirmed": confirmed, "unsettled": unsettled, "errors": errors}


def us_close_snapshot(state):
    """The recorded polls in read_snapshot's shape, so summarize derives them like any other dataset."""
    polls = state.get("polls") or []
    observed = max((p["observed_at"] for p in polls), default=None)
    if not observed:
        return {"ok": True, "datasets": {}, "errors": [], "observed_at": utcnow().isoformat()}
    source = {"provider": CHART_PROVIDER, "session": state["session"], "close_at": state["close_at"]}
    return {"ok": True, "errors": [], "observed_at": observed, "datasets": {US_CLOSE: {
        "source": source, "date_bounds": {}, "observed_at": observed, "rows": polls, "catalogue": state["catalogue"]}}}


def us_close_lines(data, edition):
    """A compact table of confirmed values (no raw polls). Index rows are offered as locked values that
    still need a post-close article match; ETF/stock rows are dated context only."""
    source, lines, values, contextual, diagnostics = data["source"], [], [], [], []
    close_at = datetime.fromisoformat(source["close_at"])
    session = edition.us_session
    if not session or str(session) != source["session"]:
        return lines, values, contextual, [{"dataset": US_CLOSE, "error": "stale_session",
                                            "latest": source["session"], "expected": str(session)}]
    state = confirm_us_close(data["rows"], data["catalogue"], close_at)
    for key, value in state["confirmed"].items():
        if value["group"] not in TABLE_GROUPS:
            continue
        change = (f"{(Decimal(value['value'])/Decimal(value['previous_close'])-1)*100:+.2f}%"
                  if value["previous_close"] else "-")
        at = datetime.fromisoformat(value["source_time"]).astimezone(UTC)
        # One short row per value; an index row is repeated as its locked observation's evidence quote.
        line = (f"{key}|{value['symbol']}|{value['value']}|{change}|{value['previous_close'] or '-'}"
                f"|{at:%H:%M}Z|yahoo")
        (lines if value["group"] == "index" else contextual).append(line)
        if value["group"] != "index":
            continue
        dated = bool(value["previous_close"]) and value["previous_session"] == str(edition.previous_us_session)
        values.append({"id": "data-"+key, "instrument": key, "value": value["value"], "unit": "pt",
                       "session_date": str(session), "venue": "Yahoo chart", "basis": "close",
                       "as_of": (close_at+timedelta(minutes=SETTLE_MINUTES.get(key, 0))).isoformat(),
                       "previous_value": value["previous_close"] if dated else None,
                       "previous_session_date": value["previous_session"] if dated else None, "quote": line})
    if lines or contextual:
        lines.insert(0, f"US close {session} vs {edition.previous_us_session}; two identical post-close polls"
                        "|key|symbol|close|change|previous_close|source_time|provider")
    pending = sorted(k for k, (_, group) in data["catalogue"].items() if group == "index" and k not in state["confirmed"])
    if pending:
        diagnostics.append({"dataset": US_CLOSE, "error": "close_not_confirmed", "instruments": pending})
    return lines, values, contextual, diagnostics


def krx_amount(won, *, signed=False):
    """KRW as Korean 조/억 units rounded to 억원, the precision closing reports use."""
    eok = int((Decimal(won)/Decimal(100000000)).quantize(Decimal(1), ROUND_HALF_UP))
    jo, rest = divmod(abs(eok), 10000)
    text = (f"{jo:,}조 {rest:,}억원" if jo and rest else f"{jo:,}조원" if jo else f"{rest:,}억원")
    return ("-" if eok < 0 else "+" if signed and eok > 0 else "")+text


def krx_shares(count):
    man = int((Decimal(count)/Decimal(10000)).quantize(Decimal(1), ROUND_HALF_UP))
    eok, rest = divmod(man, 10000)
    return f"{eok:,}억 {rest:,}만주" if eok and rest else f"{eok:,}억주" if eok else f"{rest:,}만주"


def krx_price(value):
    value = Decimal(value)
    return f"{int(value):,}" if value == value.to_integral_value() else f"{value:,}"


def krx_close_session(name, rows, edition, close_at, observed):
    """None when every sampled row is the edition's session, collected at or after the actual close and
    before the 16:00 after-market start; otherwise the code rejecting the whole table."""
    if not rows:
        return {"dataset": name, "error": "no_current_session"}
    if any(r["date"] != str(edition.day) for r in rows):
        return {"dataset": name, "error": "not_current_session", "latest": max(r["date"] for r in rows),
                "expected": str(edition.day)}
    stamps = [datetime.fromisoformat(r["collected_at"]) for r in rows]
    end = datetime.combine(edition.day, AFTER_MARKET_START, KST)
    if any(at < close_at for at in stamps):
        return {"dataset": name, "error": "collected_before_close"}
    if any(at >= end for at in stamps):
        return {"dataset": name, "error": "collected_after_regular_session"}
    if any(at > observed for at in stamps):
        return {"dataset": name, "error": "collected_after_read"}
    return None


def krx_close_lines(data, edition, changes=None):
    """The regular-session close snapshot as a compact table. The KOSPI foreign/institution flows become locked
    values; KOSPI/KOSDAQ closes become locked candidates that reconcile uses only beside a closing report of the
    same close (the index quote lags). Volume, value, all flows and the top stocks by trading value are
    service-owned display rows. A table failing any check is rejected whole (fallback)."""
    lines, values, problems, display = [], [], [], {}
    close_at = close("KR", edition.day, changes) if edition.kind == "pm" and edition.kr_session else None
    if not close_at:
        return lines, values, [{"dataset": KRX_CLOSE, "error": "no_regular_session"}], display
    observed = datetime.fromisoformat(data["observed_at"])
    accepted = {}
    for table, rows in data["rows"].items():
        name = KRX_CLOSE_TABLES[table][0]
        problem = krx_close_session(name, rows, edition, close_at, observed)
        if not problem and table == "index":
            by = {r["index"]: r for r in rows}
            if set(by) != set(KRX_MARKETS) or len(rows) != len(by):
                problem = {"dataset": name, "error": "incomplete_session"}
            elif any(Decimal(r["close"]) <= 0 or Decimal(r["prev_close"]) <= 0 for r in rows):
                problem = {"dataset": name, "error": "nonpositive_index"}
            elif any(abs((Decimal(r["close"])/Decimal(r["prev_close"])-1)*100-Decimal(r["chg_pct"])) > Decimal("0.011")
                     for r in rows):
                problem = {"dataset": name, "error": "change_inconsistent_with_previous_close"}
        if not problem and table == "flows":
            keys = {(r["market"], r["investor"]) for r in rows}
            if keys != {(m, i) for m in KRX_MARKETS for i in KRX_INVESTORS} or len(rows) != len(keys):
                problem = {"dataset": name, "error": "incomplete_session"}
        if not problem and table == "prices":
            if len({r["ticker"] for r in rows}) != len(rows) or any(Decimal(r["close"]) <= 0 for r in rows):
                problem = {"dataset": name, "error": "duplicate_or_nonpositive_rows"}
        if problem:
            problems.append(problem)
        else:
            accepted[table] = rows
    if not accepted:
        return lines, values, problems, display
    day, as_of = str(edition.day), close_at.isoformat()
    collected = min(datetime.fromisoformat(r["collected_at"]) for rows in accepted.values() for r in rows)
    lines.append(f"KRX 정규장 마감 스냅샷 · {day} · {collected.astimezone(KST):%H:%M} KST 수집 · {INDEX_LABEL}"
                 f" · 종목 {CLOSE_LABEL} · 투자자별 수급 {SESSION_LABEL}")
    display.update(session=day, collected_at=collected.isoformat(), index=[], flows=[], top=[])
    for row in sorted(accepted.get("index", []), key=lambda r: list(KRX_MARKETS).index(r["index"])):
        symbol = row["index"]
        instrument, label = KRX_MARKETS[symbol]
        price, prior = Decimal(row["close"]), Decimal(row["prev_close"])
        change = Decimal(row["chg_pct"]).quantize(Decimal("0.01"), ROUND_HALF_UP)
        line = (f"{symbol} | session={day} | close={price} pt | chg_pct={change:+.2f}% | "
                f"previous_session={edition.previous_kr_session} | previous_close={prior} pt | {INDEX_LABEL}")
        lines.append(line)
        values.append({"id": "data-"+instrument, "instrument": instrument, "value": str(price), "unit": "pt",
                       "session_date": day, "as_of": as_of, "basis": "close", "venue": "KRX",
                       "previous_value": str(prior), "previous_session_date": str(edition.previous_kr_session),
                       "reported_change": str(change), "change_unit": "%", "quote": line})
        trade = f"거래량 {krx_shares(row['volume'])} · 거래대금 {krx_amount(row['value'])}"
        lines.append(f"{symbol} 거래량·거래대금 | session={day} | {trade} | {INDEX_LABEL}")
        display["index"].append({"market": symbol, "label": label, "text": trade})
    for symbol, (_, label) in KRX_MARKETS.items():
        if "flows" not in accepted:
            break
        by = {r["investor"]: r for r in accepted["flows"] if r["market"] == symbol}
        figures = [[investor, KRX_INVESTORS[investor], krx_amount(by[investor]["net_value"], signed=True)]
                   for investor in ("indiv", "frgn", "inst")]
        lines.append(f"{symbol} 투자자별 순매수 | session={day} | "+" | ".join(f"{n} {v}" for _, n, v in figures)
                     +f" | {SESSION_LABEL}")
        display["flows"].append({"market": symbol, "label": label, "figures": figures})
        if symbol != "KOSPI":
            continue
        for investor, instrument in KRX_FLOW_INSTRUMENTS.items():
            amount = (Decimal(by[investor]["net_value"])/Decimal(100000000)).quantize(Decimal("0.01"), ROUND_HALF_UP)
            line = (f"{instrument} | KOSPI {KRX_INVESTORS[investor]} 순매수 | session={day} | value={amount} | "
                    f"unit=억원 | {SESSION_LABEL}")
            lines.append(line)
            values.append({"id": "data-"+instrument, "instrument": instrument, "value": str(amount), "unit": "억원",
                           "session_date": day, "as_of": as_of, "basis": "close", "venue": "KRX 유가증권시장",
                           "quote": line})
    names = data.get("names")
    if "prices" in accepted and names is None:
        problems.append({"dataset": KRX_CLOSE, "error": "top_stocks_omitted_master_unavailable"})
    elif "prices" in accepted:
        ranked = sorted(accepted["prices"], key=lambda r: (-Decimal(r["value"]), r["ticker"]))
        for row in ranked:
            name = names.get(row["ticker"])
            if not name:
                # The next ticker by trading value takes the place of one the master cannot name.
                problems.append({"dataset": STOCK_MASTER, "error": "stock_name_missing", "ticker": row["ticker"]})
                continue
            change = Decimal(row["chg_pct"]).quantize(Decimal("0.01"), ROUND_HALF_UP)
            text = f"{name}({row['ticker']}) {CLOSE_LABEL} {krx_price(row['close'])}원 · {change:+.2f}%"
            display["top"].append({"ticker": row["ticker"], "market": row["market"], "text": text})
            lines.append(f"거래대금 상위 {len(display['top'])} | {text} | {row['market']} | "
                         f"거래대금 {krx_amount(row['value'])} | {SESSION_LABEL}")
            if len(display["top"]) == TOP_STOCKS:
                break
    return lines, values, problems, display


def expected_prior(market, day, offset, changes=None):
    for _ in range(offset):
        day = previous(market, day, changes)
    return day


def summarize(snapshot, edition, changes=None):
    """Numbers are derived here, never by the prose writer. Historical context stays separately dated."""
    from ..company import fingerprint
    from .contracts import SourceDocument

    docs, observations, contexts, snapshot_ids = [], [], [], set()
    diagnostics = list(snapshot.get("errors", []))
    if not snapshot.get("ok"):
        diagnostics.append({"dataset": "lake", "error": snapshot.get("error", "unavailable")})
    observed = datetime.fromisoformat(snapshot.get("observed_at", utcnow().isoformat()))
    if observed > edition.cutoff:
        return {"documents": [], "observations": [], "contexts": [],
                "diagnostics": [{"dataset": "lake", "error": "data_cutoff_passed"}]}
    for name, data in snapshot.get("datasets", {}).items():
        rows = data["rows"]
        lines, values, contextual = [], [], []
        source = data["source"]
        display = None
        if name == US_CLOSE:
            lines, values, contextual, problems = us_close_lines(data, edition)
            diagnostics.extend(problems)
        elif name == KRX_CLOSE:
            lines, values, problems, display = krx_close_lines(data, edition, changes)
            diagnostics.extend(problems)
        elif name == "krx_index":
            expected = edition.kr_session if edition.kind == "pm" else (
                edition.previous_kr_session or previous("KR", edition.day, changes))
            for symbol, instrument in (("KOSPI", "kospi"), ("KOSDAQ", "kosdaq")):
                selected = {date.fromisoformat(r["date"]): r for r in rows if r["index"] == symbol}
                if len(selected) != sum(r["index"] == symbol for r in rows):
                    diagnostics.append({"dataset": name, "error": "duplicate_session", "instrument": instrument})
                    continue
                eligible = [d for d in selected if close("KR", d, changes) and d <= edition.day
                            and datetime.combine(d, datetime.min.time(), KST)+timedelta(hours=18) <= edition.cutoff]
                if not eligible:
                    diagnostics.append({"dataset": name, "error": "no_available_session", "instrument": instrument})
                    continue
                day = max(eligible)
                row = selected[day]
                price = Decimal(row["close"])
                if price <= 0:
                    diagnostics.append({"dataset": name, "error": "nonpositive_index", "instrument": instrument})
                    continue
                prior_day = expected_prior("KR", day, 1, changes)
                prior_row = selected.get(prior_day)
                if prior_row and Decimal(prior_row["close"]) <= 0:
                    prior_row = None
                line = f"{symbol} | session={day} | close={price} pt | regular KRX close"
                if prior_row and Decimal(prior_row["close"]) > 0:
                    line += f" | previous_session={prior_day} | previous_close={prior_row['close']} pt"
                lines.append(line)
                current = day == expected
                if current:
                    value = {"id": "data-"+instrument, "instrument": instrument, "value": str(price), "unit": "pt",
                             "session_date": str(day), "as_of": close("KR", day, changes).isoformat(), "basis": "close",
                             "venue": "KRX", "previous_value": prior_row["close"] if prior_row else None,
                             "previous_session_date": str(prior_day) if prior_row else None, "quote": line}
                    values.append(value)
                elif not current:
                    diagnostics.append({"dataset": name, "error": "stale_session", "instrument": instrument,
                                        "latest": str(day), "expected": str(expected)})
                if (edition.day-day).days <= 7:
                    comparisons = []
                    for sessions in (1, 5, 20):
                        past = selected.get(expected_prior("KR", day, sessions, changes))
                        if past and Decimal(past["close"]) > 0:
                            change = (price/Decimal(past["close"])-1)*100
                            comparisons.append(f"{sessions}거래일 {change:+.2f}%")
                    contextual.append(f"{symbol} · {day} 기준 · 종가 {price} pt · "+" / ".join(comparisons))
                    if edition.kind == "pm" and current and Decimal(row["value"]) >= 0:
                        turnover = Decimal(row["value"])/Decimal(100000000)
                        contextual.append(f"{symbol} 거래대금 · {day} · {turnover:.2f}억원")
        else:
            labels = {"VIXCLS": "VIX 일간 관측", "BAMLH0A0HYM2": "미 하이일드 옵션조정 스프레드(%)",
                      "base_rate": "한국 기준금리(%)", "ktb_3y": "한국 국고채 3년(%)",
                      "usdkrw": "ECOS 달러/원 일간 관측(KRW/USD)"}
            for series in sorted({r["series"] for r in rows}):
                selected = {date.fromisoformat(r["date"]): Decimal(r["value"]) for r in rows if r["series"] == series}
                if len(selected) != sum(r["series"] == series for r in rows):
                    diagnostics.append({"dataset": name, "error": "duplicate_session", "series": series})
                    continue
                # Macro dates lack release timestamps; only prior dates are context, never today's prices/releases.
                eligible = [d for d in selected if d < edition.day]
                if not eligible:
                    continue
                day = max(eligible)
                if (edition.day-day).days > 7:
                    diagnostics.append({"dataset": name, "error": "stale_context", "series": series, "latest": str(day)})
                    continue
                if name == "prices":
                    if selected[day] <= 0:
                        diagnostics.append({"dataset": name, "error": "nonpositive_adjusted_price", "series": series})
                        continue
                    expected = edition.us_session or expected_prior("US", edition.day, 1, changes)
                    if day != expected:
                        diagnostics.append({"dataset": name, "error": "stale_context", "series": series,
                                            "latest": str(day), "expected": str(expected)})
                    comparisons = []
                    for sessions in (1, 5, 20):
                        past = selected.get(expected_prior("US", day, sessions, changes))
                        if past and past > 0:
                            change = (selected[day]/past-1)*100
                            comparisons.append(f"{sessions}거래일 {change:+.2f}%")
                    if comparisons:
                        contextual.append(f"{series} ETF · {day} 기준 · "+" / ".join(comparisons)
                                          +" · 수집 수정가격 기준; 지수 종가 대용 아님")
                else:
                    contextual.append(f"{labels.get(series, series)} · 관측일 {day} · 값 {selected[day]}"
                                      " · 과거 관측 배경자료; 발표시각 미확인")
        if not lines and not contextual:
            continue
        content = "\n".join(lines+contextual)
        identity = "data-"+fingerprint([name, source, content])[:24]
        # Only receipt source/note reach the model; the close identity is already in the content.
        receipt = ({"source": {"provider": CHART_PROVIDER}, "session": source["session"], "close_at": source["close_at"],
                    "rows": rows, "provider": CHART_PROVIDER, "poll_count": len(rows), "available_at": observed.isoformat(),
                    "endpoint": f"https://{CHART_HOST}/v8/finance/chart/{{symbol}}?interval=1d&range=5d",
                    "attribution": "Provider link is attribution; every recorded poll and response digest is retained."}
                   if name == US_CLOSE else {"source": source, "rows": rows,
                "qdata_code_commit": snapshot.get("qdata_code_commit"), "available_at": observed.isoformat(),
                "note": "Provider link is attribution; exact object identity and frozen rows are retained in the edition."})
        if display:
            # Service-owned close rows the renderer shows; the stock names' master vintage is kept beside them.
            receipt.update(display=display, names=data.get("names"), master=data.get("master"))
        doc = SourceDocument(id=identity, url={"krx_index": "https://data.krx.co.kr/", "prices": "https://finance.yahoo.com/",
            "fred": "https://fred.stlouisfed.org/", "ecos": "https://ecos.bok.or.kr/", US_CLOSE: "https://finance.yahoo.com/",
            KRX_CLOSE: "https://data.krx.co.kr/"}[name],
            title="Collected market data: "+name, publisher="수집 데이터 · "+name, kind="dataset", origin_group="dataset:"+name,
            content=content, published_at=None, retrieved_at=observed, sha256=fingerprint([source, rows]),
            registration=US_CLOSE_REGISTRATION if name == US_CLOSE else "qdata.api:"+name, receipt=receipt)
        docs.append(doc.model_dump(mode="json"))
        for value in values:
            quote = value.pop("quote")
            observations.append({**value, "evidence": [{"source_id": identity, "quote": quote}]})
        contexts.extend({"text": text, "source_id": identity} for text in contextual)
        if name == KRX_CLOSE:
            snapshot_ids.add(identity)
    if snapshot_ids:
        # A same-session lake close (possible only with a cutoff after 18:00) never doubles a snapshot value.
        supplied = {o["instrument"] for o in observations if o["evidence"][0]["source_id"] in snapshot_ids}
        observations = [o for o in observations
                        if o["instrument"] not in supplied or o["evidence"][0]["source_id"] in snapshot_ids]
    return {"documents": docs, "observations": observations, "contexts": contexts, "diagnostics": diagnostics,
            "observed_at": observed.isoformat()}


def main():
    try:
        from qdata import api

        request = json.loads(sys.stdin.read(16384))
        result = read_snapshot(request["definition"], api, krx_close=request.get("krx_close") is True)
    except Exception:
        result = {"ok": False, "error": "lake_access_or_format_unavailable", "observed_at": utcnow().isoformat()}
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > 131072:
        encoded = json.dumps({"ok": False, "error": "lake_result_too_large", "observed_at": utcnow().isoformat()})
    print(encoded)


if __name__ == "__main__":
    main()
