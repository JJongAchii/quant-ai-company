"""Hash approved raw ETF bodies and trace them through unchanged qdata builders.

The operator archives exactly the approved daily objects into a new company-local
lake. Price consumers use the pinned qdata API. Reconstructed inputs are audit
artifacts, never active research inputs. No strategy or sealed period is run.
"""

import argparse
import hashlib
import json
import math
import os
import runpy
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

BUCKET = "insight-invest-datalake"
PROGRAM = "53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b"
MANIFEST_SHA = "963dc1ea3420fb46802ba9ab50fea9d0f8c098777eaa0f67b603b4aa81235b68"
QDATA_COMMIT = "d6d7d0ed066ec49541e9acdd657c9ec5692ffc52"
GENERATOR_SHA = "976b105b9c8cc245ce9c1e91b04915322548faddac41b2b40852d44baf51dc7b"
ENGINE_SHA = "1984bf6b7fa5997a8b7ba446061ff663bdf78c683776be945da9130a067480cc"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, value):
    with path.open("x") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")


def approved_objects(manifest, dates):
    objects = manifest["objects"]
    observed = [entry["trade_date"] for entry in objects]
    if (manifest["missing_dates"] or len(observed) != len(set(observed))
            or set(observed) != set(dates)):
        raise ValueError("raw_manifest_must_cover_exact_approved_sessions")
    for entry in objects:
        expected = "qdata-raw/raw/krx_etf/snapshot/" + entry["trade_date"].replace("-", "") + ".parquet"
        if entry["Key"] != expected or entry["Size"] <= 0:
            raise ValueError("raw_object_outside_exact_approved_scope")
    return objects


def identity(response):
    return {"size_bytes": response["ContentLength"], "etag": response["ETag"],
            "last_modified": response["LastModified"].isoformat(),
            "version_id": response.get("VersionId")}


def require_manifest_identity(observed, entry):
    if (observed["size_bytes"] != entry["Size"] or observed["etag"] != entry["ETag"]
            or datetime.fromisoformat(observed["last_modified"])
                != datetime.fromisoformat(entry["LastModified"])):
        raise ValueError("raw_source_identity_changed:" + entry["trade_date"])


def download_one(client, entry, directory):
    started = datetime.now(UTC).isoformat()
    before = identity(client.head_object(Bucket=BUCKET, Key=entry["Key"]))
    require_manifest_identity(before, entry)
    response = client.get_object(Bucket=BUCKET, Key=entry["Key"], IfMatch=entry["ETag"])
    stream = response["Body"]
    try:
        if identity(response) != before:
            raise ValueError("raw_source_changed_before_get:" + entry["trade_date"])
        data = stream.read()
    finally:
        stream.close()
    after = identity(client.head_object(Bucket=BUCKET, Key=entry["Key"]))
    if after != before or len(data) != before["size_bytes"]:
        raise ValueError("raw_source_changed_during_get:" + entry["trade_date"])
    destination = directory / (entry["trade_date"].replace("-", "") + ".parquet")
    with destination.open("xb") as output:
        output.write(data)
    destination.chmod(0o444)
    return {"trade_date": entry["trade_date"], "key": entry["Key"], **before,
            "sha256": hashlib.sha256(data).hexdigest(), "conditional_get": True,
            "head_get_head_identity_equal": True,
            "read_started_at": started, "read_completed_at": datetime.now(UTC).isoformat()}


def authority(root):
    prepared = json.loads((root / "docs/project/evidence/research-programs-20260928/real-inputs.json")
                          .read_text())["markets"]["kr_etf"]
    draft = json.loads((root / "docs/project/evidence/research-programs-20260928/first-program-draft.json")
                       .read_text())
    if draft["program_digest"] != PROGRAM or prepared["qdata_commit"] != QDATA_COMMIT:
        raise ValueError("approved_program_or_qdata_identity_changed")
    etf = [e for e in draft["program"]["envelopes"] if e["name"] in {"etf_claim", "etf_strategy"}]
    if len(etf) != 2 or any(e["template"]["data"]["input_files"] != prepared["input_files"] for e in etf):
        raise ValueError("approved_input_contract_changed")
    frozen = []
    for name, digest in prepared["input_files"].items():
        path = root / ".local/science-inputs/kr_etf-prepared-v2" / name
        if sha(path) != digest:
            raise ValueError("approved_input_bytes_changed:" + name)
        frozen.extend(json.loads(path.read_text()))
    dates = sorted({row["date"] for row in frozen})
    if (len(frozen) != 8350 or len(dates) != 835 or dates[0] != prepared["warmup_start"]
            or dates[-1] != prepared["development_end"]):
        raise ValueError("approved_window_changed")
    manifest = root / "docs/project/evidence/research-programs-20260930/etf-raw-object-manifest.json"
    if sha(manifest) != MANIFEST_SHA:
        raise ValueError("raw_manifest_bytes_changed")
    return prepared, frozen, approved_objects(json.loads(manifest.read_text()), dates)


