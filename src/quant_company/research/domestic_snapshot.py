"""Prepare bounded, point-in-time KRX inputs through the pinned qdata API.

This is an offline operator command. It records data quality; it does not backtest,
select on future survival, submit a job, or modify the lake.
"""

import argparse
import hashlib
import json
import math
import os
import subprocess
from datetime import date
from pathlib import Path


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _day(value):
    return value.date().isoformat() if hasattr(value, "date") else date.fromisoformat(str(value)).isoformat()


def _valid(row):
    return all(isinstance(row[field], (int, float)) and math.isfinite(row[field]) and row[field] > 0
               for field in ("open", "close", "adj_close", "volume", "value"))


def _bad_fields(row):
    return [field for field in ("open", "close", "adj_close", "volume", "value")
            if not isinstance(row[field], (int, float)) or not math.isfinite(row[field]) or row[field] <= 0]


def select_cohort(prices, *, selection_date, cohort_size, market, metadata=None):
    """Rank only the last 20 observed sessions before the development boundary."""
    days = sorted({_day(row["date"]) for row in prices if _day(row["date"]) <= selection_date})
    if selection_date not in days or len(days) < 20:
        raise ValueError("selection_date_requires_20_observed_sessions")
    window = set(days[-20:])
    excluded = set()
    if market == "kr_etf":
        if metadata is None:
            raise ValueError("etf_metadata_required")
        exact = [row for row in metadata if _day(row["date"]) == selection_date]
        if len(exact) != len({str(row["ticker"]) for row in exact}):
            raise ValueError("duplicate_etf_selection_metadata")
        eligible = {str(row["ticker"]) for row in exact if row["is_leveraged"] is False
                    and row["is_inverse"] is False}
    else:
        eligible = {str(row["ticker"]) for row in prices if str(row["ticker"]).endswith("0")}
    grouped = {}
    for row in prices:
        day, ticker = _day(row["date"]), str(row["ticker"])
        if day not in window or ticker not in eligible:
            continue
        if not _valid(row):
            excluded.add(ticker)
            continue
        values = grouped.setdefault(ticker, {})
        if day in values:
            raise ValueError("duplicate_selection_date_ticker")
        values[day] = float(row["value"])
    ranked = sorted(((sum(values.values()) / 20, ticker) for ticker, values in grouped.items()
                     if len(values) == 20 and ticker not in excluded), key=lambda item: (-item[0], item[1]))
    if len(ranked) < cohort_size:
        raise ValueError("insufficient_predevelopment_tradable_cohort")
    return [ticker for _, ticker in ranked[:cohort_size]], {"selection_sessions": days[-20:],
        "selection_eligible_count": len(ranked), "selection_excluded_bad_rows": len(excluded)}


def build_rows(prices, *, market, cohort, warmup_start, development_start, development_end):
    """Keep every usable source row, and report every rejected/gapped cohort session."""
    selected = set(cohort)
    by_key, bad, halted, sessions, seen = {}, [], [], set(), set()
    for raw in prices:
        day, ticker = _day(raw["date"]), str(raw["ticker"])
        if ticker not in selected or not warmup_start <= day <= development_end:
            continue
        key = (day, ticker)
        if key in seen:
            raise ValueError("duplicate_cohort_date_ticker")
        seen.add(key)
        sessions.add(day)
        if (not isinstance(raw["open"], (int, float)) or not math.isfinite(raw["open"]) or raw["open"] < 0
                or not isinstance(raw["close"], (int, float)) or not math.isfinite(raw["close"]) or raw["close"] <= 0
                or not isinstance(raw["adj_close"], (int, float)) or not math.isfinite(raw["adj_close"])
                or raw["adj_close"] <= 0 or not isinstance(raw["volume"], (int, float))
                or not math.isfinite(raw["volume"]) or raw["volume"] < 0
                or not isinstance(raw["value"], (int, float)) or not math.isfinite(raw["value"])
                or raw["value"] < 0):
            bad.append({"date": day, "ticker": ticker, "fields": _bad_fields(raw)})
            continue
        tradable = _valid(raw)
        if not tradable:
            halted.append({"date": day, "ticker": ticker, "fields": _bad_fields(raw)})
        by_key[key] = {"date": day, "ticker": ticker, "market": market,
            "open": float(raw["open"]), "close": float(raw["close"]),
            "adj_close": float(raw["adj_close"]), "value": float(raw["value"]),
            # KRX daily rows are known after the close. 23:59 KST is a
            # conservative availability bound for a next-session 09:00 signal.
            "available_at": day + "T23:59:00+09:00", "tradable": tradable}
    if not sessions or development_start not in sessions or development_end not in sessions:
        raise ValueError("development_endpoints_not_observed")
    missing = [{"date": day, "ticker": ticker} for day in sorted(sessions) for ticker in cohort
               if (day, ticker) not in seen]
    rows = [by_key[key] for key in sorted(by_key)]
    warmup = [row for row in rows if row["date"] < development_start]
    development = [row for row in rows if row["date"] >= development_start]
    if not warmup or not development:
        raise ValueError("empty_input_phase")
    return warmup, development, {"bad_rows": bad, "missing_rows": missing, "nontrading_rows": halted,
        "observed_sessions": len(sessions), "warmup_rows": len(warmup),
        "development_rows": len(development), "development_months": len({r["date"][:7] for r in development})}


