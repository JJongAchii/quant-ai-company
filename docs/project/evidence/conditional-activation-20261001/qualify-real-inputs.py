"""Independently check frozen numerical inputs via pinned qdata; no candidate or returns."""

import hashlib
import json
import math
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
EVIDENCE = Path(__file__).resolve().parent
DESTINATION = ROOT / ".local/conditional-activation-20261001/inputs"
PIN = "d6d7d0ed066ec49541e9acdd657c9ec5692ffc52"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    with path.open("x") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")


def main():
    draft_root = ROOT / "docs/project/evidence/etf-execution-contract-20260930"
    policy = read(draft_root / "data-policy-draft.json")
    lineage_ref = policy["lineage_receipt"]
    lineage_path = ROOT / lineage_ref["path"]
    assert sha(lineage_path) == lineage_ref["sha256"], "lineage_changed"
    lineage = read(lineage_path)
    manifest_ref = policy["raw_manifest"]
    manifest_path = ROOT / manifest_ref["path"]
    assert sha(manifest_path) == manifest_ref["sha256"], "manifest_changed"
    manifest = read(manifest_path)
    expected_dates = {item["trade_date"] for item in manifest["objects"]}
    assert len(expected_dates) == 835 and not manifest["failures"]
    assert max(expected_dates) == "2025-12-30" and min(expected_dates) == "2022-08-01"
    source = ROOT / ".local/etf-execution-contract-20260930/inputs"
    assert read(source / "receipt.json")["state"] == "review_draft_not_ready"
    old_receipt_sha = sha(source / "receipt.json")
    inputs = {name: source / name for name in policy["input_files"]}
    assert {name: sha(path) for name, path in inputs.items()} == policy["input_files"]
    cohort = policy["universe_contract"]["selected_tickers"]
    qdata = ROOT / ".local/quant-data-pinned"
    assert subprocess.check_output(["git", "-C", str(qdata), "rev-parse", "HEAD"], text=True).strip() == PIN
    assert not subprocess.check_output(["git", "-C", str(qdata), "status", "--porcelain", "--untracked-files=no"], text=True).strip()
    sys.path.insert(0, str(qdata / "src"))
    from qdata import api

    assert Path(api.__file__).resolve() == qdata / "src/qdata/api.py"
    roots = {}
    for phase, files in lineage["reconstructed_clean"].items():
        paths = [Path(entry["inspection"]["source"]["uri"]) for entry in files.values()]
        assert all(not path.is_symlink() and sha(path) == files[path.stem]["sha256"] for path in paths)
        assert len({path.parent.parent for path in paths}) == 1
        roots[phase] = paths[0].parent.parent
    os.environ["QDATA_LAKE"] = str(roots["selection_prefix"])
    prefix = api.load_krx_etf_prices(start="2022-08-01", end="2022-12-29")
    metadata = api.load_krx_etf_meta(start="2022-12-29", end="2022-12-29")
    assert not prefix.duplicated(["date", "ticker"]).any()
    assert not metadata.duplicated(["date", "ticker"]).any()
    eligible = set(metadata.loc[(metadata["is_leveraged"] == False) & (metadata["is_inverse"] == False), "ticker"])  # noqa: E712
    sessions = sorted(prefix["date"].dt.date.astype(str).unique())[-20:]
    grouped = {}
    for item in prefix.to_dict("records"):
        day = item["date"].date().isoformat()
        if day in sessions and item["ticker"] in eligible:
            grouped.setdefault(item["ticker"], []).append(item)
    ranking = []
    for ticker, rows in grouped.items():
        if len(rows) == 20 and all(math.isfinite(row[field]) and row[field] > 0
                                   for row in rows for field in ("open", "close", "adj_close", "volume", "value")):
            ranking.append((sum(row["value"] for row in rows) / 20, ticker))
    selected = [ticker for _, ticker in sorted(ranking, key=lambda row: (-row[0], row[1]))[:10]]
    assert selected == cohort, "predevelopment_cohort_changed"
    os.environ["QDATA_LAKE"] = str(roots["approved_window"])
    panel = api.load_krx_etf_prices(start="2022-08-01", end="2025-12-30", tickers=cohort)
    assert not panel.duplicated(["date", "ticker"]).any()
    actual = {(row["date"].date().isoformat(), row["ticker"]): row for row in panel.to_dict("records")}
    assert set(actual) == {(day, ticker) for day in expected_dates for ticker in cohort}
    prepared = [row for path in inputs.values() for row in read(path)]
    seen = set()
    for row in prepared:
        assert set(row) == {"date", "ticker", "market", "open", "close", "adj_close", "value", "available_at", "tradable"}
        key = row["date"], row["ticker"]
        assert key not in seen and key in actual
        seen.add(key)
        assert row["market"] == "kr_etf" and row["tradable"] is True
        assert row["available_at"] == row["date"] + "T23:59:00+09:00"
        assert row["date"] < "2026-01-01"
        for field in ("open", "close", "adj_close", "value"):
            assert type(row[field]) in (int, float) and math.isfinite(row[field]) and row[field] > 0
            assert math.isclose(row[field], actual[key][field], rel_tol=1e-13, abs_tol=1e-9), (key, field)
        assert math.isfinite(row["open"] * row["adj_close"] / row["close"])
    assert seen == set(actual) and len(prepared) == 8350
    warmup = read(inputs["warmup.json"])
    development = read(inputs["development.json"])
    assert len(warmup) == 1040 and len(development) == 7310
    assert max(row["date"] for row in warmup) == "2022-12-29"
    assert min(row["date"] for row in development) == "2023-01-02"
    assert len({row["date"][:7] for row in development}) == 36
    assert all(sum(row["ticker"] == ticker for row in warmup) == 104 for ticker in cohort)
    assert sha(ROOT / "src/quant_company/research/reference/domestic_engine.py") == policy["protected_engine_sha256"]
    assert sha(source / "receipt.json") == old_receipt_sha
    DESTINATION.mkdir(parents=True, exist_ok=False)
    for name, path in inputs.items():
        with (DESTINATION / name).open("xb") as stream:
            stream.write(path.read_bytes())
    receipt = {
        "schema_version": 1, "state": "ready", "market": "kr_etf",
        "scope": "numeric_quality_only_for_frozen_vintage_retrospective_policy",
        "observed_at": datetime.now(UTC).isoformat(), "script_sha256": sha(Path(__file__)),
        "qdata_commit": PIN, "input_files": policy["input_files"],
        "quality": {"bad_rows": [], "missing_rows": [], "nontrading_rows": [],
                    "observed_sessions": 835, "warmup_rows": 1040, "development_rows": 7310,
                    "development_months": 36},
        "independent_api_row_comparison": {"matched_rows": 8350, "mismatches": 0,
                                           "duplicate_keys": 0, "extra_keys": 0, "missing_keys": 0},
        "selection": {"cohort": cohort, "sessions": sessions, "reproduced": True,
                      "post_selection_rows_used": 0, "input_value_is_raw_trading_value": True},
        "evaluation_prices": {"positive_finite_adjusted_open_rows": 8350,
                              "formula": "open * adj_close / close", "executable_fill_verified": False,
                              "cash_distribution_reinvestment_verified": False},
        "historical_point_in_time_verified": False, "original_conditions_verified": False,
        "availability_is_generated_assumption": True, "staff_data_decision": "not_made",
        "source_reports": {str(lineage_path.relative_to(ROOT)): sha(lineage_path),
                           str(manifest_path.relative_to(ROOT)): sha(manifest_path)},
        "previous_draft_receipt_sha256": old_receipt_sha, "previous_draft_receipt_preserved": True,
        "production_mutated": False, "sealed_price_rows_read": False, "candidate_called": False,
        "performance_computed": False, "scientific_trials_added": 0, "qualified_worker": False,
    }
    write(DESTINATION / "receipt.json", receipt)
    write(EVIDENCE / "numeric-quality.json", receipt)
    print(json.dumps({"numeric_quality": "passed", "prepared_rows": 8350, "cohort": len(cohort),
                      "receipt_sha256": sha(DESTINATION / "receipt.json"), "staff_data_decision": "not_made"}))


if __name__ == "__main__":
    main()