def download(objects, scratch, output):
    import botocore.session
    from botocore.config import Config

    directory = scratch / "bounded/raw/krx_etf/snapshot"
    directory.mkdir(parents=True, exist_ok=False)
    client = botocore.session.get_session().create_client(
        "s3", region_name="ap-northeast-2",
        config=Config(max_pool_connections=4, retries={"mode": "standard", "max_attempts": 3}),
    )
    entries, failures = [], []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(download_one, client, entry, directory): entry for entry in objects}
        for future in as_completed(futures):
            entry = futures[future]
            try:
                entries.append(future.result())
            except Exception as error:
                # Record exception type and public object key; never credential-bearing errors.
                failures.append({"trade_date": entry["trade_date"], "key": entry["Key"],
                                 "error_type": type(error).__name__})
            if (len(entries) + len(failures)) % 100 == 0:
                print(json.dumps({"downloaded": len(entries), "failed": len(failures)}), flush=True)
    client.close()
    receipt = {"schema_version": 1, "observed_at": datetime.now(UTC).isoformat(),
               "scope": "current_raw_bodies_for_exact_approved_sessions",
               "program_digest": PROGRAM, "source_manifest_sha256": MANIFEST_SHA,
               "script_sha256": sha(Path(__file__)), "objects": sorted(entries, key=lambda e: e["trade_date"]),
               "failures": sorted(failures, key=lambda e: e["trade_date"]),
               "total_bytes": sum(e["size_bytes"] for e in entries),
               "raw_body_downloads_completed": len(entries),
               "all_raw_price_bodies_read": len(entries) == len(objects), "sealed_price_rows_read": False,
               "historical_publication_times_verified": False, "production_mutated": False}
    write_new(output / "raw-body-manifest.json", receipt)
    if failures or len(entries) != len(objects):
        raise RuntimeError("incomplete_raw_download_see_manifest")
    print(json.dumps({"phase": "download_complete", "objects": len(entries),
                      "total_bytes": receipt["total_bytes"]}), flush=True)


def check_local_bodies(objects, scratch, manifest):
    import pyarrow.parquet as pq

    if (manifest["program_digest"] != PROGRAM or manifest["source_manifest_sha256"] != MANIFEST_SHA
            or manifest["script_sha256"] != sha(Path(__file__)) or manifest["failures"]):
        raise ValueError("raw_download_receipt_drift")
    directory = scratch / "bounded/raw/krx_etf/snapshot"
    expected = {entry["trade_date"].replace("-", "") + ".parquet" for entry in objects}
    if {p.name for p in directory.iterdir()} != expected:
        raise ValueError("local_raw_files_outside_exact_scope")
    entries = {entry["trade_date"]: entry for entry in manifest["objects"]}
    if len(entries) != len(objects) or len(entries) != len(manifest["objects"]):
        raise ValueError("raw_body_manifest_incomplete_or_duplicate")
    schemas, details = {}, []
    required = {"ISU_SRT_CD", "NAV", "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "TDD_CLSPRC",
                "FLUC_RT", "ACC_TRDVOL", "ACC_TRDVAL", "OBJ_STKPRC_IDX", "ISU_ABBRV", "IDX_IND_NM",
                "INVSTASST_NETASST_TOTAMT", "MKTCAP", "LIST_SHRS"}
    for original in objects:
        entry = entries[original["trade_date"]]
        path = directory / (entry["trade_date"].replace("-", "") + ".parquet")
        if path.is_symlink() or entry["key"] != original["Key"] or sha(path) != entry["sha256"]:
            raise ValueError("local_raw_body_hash_or_key_drift")
        require_manifest_identity(entry, original)
        parquet = pq.ParquetFile(path)
        schema = parquet.schema_arrow
        if not required.issubset(schema.names) or parquet.metadata.num_rows <= 0:
            raise ValueError("raw_snapshot_missing_required_schema_or_rows")
        schema_key = hashlib.sha256(str(schema.remove_metadata()).encode()).hexdigest()
        schemas.setdefault(schema_key, {"columns": schema.names, "session_count": 0})["session_count"] += 1
        # Forensic key inspection catches duplicates before the unchanged builder deduplicates.
        # Numeric price bodies are consumed by the pinned builder, then through qdata.api.
        keys = parquet.read(columns=["ISU_SRT_CD"])["ISU_SRT_CD"].to_pylist()
        if any(not isinstance(k, str) or not k.strip() for k in keys) or len(keys) != len(set(keys)):
            raise ValueError("raw_snapshot_null_or_duplicate_ticker:" + entry["trade_date"])
        details.append({"trade_date": entry["trade_date"], "rows": len(keys), "schema_sha256": schema_key})
    return {"objects": len(details), "rows": sum(d["rows"] for d in details), "schemas": schemas,
            "duplicate_date_ticker_rows": 0, "empty_snapshots": 0, "sessions": details}


