"""Protected worker-only domestic ETF/equity evaluator, using frozen JSON snapshots.

This module is copied into an operator-provisioned quant-lab code bundle. The company
server never imports candidate code or runs this entrypoint. No lake/network access.
Prices are price returns, not dividend-reinvested total returns. Missing delisting
settlement or unexecutable held prices rejects the evaluation, never drops a holding.
"""

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import math
import statistics
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))


def finite(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("non_finite_or_non_numeric")
    return float(value)


def snapshot(payload):
    required = {"date", "ticker", "market", "open", "close", "adj_close", "value", "available_at", "tradable"}
    if not isinstance(payload, list) or not payload:
        raise ValueError("empty_snapshot")
    seen, rows = set(), []
    for original in payload:
        if not isinstance(original, dict) or not required <= set(original):
            raise ValueError("snapshot_missing_fields")
        row = dict(original)
        day = date.fromisoformat(row["date"])
        known = datetime.fromisoformat(row["available_at"])
        if (known.tzinfo is None or known < datetime.combine(day, time(18), KST)
                or row["market"] not in {"kr_stock", "kr_etf"} or type(row["tradable"]) is not bool
                or not isinstance(row["ticker"], str) or not row["ticker"]):
            raise ValueError("snapshot_pit_or_market_contract")
        key = (day, row["ticker"])
        if key in seen:
            raise ValueError("duplicate_point_in_time_key")
        seen.add(key)
        for field in ("open", "close", "adj_close", "value"):
            row[field] = finite(row[field])
            if (row[field] < 0 or (field in {"close", "adj_close"} and row[field] == 0)
                    or (field == "open" and row[field] == 0 and row["tradable"])):
                raise ValueError("invalid_price_or_liquidity")
        rows.append(row)
    return sorted(rows, key=lambda row: (row["date"], row["ticker"]))


def history_at(rows, trade_day):
    cutoff = datetime.combine(date.fromisoformat(trade_day), time(9), KST)
    # The lake's adjusted series is anchored to its newest asof. A later split or
    # distribution can rescale every earlier value. Rebase each ticker to its
    # latest *known* raw close before exposing history to candidate code; this
    # removes future-asof scale while preserving ratios known at the signal time.
    # Fresh copies also prevent candidate mutation of the evaluator's own rows.
    history = [dict(row) for row in rows if row["date"] < trade_day
               and datetime.fromisoformat(row["available_at"]) < cutoff]
    latest = {}
    for row in history:
        ticker = row["ticker"]
        if ticker not in latest or row["date"] > latest[ticker]["date"]:
            latest[ticker] = row
    for row in history:
        reference = latest[row["ticker"]]
        row["adj_close"] *= reference["close"] / reference["adj_close"]
    return history


def adjusted_open(row):
    return row["open"] * row["adj_close"] / row["close"]


def prediction(candidate, history, config, *, strategy):
    method = candidate.weights if strategy else candidate.predict
    values = method(history, json.loads(json.dumps(config)))
    if not isinstance(values, dict) or not values:
        raise ValueError("candidate_requires_nonempty_ticker_values")
    latest = max(row["date"] for row in history)
    universe = {row["ticker"] for row in history if row["date"] == latest}
    if not set(values) <= universe:
        raise ValueError("candidate_outside_historical_universe")
    values = {ticker: finite(value) for ticker, value in values.items()}
    if strategy and (min(values.values()) < 0 or sum(values.values()) > 1 + 1e-12):
        raise ValueError("candidate_must_be_unlevered_long_only")
    return values


def simulate(rows, candidate, config, spec):
    start, end = spec["development"]["start"], spec["development"]["end"]
    by_day = {}
    for row in rows:
        if row["market"] != spec["market"]:
            raise ValueError("snapshot_market_mismatch")
        by_day.setdefault(row["date"], {})[row["ticker"]] = row
    days = sorted(day for day in by_day if start <= day <= end)
    if len(days) < 2 or days[0] != start or days[-1] != end:
        raise ValueError("development_endpoints_must_be_observed_sessions")
    strategy = spec["kind"] == "strategy"
    series = {"base": [(days[0], 0.0)], "stress": [(days[0], 0.0)]}
    observations, positions, turnover, exposure = [], {}, 0.0, 0.0
    for day, following in zip(days, days[1:], strict=False):
        history = history_at(rows, day)
        if not history:
            raise ValueError("warmup_required")
        proposed = prediction(candidate, history, config, strategy=strategy)
        current, future = by_day[day], by_day[following]
        required = set(proposed) | set(positions)
        if any(t not in current or t not in future for t in required):
            raise ValueError("missing_held_price_or_delisting_settlement")
        if any(not current[t]["tradable"] or not future[t]["tradable"] for t in required):
            raise ValueError("unexecutable_price_requires_event_aware_adapter")
        returns = {t: adjusted_open(future[t]) / adjusted_open(current[t]) - 1 for t in proposed}
        if not strategy:
            if len(proposed) < 3:
                raise ValueError("cross_section_too_small")
            x, y = list(proposed.values()), [returns[t] for t in proposed]
            if statistics.pstdev(x) == 0 or statistics.pstdev(y) == 0:
                raise ValueError("undefined_cross_sectional_correlation")
            observations.append((following, statistics.correlation(x, y)))
            continue
        traded = sum(abs(proposed.get(t, 0) - positions.get(t, 0)) for t in required)
        gross = 1 + sum(weight * returns[t] for t, weight in proposed.items())
        for label in ("base", "stress"):
            cost = spec[label + "_cost_bps"] / 10000 * traded
            if cost >= 1 or gross <= 0:
                raise ValueError("portfolio_insolvent")
            series[label].append((following, (1 - cost) * gross - 1))
        positions = {t: w * (1 + returns[t]) / gross for t, w in proposed.items()}
        turnover += traded
        exposure = max(exposure, sum(proposed.values()))
    years = (date.fromisoformat(end) - date.fromisoformat(start)).days / 365.25
    return series, observations, {"annual_turnover": turnover / years, "gross_exposure": exposure}


def csv_bytes(rows, field):
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["date", field])
    writer.writerows(rows)
    return stream.getvalue().encode()


