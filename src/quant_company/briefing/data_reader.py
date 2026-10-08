"""Fixed, bounded qdata API queries and public US close chart reads. No model code, lake internals, or collector writes."""

import hashlib
import http.client
import json
import os
import ssl
import sys
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, date, datetime, timedelta
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


def read_snapshot(definition, api, *, clock=utcnow):
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
    result["observed_at"] = clock().isoformat()
    return result


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


def expected_prior(market, day, offset, changes=None):
    for _ in range(offset):
        day = previous(market, day, changes)
    return day


def summarize(snapshot, edition, changes=None):
    """Numbers are derived here, never by the prose writer. Historical context stays separately dated."""
    from ..company import fingerprint
    from .contracts import SourceDocument

    docs, observations, contexts = [], [], []
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
        if name == US_CLOSE:
            lines, values, contextual, problems = us_close_lines(data, edition)
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
        doc = SourceDocument(id=identity, url={"krx_index": "https://data.krx.co.kr/", "prices": "https://finance.yahoo.com/",
            "fred": "https://fred.stlouisfed.org/", "ecos": "https://ecos.bok.or.kr/", US_CLOSE: "https://finance.yahoo.com/"}[name],
            title="Collected market data: "+name, publisher="수집 데이터 · "+name, kind="dataset", origin_group="dataset:"+name,
            content=content, published_at=None, retrieved_at=observed, sha256=fingerprint([source, rows]),
            registration=US_CLOSE_REGISTRATION if name == US_CLOSE else "qdata.api:"+name, receipt=receipt)
        docs.append(doc.model_dump(mode="json"))
        for value in values:
            quote = value.pop("quote")
            observations.append({**value, "evidence": [{"source_id": identity, "quote": quote}]})
        contexts.extend({"text": text, "source_id": identity} for text in contextual)
    return {"documents": docs, "observations": observations, "contexts": contexts, "diagnostics": diagnostics,
            "observed_at": observed.isoformat()}


def main():
    try:
        from qdata import api

        request = json.loads(sys.stdin.read(16384))
        result = read_snapshot(request["definition"], api)
    except Exception:
        result = {"ok": False, "error": "lake_access_or_format_unavailable", "observed_at": utcnow().isoformat()}
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > 131072:
        encoded = json.dumps({"ok": False, "error": "lake_result_too_large", "observed_at": utcnow().isoformat()})
    print(encoded)


if __name__ == "__main__":
    main()