def compare(root, prepared, frozen, objects, scratch, output, qdata_repo):
    prior_path = root / "docs/project/evidence/research-programs-20260929/verify-etf-against-qdata.py"
    prior = runpy.run_path(str(prior_path))
    generator_path = root / "src/quant_company/research/domestic_snapshot.py"
    engine_path = root / "src/quant_company/research/reference/domestic_engine.py"
    if sha(generator_path) != GENERATOR_SHA or sha(engine_path) != ENGINE_SHA:
        raise ValueError("frozen_generator_or_engine_changed")
    generator = runpy.run_path(str(generator_path))
    sys.path.insert(0, str(qdata_repo / "src"))
    from qdata import api
    from qdata.store.krx_etf import build_clean_krx_etf, build_clean_krx_etf_meta

    package_count = prior["pinned_package"](Path(api.__file__), qdata_repo, QDATA_COMMIT, qdata_repo / "src")
    body_manifest_path = output / "raw-body-manifest.json"
    body_manifest = json.loads(body_manifest_path.read_text())
    raw_checks = check_local_bodies(objects, scratch, body_manifest)
    write_new(output / "raw-schema-and-keys.json", raw_checks)
    roots = {"approved_window": scratch / "bounded", "selection_prefix": scratch / "selection-prefix"}
    prefix = roots["selection_prefix"] / "raw/krx_etf/snapshot"
    prefix.mkdir(parents=True, exist_ok=False)
    for entry in objects:
        if entry["trade_date"] <= prepared["selection_date"]:
            filename = entry["trade_date"].replace("-", "") + ".parquet"
            shutil.copyfile(roots["approved_window"] / "raw/krx_etf/snapshot" / filename, prefix / filename)
            (prefix / filename).chmod(0o444)
    built = {}
    for name, lake in roots.items():
        if (lake / "clean").exists():
            raise ValueError("reconstructed_clean_destination_must_be_new")
        print(json.dumps({"phase": "pinned_build", "scope": name}), flush=True)
        files = {"krx_etf": build_clean_krx_etf(lake), "krx_etf_meta": build_clean_krx_etf_meta(lake)}
        os.environ["QDATA_LAKE"] = str(lake)
        built[name] = {dataset: {"sha256": sha(path), "inspection": api.inspect_dataset(dataset, sample_rows=0)}
                       for dataset, path in files.items()}
        if any(v["inspection"]["row_count"] != (raw_checks["rows"] if name == "approved_window" else
               sum(s["rows"] for s in raw_checks["sessions"] if s["trade_date"] <= prepared["selection_date"]))
               for v in built[name].values()):
            raise ValueError("raw_to_clean_row_loss")
    os.environ["QDATA_LAKE"] = str(roots["selection_prefix"])
    selection = prior["selection_check"](
        api.load_krx_etf_prices(start=prepared["warmup_start"], end=prepared["selection_date"]).to_dict("records"),
        api.load_krx_etf_meta(start=prepared["selection_date"], end=prepared["selection_date"]).to_dict("records"),
        selection_date=prepared["selection_date"], cohort=prepared["cohort"],
    )
    selection["raw_sessions_used"] = len(list(prefix.glob("*.parquet")))
    selection["post_selection_raw_objects_used"] = 0
    os.environ["QDATA_LAKE"] = str(roots["approved_window"])
    panel = api.load_krx_etf_prices(start=prepared["warmup_start"], end=prepared["development_end"],
                                    tickers=prepared["cohort"]).to_dict("records")
    comparison = prior["compare_panel"](panel, frozen, cohort=prepared["cohort"])
    warmup, development, quality = generator["build_rows"](
        panel, market="kr_etf", cohort=prepared["cohort"], warmup_start=prepared["warmup_start"],
        development_start=prepared["development_start"], development_end=prepared["development_end"],
    )
    audit_inputs = scratch / "reconstructed-audit-inputs"
    audit_inputs.mkdir(exist_ok=False)
    for filename, rows in (("warmup.json", warmup), ("development.json", development)):
        (audit_inputs / filename).write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":"),
                                                        allow_nan=False))
    original = {(r["date"], r["ticker"]): r for r in frozen}
    generated = {(r["date"], r["ticker"]): r for r in warmup + development}
    fields = ["date", "ticker", "market", "open", "close", "value", "available_at", "tradable"]
    input_mismatches = {field: sum(r[field] != original[key][field] for key, r in generated.items()
                                  if key in original) for field in fields}
    shape = {"rows_checked": 0, "mismatches": 0, "max_relative_error": 0.0}
    for ticker in prepared["cohort"]:
        keys = sorted(key for key in original if key[1] == ticker)
        if any(key not in generated for key in keys):
            raise ValueError("reconstructed_input_key_missing")
        anchor = original[keys[-1]]["close"] / original[keys[-1]]["adj_close"]
        for key in keys:
            actual, expected = generated[key]["adj_close"], original[key]["adj_close"] * anchor
            if not math.isfinite(actual) or actual <= 0:
                raise ValueError("reconstructed_adjusted_curve_invalid")
            shape["max_relative_error"] = max(shape["max_relative_error"], abs(actual - expected) / expected)
            shape["mismatches"] += not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-8)
            shape["rows_checked"] += 1
    raw_checks.pop("sessions")
    receipt = {"schema_version": 1, "observed_at": datetime.now(UTC).isoformat(),
               "scope": "current_raw_to_pinned_clean_to_approved_input_lineage",
               "program_digest": PROGRAM, "script_sha256": sha(Path(__file__)),
               "qdata_commit": QDATA_COMMIT, "verified_package_file_count": package_count,
               "pinned_builder_sha256": sha(qdata_repo / "src/qdata/store/krx_etf.py"),
               "pinned_numeric_normalizer_sha256": sha(qdata_repo / "src/qdata/store/krx_index.py"),
               "generator_sha256": GENERATOR_SHA, "frozen_engine_sha256": ENGINE_SHA,
               "approved_input_files": prepared["input_files"],
               "raw_bodies": {"uri": f"s3://{BUCKET}/qdata-raw/raw/krx_etf/snapshot/",
                              "total_bytes": body_manifest["total_bytes"],
                              "body_manifest_path": str(body_manifest_path.relative_to(root)),
                              "body_manifest_sha256": sha(body_manifest_path),
                              "source_manifest_sha256": MANIFEST_SHA, **raw_checks},
               "reconstructed_clean": built, "selection_prefix_check": selection,
               "approved_panel_comparison": comparison, "generator_quality": quality,
               "generated_input_comparison": {"non_adjustment_field_mismatches": input_mismatches,
                   "missing_keys": len(original.keys() - generated.keys()),
                   "extra_keys": len(generated.keys() - original.keys()),
                   "adjusted_curve_shape": shape,
                   "audit_only_input_files": {name: sha(audit_inputs / name)
                                               for name in prepared["input_files"]}},
               "availability": {"generated_field": "trade_day_T23:59:00+09:00_assumption",
                                "historical_source_publication_times_verified": False},
               "absolute_preparation_clean_bytes_recovered": False,
               "absolute_preparation_adjustment_anchor_recovered": False,
               "reconstructed_inputs_activated": False, "raw_price_bodies_read": True,
               "sealed_price_rows_read": False, "strategy_performance_computed": False,
               "scientific_trials_added": 0, "data_readiness_decision": "not_made",
               "production_mutated": False,
               "remaining_gaps": ["preparation_time_source_bytes_and_execution_manifest",
                                  "historical_source_availability_and_revision_vintages"]}
    write_new(output / "raw-lineage-receipt.json", receipt)
    print(json.dumps({"phase": "comparison_complete", "raw_rows": raw_checks["rows"],
                      "cohort_matches": selection["approved_cohort_matches"],
                      "non_adjustment_field_mismatches": input_mismatches,
                      "shape_mismatches": shape["mismatches"], "readiness": "not_made"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["download", "compare"], required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--qdata-repo", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[4]
    scratch = args.scratch.resolve()
    scratch.relative_to(root / ".local/etf-raw-lineage-20260930")
    if scratch == root / ".local/etf-raw-lineage-20260930":
        raise ValueError("scratch_requires_new_run_directory")
    prepared, frozen, objects = authority(root)
    output = Path(__file__).resolve().parent
    if args.phase == "download":
        if scratch.exists() or (output / "raw-body-manifest.json").exists():
            raise ValueError("download_run_and_receipt_must_be_new")
        download(objects, scratch, output)
    else:
        if (output / "raw-lineage-receipt.json").exists() or (output / "raw-schema-and-keys.json").exists():
            raise ValueError("comparison_receipts_must_be_new")
        compare(root, prepared, frozen, objects, scratch, output, args.qdata_repo.resolve())


if __name__ == "__main__":
    main()
