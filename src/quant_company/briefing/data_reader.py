"""Fixed, bounded qdata API queries. No model code, lake internals, or collector writes."""

import json
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal

from .contracts import BriefEdition
from .schedule import KST, close, previous, utcnow

DATASETS = ("krx_index", "prices", "fred", "ecos")
MAX_OBJECT_BYTES = 4 * 1024 * 1024
MAX_ROWS = 100000
ETFS = ("SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "HYG", "GLD", "DBC")


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
        if name == "krx_index":
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
                if edition.kind == "pm" and current:
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
        doc = SourceDocument(id=identity, url={"krx_index": "https://data.krx.co.kr/", "prices": "https://finance.yahoo.com/",
            "fred": "https://fred.stlouisfed.org/", "ecos": "https://ecos.bok.or.kr/"}[name],
            title="Collected market data: "+name, publisher="수집 데이터 · "+name, kind="dataset", origin_group="dataset:"+name,
            content=content, published_at=None, retrieved_at=observed, sha256=fingerprint([source, rows]),
            registration="qdata.api:"+name, receipt={"source": source, "rows": rows,
                "qdata_code_commit": snapshot.get("qdata_code_commit"), "available_at": observed.isoformat(),
                "note": "Provider link is attribution; exact object identity and frozen rows are retained in the edition."})
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