def sha(content):
    return hashlib.sha256(content).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))


def run(action, config_path, manifest_path, output, *, input_root=Path("/inputs")):
    manifest, config = json.loads(manifest_path.read_text()), json.loads(config_path.read_text())
    spec = manifest["spec"]
    # Names are a public profile contract; qualification has no development mount.
    names = ["warmup.json"] if action == "qualify" else ["warmup.json", "development.json"]
    data, hashes = [], {}
    for name in names:
        content = (input_root / name).read_bytes()
        hashes[name] = sha(content)
        if hashes[name] != manifest["plan"]["input_files"].get(name):
            raise ValueError("input_digest_mismatch")
        data.extend(json.loads(content))
    rows = snapshot(data)
    for row in rows:
        if any(window["start"] <= row["date"] <= window["end"] for window in spec["sealed"]):
            raise ValueError("sealed_observation")
        if action == "qualify" and row["date"] >= spec["development"]["start"]:
            raise ValueError("qualification_must_precede_development")
        if row["date"] > spec["development"]["end"]:
            raise ValueError("unapproved_future_observation")
    module_spec = importlib.util.spec_from_file_location("research_candidate", config_path.parent / "candidate.py")
    candidate = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(candidate)
    if action == "qualify":
        for check in (lambda: snapshot([]), lambda: finite(float("nan"))):
            try:
                check()
            except ValueError:
                pass
            else:
                raise ValueError("qualification_negative_guard_failed")
        if json.loads(json.dumps(rows, allow_nan=False)) != rows:
            raise ValueError("qualification_json_roundtrip_failed")
        day = (date.fromisoformat(rows[-1]["date"]) + timedelta(days=1)).isoformat()
        a = prediction(candidate, history_at(rows, day), config, strategy=spec["kind"] == "strategy")
        b = prediction(candidate, history_at(rows, day), config, strategy=spec["kind"] == "strategy")
        if a != b:
            raise ValueError("candidate_is_not_deterministic")
        dates = sorted({row["date"] for row in rows})
        dump(output / "qualification.json", {"schema_version": 1, "kind": "adaptive_qualification",
            "trial_id": manifest["trial_id"], "plan_digest": manifest["plan_digest"],
            "code_commit": manifest["plan"]["code_commit"], "config_files": manifest["plan"]["config_files"],
            "input_files": hashes, "sample_count": len(dates), "json_dates": dates,
            "json_datetimes": [day + "T18:00:00+09:00" for day in dates],
            "typed_schema": {"date": "date", "available_at": "datetime", "open": "float", "ticker": "string"},
            "primary_unit": spec["objective"]["unit"], "empty_sample_rejected": True,
            "non_finite_rejected": True, "json_roundtrip_passed": True, "performance_read": False, "sealed_read": False})
        return
    series, observations, risks = simulate(rows, candidate, config, spec)
    result = {"schema_version": 1, "kind": "adaptive_result", "trial_id": manifest["trial_id"],
        "plan_digest": manifest["plan_digest"], "code_commit": manifest["plan"]["code_commit"], "output_files": {}}
    if spec["kind"] == "strategy":
        nav = peak = 1.0
        drawdown = 0.0
        for _, value in series["stress"]:
            nav *= 1 + value
            peak = max(peak, nav)
            drawdown = max(drawdown, 1 - nav / peak)
        days = (date.fromisoformat(spec["development"]["end"]) - date.fromisoformat(spec["development"]["start"])).days
        score = nav ** (365.25 / days) - 1
        risks["max_drawdown"] = drawdown
        for label in ("base", "stress"):
            name = label + ".csv"
            content = csv_bytes(series[label], "net_return")
            (output / name).write_bytes(content)
            result["output_files"][name] = sha(content)
            result[label + "_returns"] = {"path": name, "sha256": sha(content), "frequency": "daily", "unit": "fraction-per-period"}
        count = len(series["stress"])
    else:
        # The evaluator copy of evaluation.py is protected and imported from this code bundle.
        from domestic_statistics import evaluate_observations

        content = csv_bytes(observations, "value")
        (output / "observations.csv").write_bytes(content)
        result["output_files"]["observations.csv"] = sha(content)
        result["observations"] = "observations.csv"
        measurement = evaluate_observations(content, spec["evaluation"], spec["development"])
        score, count, risks = measurement["value"], measurement["sample_count"], {}
    result["metrics"] = {"primary": {**spec["objective"], "value": score}, "risks": risks,
        "sample_count": count, "sample_window": spec["development"]}
    dump(output / "result.json", result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["qualify", "evaluate"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.action, args.config, args.manifest, args.output)
