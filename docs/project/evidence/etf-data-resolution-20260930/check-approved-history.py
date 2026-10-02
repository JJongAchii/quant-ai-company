"""Compare approved histories with a bounded pinned-qdata read; no strategy runs.

Only the protected engine's snapshot/history functions are called. Candidate
code, prediction, fitting, simulation and evaluation functions are never called.
The canonical API panel is kept in a private directory, not published to Git.
Historical release/revision evidence cannot be inferred from these checks.
"""

import argparse
import hashlib
import json
import math
import os
import runpy
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

PROGRAM = "53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b"
QDATA = "d6d7d0ed066ec49541e9acdd657c9ec5692ffc52"
ENGINE = "1984bf6b7fa5997a8b7ba446061ff663bdf78c683776be945da9130a067480cc"
INPUTS = {
    "warmup.json": "e3dc0ed5aa86d2abdad96d39d92b313bf1fa74a13c2f662bcd76d184a578ba81",
    "development.json": "6482c63da05e443a51560f119cd715500026aeab8d787ecd2dfa201f19a00574",
}
COHORT = ["069500", "229200", "153130", "214980", "196230", "157450", "371460", "102110", "357870", "305720"]


def digest(content):
    return hashlib.sha256(content).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def pinned_package(repository):
    names = subprocess.check_output(
        ["git", "-C", str(repository), "ls-tree", "-r", "--name-only", QDATA, "src/qdata"], text=True
    ).splitlines()
    if not names:
        raise ValueError("missing_pinned_qdata_tree")
    for name in names:
        expected = subprocess.check_output(["git", "-C", str(repository), "show", QDATA + ":" + name])
        if (repository / name).read_bytes() != expected:
            raise ValueError("qdata_source_changed:" + name)
    return len(names)


