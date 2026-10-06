"""Trace approved ETF input provenance without evaluating a strategy.

Price rows are consumed only through the pinned qdata API and only within the
approved warmup/development window. S3 raw keys and pipeline logs are inspected
for provenance; raw price bodies and sealed-period price rows are never read.
"""

import argparse
import hashlib
import json
import math
import os
import re
import runpy
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

BUCKET = "insight-invest-datalake"
PROGRAM_DIGEST = "53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def aws(*args):
    result = subprocess.run(
        ["aws", "s3api", *args, "--output", "json"], capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


def objects(prefix):
    found, pages, token = [], 0, None
    while True:
        args = ["list-objects-v2", "--bucket", BUCKET, "--prefix", prefix, "--no-paginate"]
        if token:
            args.extend(["--continuation-token", token])
        result = aws(*args)
        found.extend(result.get("Contents", []))
        pages += 1
        if not result.get("IsTruncated"):
            return found, pages
        token = result["NextContinuationToken"]


def head(key):
    result = aws("head-object", "--bucket", BUCKET, "--key", key)
    return {name: result.get(name) for name in
            ("ContentLength", "LastModified", "ETag", "VersionId", "Metadata")}


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qdata-repo", type=Path, required=True)
    parser.add_argument("--local-lake", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[4]
    output = args.output.resolve()
    output.relative_to(root / "docs/project/evidence")
    if output.exists() or output.with_name("etf-raw-object-manifest.json").exists():
        raise ValueError("provenance_receipts_must_be_new")
    prior_path = root / "docs/project/evidence/research-programs-20260929/verify-etf-against-qdata.py"
    prior = runpy.run_path(str(prior_path))
    prepared = json.loads((root / "docs/project/evidence/research-programs-20260928/real-inputs.json")
                          .read_text())["markets"]["kr_etf"]
    qdata_repo = args.qdata_repo.resolve()
    source_root = qdata_repo / "src"
    sys.path.insert(0, str(source_root))
    from qdata import api
    from qdata.store.krx_etf import compute_reference_adjusted_close

    verified = prior["pinned_package"](
        Path(api.__file__), qdata_repo, prepared["qdata_commit"], source_root
    )
    frozen = []
    for name, expected in prepared["input_files"].items():
        path = root / ".local/science-inputs/kr_etf-prepared-v2" / name
        if sha(path) != expected:
            raise ValueError("approved_input_changed:" + name)
        frozen.extend(json.loads(path.read_text()))
    dates = sorted({row["date"] for row in frozen})
    if dates[0] != prepared["warmup_start"] or dates[-1] != prepared["development_end"]:
        raise ValueError("approved_window_changed")

    os.environ["QDATA_LAKE"] = str(args.local_lake.resolve())
    local = {name: api.inspect_dataset(name, sample_rows=0) for name in prepared["source"]}
    local_matches = {
        name: entry["source"]["size_bytes"] == prepared["source"][name]["size_bytes"]
        for name, entry in local.items()
    }
    os.environ["QDATA_LAKE"] = prepared["lake_uri"]
    before = api.inspect_dataset("krx_etf", sample_rows=0)["source"]
    frame = api.load_krx_etf_prices(
        start=dates[0], end=dates[-1], tickers=prepared["cohort"]
    )
    comparison = prior["compare_panel"](frame.to_dict("records"), frozen, cohort=prepared["cohort"])
    if comparison["current_rows"] != len(frozen) or any(
        value for key, value in comparison["mismatch_counts"].items() if key != "adj_close"
    ):
        raise ValueError("current_api_panel_cannot_support_shape_comparison")
    frozen_by_ticker = {ticker: sorted((r for r in frozen if r["ticker"] == ticker),
                                     key=lambda r: r["date"]) for ticker in prepared["cohort"]}
    checked = mismatches = 0
    max_error = 0.0
    for ticker, rows in frozen_by_ticker.items():
        current = frame[frame["ticker"] == ticker].sort_values("date")
        if [prior["day"](d) for d in current["date"]] != [r["date"] for r in rows]:
            raise ValueError("ticker_dates_differ:" + ticker)
        # This terminal anchor is used only to compare curve shape. It is not
        # exposed to a candidate or used to create a feature, label or signal.
        rebuilt = compute_reference_adjusted_close(current["close"], current["chg_pct"])
        anchor = rows[-1]["close"] / rows[-1]["adj_close"]
        for actual, row in zip(rebuilt, rows, strict=True):
            expected = row["adj_close"] * anchor
            if not math.isfinite(actual):
                raise ValueError("invalid_reconstructed_curve:" + ticker)
            error = abs(float(actual) - expected) / expected
            max_error = max(max_error, error)
            mismatches += not math.isclose(float(actual), expected, rel_tol=1e-10, abs_tol=1e-8)
            checked += 1
    after = api.inspect_dataset("krx_etf", sample_rows=0)["source"]
    if before != after:
        raise ValueError("current_lake_changed_during_read")

    raw, page_count = [], 0
    for year in sorted({d[:4] for d in dates}):
        entries, pages = objects("qdata-raw/raw/krx_etf/snapshot/" + year)
        raw.extend(entries)
        page_count += pages
    by_date = {datetime.strptime(entry["Key"].rsplit("/", 1)[-1][:-8], "%Y%m%d").date().isoformat(): entry
               for entry in raw}
    missing = sorted(set(dates) - set(by_date))
    selected = [{"trade_date": d, **by_date[d]} for d in dates if d in by_date]
    manifest = output.with_name("etf-raw-object-manifest.json")
    sample_days = sorted({dates[0], prepared["selection_date"], prepared["development_start"], dates[-1]})
    sample_heads = {d: head(by_date[d]["Key"]) for d in sample_days if d in by_date}
    logs, _ = objects("qdata-raw/logs/pipeline-20260925")
    log_key = "qdata-raw/logs/pipeline-20260925-1000.log"
    log_path = root / ".local/ops-access/qdata-pipeline-20260925-1000.log"
    log_before = head(log_key)
    aws("get-object", "--bucket", BUCKET, "--key", log_key, str(log_path))
    if head(log_key) != log_before or log_path.stat().st_size != log_before["ContentLength"]:
        raise ValueError("pipeline_log_changed_during_read")
    allowed = re.compile(
        r"^(?:===== pipeline (?:start|done).*|===== finalizer .*|\[krx_etf[^\]]*\].*|"
        r"\s*krx_etf(?:_meta|_profile)?\.parquet \([0-9.]+ MB\) → "
        r"s3://insight-invest-datalake/qdata/clean/krx_etf(?:_meta|_profile)?\.parquet)$"
    )
    excerpts = [{"line": i, "text": line} for i, line in enumerate(log_path.read_text().splitlines(), 1)
                if allowed.fullmatch(line)]
    source_files = ["src/qdata/api.py", "src/qdata/ingest/krx_etf.py", "src/qdata/ingest/krx.py",
                    "src/qdata/store/krx_etf.py", "src/qdata/store/lake.py", "src/qdata/store/mirror.py"]
    generator = "src/quant_company/research/domestic_snapshot.py"
    generator_commit = subprocess.check_output(
        ["git", "-C", str(root), "log", "-1", "--format=%H", "--", generator], text=True
    ).strip()
    expected_generator = subprocess.check_output(
        ["git", "-C", str(root), "show", f"{generator_commit}:{generator}"]
    )
    if (root / generator).read_bytes() != expected_generator:
        raise ValueError("snapshot_generator_has_uncommitted_changes")
    write(manifest, {"schema_version": 1, "scope": "approved_session_raw_object_metadata_only",
                     "objects": selected, "missing_dates": missing, "price_bodies_read": False})
    receipt = {
        "schema_version": 1, "observed_at": datetime.now(UTC).isoformat(),
        "scope": "approved_etf_provenance_recovery_read_only", "program_digest": PROGRAM_DIGEST,
        "script_sha256": sha(Path(__file__)), "prior_verifier_sha256": sha(prior_path),
        "qdata_commit": prepared["qdata_commit"], "verified_package_file_count": verified,
        "pinned_source_files": {name: sha(qdata_repo / name) for name in source_files},
        "snapshot_generator": {"path": generator, "commit": generator_commit,
                               "sha256": sha(root / generator)},
        "approved_input_files": prepared["input_files"], "current_source": before,
        "current_panel": comparison,
        "adjusted_curve_shape_check": {"rows_checked": checked, "mismatches": mismatches,
                                      "max_relative_error": max_error,
                                      "absolute_original_anchor_recovered": False},
        "local_copy": {"inspection": local, "sizes_match_preparation": local_matches,
                       "original_bytes_recovered": False},
        "raw_archive": {"uri": f"s3://{BUCKET}/qdata-raw/raw/krx_etf/snapshot/",
                        "session_count": len(dates), "covered_sessions": len(selected),
                        "missing_dates": missing, "listing_pages": page_count,
                        "upload_date_counts": dict(Counter(e["LastModified"][:10] for e in selected)),
                        "sample_heads": sample_heads, "manifest_path": str(manifest.relative_to(root)),
                        "manifest_sha256": sha(manifest), "original_publication_times_verified": False,
                        "preparation_raw_content_hashes_recorded": False},
        "pipeline_log": {"uri": f"s3://{BUCKET}/{log_key}", "objects": logs,
                         "head": log_before, "sha256": sha(log_path), "excerpts": excerpts,
                         "original_clean_object_hash_recorded": False},
        "availability": {"input_field_source": "snapshot_generator_day_plus_23_59_KST_assumption",
                         "source_collector_policy": "skip_current_day_before_18_00_KST",
                         "historical_publication_receipts_recovered": False},
        "remaining_gaps": ["preparation_clean_object_bytes_and_raw_to_clean_manifest",
                           "historical_source_availability_and_revision_vintages"],
        "raw_price_bodies_read": False, "sealed_price_rows_read": False,
        "strategy_performance_computed": False, "scientific_trials_added": 0,
        "data_readiness_decision": "not_made", "production_mutated": False,
    }
    write(output, receipt)
    print(json.dumps({"receipt": str(output.relative_to(root)), "raw_sessions": len(selected),
                      "missing_raw_dates": missing, "shape_checked_rows": checked,
                      "shape_mismatches": mismatches, "readiness": "not_made"}, sort_keys=True))


if __name__ == "__main__":
    main()