def prepare(*, api, destination, market, lake_uri, qdata_commit, warmup_start,
            selection_date, development_start, development_end, cohort_size):
    if not (warmup_start < selection_date < development_start < development_end):
        raise ValueError("invalid_predeclared_windows")
    if market not in {"kr_stock", "kr_etf"} or cohort_size < 3:
        raise ValueError("invalid_market_or_cohort")
    if destination.exists():
        raise ValueError("snapshot_destination_must_be_new")
    names = ["krx_prices"] if market == "kr_stock" else ["krx_etf", "krx_etf_meta"]
    before = {name: api.inspect_dataset(name, sample_rows=0)["source"] for name in names}
    if market == "kr_stock":
        selection = api.load_krx_prices(start=warmup_start, end=selection_date, market="KOSPI",
            columns=["market", "open", "close", "adj_close", "volume", "value"])
        metadata = None
    else:
        selection = api.load_krx_etf_prices(start=warmup_start, end=selection_date)
        metadata = api.load_krx_etf_meta(start=selection_date, end=selection_date)
    cohort, selection_checks = select_cohort(selection.to_dict("records"), selection_date=selection_date,
        cohort_size=cohort_size, market=market, metadata=None if metadata is None else metadata.to_dict("records"))
    if market == "kr_stock":
        panel = api.load_krx_prices(start=warmup_start, end=development_end, tickers=cohort,
            market="KOSPI", columns=["market", "open", "close", "adj_close", "volume", "value"])
    else:
        panel = api.load_krx_etf_prices(start=warmup_start, end=development_end, tickers=cohort)
    warmup, development, quality = build_rows(panel.to_dict("records"), market=market, cohort=cohort,
        warmup_start=warmup_start, development_start=development_start, development_end=development_end)
    after = {name: api.inspect_dataset(name, sample_rows=0)["source"] for name in names}
    if before != after:
        raise ValueError("lake_source_changed_during_snapshot")
    destination.mkdir(parents=True)
    for filename, rows in (("warmup.json", warmup), ("development.json", development)):
        (destination / filename).write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    ready = not quality["bad_rows"] and not quality["missing_rows"] and quality["development_months"] >= 30
    receipt = {"schema_version": 1, "state": "ready" if ready else "blocked_data_quality", "market": market,
        "lake_uri": lake_uri, "qdata_commit": qdata_commit, "source": before, "cohort_rule":
        "top_20_session_average_traded_value_asof_selection_date; stock KOSPI common suffix 0; ETF no leverage/inverse",
        "cohort": cohort, "selection_date": selection_date, "selection_checks": selection_checks,
        "warmup_start": warmup_start, "development_start": development_start,
        "development_end": development_end, "quality": quality,
        "input_files": {name: _digest(destination / name) for name in ("warmup.json", "development.json")}}
    (destination / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qdata-root", type=Path, required=True)
    parser.add_argument("--qdata-commit", required=True)
    parser.add_argument("--lake-uri", required=True)
    parser.add_argument("--market", choices=["kr_stock", "kr_etf"], required=True)
    parser.add_argument("--warmup-start", required=True)
    parser.add_argument("--selection-date", required=True)
    parser.add_argument("--development-start", required=True)
    parser.add_argument("--development-end", required=True)
    parser.add_argument("--cohort-size", type=int, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    from qdata import api

    root = args.qdata_root.resolve()
    if Path(api.__file__).resolve() != root / "src/qdata/api.py":
        parser.error("qdata must be imported from the exact pinned checkout")
    commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if commit != args.qdata_commit or subprocess.check_output(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], text=True).strip():
        parser.error("qdata checkout is not the exact clean approved commit")
    if not args.lake_uri.startswith("s3://") or os.environ.get("QDATA_LAKE") != args.lake_uri:
        parser.error("QDATA_LAKE must equal the explicit read-only S3 mirror URI")
    receipt = prepare(api=api, destination=args.destination.resolve(), market=args.market,
        lake_uri=args.lake_uri, qdata_commit=commit, warmup_start=args.warmup_start,
        selection_date=args.selection_date, development_start=args.development_start,
        development_end=args.development_end, cohort_size=args.cohort_size)
    print(json.dumps({"state": receipt["state"], "market": receipt["market"],
                      "input_files": receipt["input_files"], "quality": {k: len(receipt["quality"][k])
                      for k in ("bad_rows", "missing_rows")}}, sort_keys=True))


if __name__ == "__main__":
    main()