def history_comparison(engine, frozen, current, days):
    checks, mismatches, future_visible = 0, 0, 0
    max_relative_error = 0.0
    first_counts = None
    for day in days:
        left = engine["history_at"](frozen, day)
        right = engine["history_at"](current, day)
        if len(left) != len(right):
            raise ValueError("history_lengths_differ:" + day)
        if first_counts is None:
            first_counts = dict(sorted(Counter(row["ticker"] for row in left).items()))
        for approved, observed in zip(left, right, strict=True):
            future_visible += approved["date"] >= day or observed["date"] >= day
            for field in ("date", "ticker", "market", "available_at", "tradable", "open", "close", "value"):
                if approved[field] != observed[field]:
                    raise ValueError("non_adjusted_history_differs:" + field)
            a, b = approved["adj_close"], observed["adj_close"]
            if not (math.isfinite(a) and math.isfinite(b) and a > 0 and b > 0):
                raise ValueError("invalid_rebased_adjusted_close")
            max_relative_error = max(max_relative_error, abs(a - b) / a)
            mismatches += not math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-8)
            checks += 1
    return {"signal_dates_checked": len(days), "history_rows_compared": checks,
            "rebased_price_mismatches": mismatches, "max_relative_error": max_relative_error,
            "same_day_or_future_rows_exposed": future_visible,
            "first_signal_history_rows_per_ticker": first_counts,
            "uses_generated_available_at_assumption": True,
            "source_publication_or_revision_times_proven": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qdata-repo", type=Path, required=True)
    parser.add_argument("--approved-input-dir", type=Path, required=True)
    parser.add_argument("--private-panel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[4]
    output, panel = args.output.resolve(), args.private_panel.resolve()
    output.relative_to(root / "docs/project/evidence")
    panel.relative_to(root / ".local")
    if output.exists() or panel.exists():
        raise ValueError("evidence_destinations_must_be_new")
    engine_path = root / "src/quant_company/research/reference/domestic_engine.py"
    if digest(engine_path.read_bytes()) != ENGINE:
        raise ValueError("protected_engine_changed")
    engine = runpy.run_path(str(engine_path))
    frozen = []
    for name, expected in INPUTS.items():
        content = (args.approved_input_dir / name).read_bytes()
        if digest(content) != expected:
            raise ValueError("approved_input_changed:" + name)
        frozen.extend(json.loads(content))
    frozen = engine["snapshot"](frozen)
    if len(frozen) != 8350 or {row["ticker"] for row in frozen} != set(COHORT):
        raise ValueError("approved_cohort_changed")
    if min(row["date"] for row in frozen) != "2022-08-01" or max(row["date"] for row in frozen) != "2025-12-30":
        raise ValueError("approved_window_changed")
    if any(row["available_at"] != row["date"] + "T23:59:00+09:00" for row in frozen):
        raise ValueError("availability_assumption_differs_from_generator")

    repository = args.qdata_repo.resolve()
    package_count = pinned_package(repository)
    sys.path.insert(0, str(repository / "src"))
    os.environ["QDATA_LAKE"] = "s3://insight-invest-datalake/qdata"
    from qdata import api

    if Path(api.__file__).resolve() != repository / "src/qdata/api.py":
        raise ValueError("wrong_qdata_import")
    before = api.inspect_dataset("krx_etf", sample_rows=0)["source"]
    frame = api.load_krx_etf_prices(start="2022-08-01", end="2025-12-30", tickers=COHORT)
    api_rows, current = [], []
    for raw in frame.to_dict("records"):
        day = raw["date"].date().isoformat()
        if not "2022-08-01" <= day <= "2025-12-30" or str(raw["ticker"]) not in COHORT:
            raise ValueError("api_returned_unapproved_row")
        row = {"date": day, "ticker": str(raw["ticker"])}
        for field in ("open", "close", "adj_close", "chg_pct", "volume", "value"):
            value = raw[field]
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("non_finite_api_field:" + field)
            row[field] = float(value)
        api_rows.append(row)
        tradable = all(row[field] > 0 for field in ("open", "close", "adj_close", "volume", "value"))
        current.append({name: row[name] for name in ("date", "ticker", "open", "close", "adj_close", "value")}
                       | {"market": "kr_etf", "available_at": day + "T23:59:00+09:00", "tradable": tradable})
    after = api.inspect_dataset("krx_etf", sample_rows=0)["source"]
    if before != after:
        raise ValueError("lake_identity_changed_during_read")
    current = engine["snapshot"](current)
    api_rows.sort(key=lambda row: (row["date"], row["ticker"]))
    if len(current) != len(frozen) or [(r["date"], r["ticker"]) for r in current] != [(r["date"], r["ticker"]) for r in frozen]:
        raise ValueError("api_cohort_keys_differ")
    field_diffs = Counter()
    for left, right in zip(frozen, current, strict=True):
        for field in ("open", "close", "adj_close", "value", "tradable"):
            if left[field] != right[field]:
                field_diffs[field] += 1
    days = sorted({row["date"] for row in frozen if row["date"] >= "2023-01-02"})
    # The final date is a terminal evaluation price, not a new signal date.
    comparison = history_comparison(engine, frozen, current, days[:-1])
    content = canonical(api_rows)
    panel.parent.mkdir(parents=True, exist_ok=True)
    with panel.open("xb") as stream:
        stream.write(content)
    panel.chmod(0o600)
    report = {"schema_version": 1, "observed_at": datetime.now(UTC).isoformat(),
              "scope": "protected_history_equivalence_and_current_api_export_only",
              "script_sha256": digest(Path(__file__).read_bytes()),
              "program_digest": PROGRAM, "approved_input_files": INPUTS,
              "protected_engine_sha256": ENGINE, "qdata_commit": QDATA,
              "qdata_package_files_verified": package_count,
              "current_api_source": before,
              "current_api_panel": {"rows": len(api_rows), "sessions": len(days) + 104,
                                    "sha256": digest(content), "bytes": len(content),
                                    "window": {"start": "2022-08-01", "end": "2025-12-30"},
                                    "cohort": COHORT, "stored_private": True,
                                    "preparation_source_bytes_recovered": False},
              "current_vs_approved_field_differences": dict(sorted(field_diffs.items())),
              "approved_engine_history_comparison": comparison,
              "remaining_gaps": ["preparation_source_bytes_and_original_transform_receipt",
                                 "historical_publication_collection_and_revision_receipts"],
              "claims_not_established": ["historically_received_point_in_time_vintage",
                                         "actual_order_fills_or_cash_distribution_total_return",
                                         "GMM_feature_validity_fitting_or_profitability"],
              "strategy_or_prediction_code_called": False, "performance_computed": False,
              "sealed_price_rows_returned_or_used": False, "scientific_trials_added": 0,
              "approved_inputs_changed": False, "production_mutated": False,
              "data_readiness_decision": "not_made"}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"receipt": str(output.relative_to(root)), "history": comparison,
                      "data_readiness_decision": "not_made"}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
