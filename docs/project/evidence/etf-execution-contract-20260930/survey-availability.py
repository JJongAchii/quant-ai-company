"""Bounded, read-only survey of ETF publication/vintage evidence; metadata only."""

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

BUCKET = "insight-invest-datalake"
ROOT = Path(__file__).resolve().parents[4]
OUTPUT = Path(__file__).with_name("availability-survey.json")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pages(client, prefix, delimiter=None):
    args = {"Bucket": BUCKET, "Prefix": prefix}
    if delimiter:
        args["Delimiter"] = delimiter
    entries, prefixes, count = [], [], 0
    while True:
        result = client.list_objects_v2(**args)
        entries.extend(result.get("Contents", []))
        prefixes.extend(p["Prefix"] for p in result.get("CommonPrefixes", []))
        count += 1
        if not result.get("IsTruncated"):
            return entries, sorted(set(prefixes)), count
        args["ContinuationToken"] = result["NextContinuationToken"]


def version_summary(client, key):
    result = client.list_object_versions(Bucket=BUCKET, Prefix=key, MaxKeys=1000)
    if result.get("IsTruncated"):
        raise ValueError("object_version_survey_truncated")
    exact = [e for e in result.get("Versions", []) if e["Key"] == key]
    return {"key": key, "versions": [{"version_id": e["VersionId"], "size_bytes": e["Size"],
                                      "etag": e["ETag"], "last_modified": e["LastModified"].isoformat(),
                                      "latest": e["IsLatest"]} for e in exact],
            "delete_markers": sum(e["Key"] == key for e in result.get("DeleteMarkers", [])),
            "truncated": False}


def local_inventory():
    lake = Path("/Users/achii/Quant/data-lake")
    parents = ["raw/krx_etf/snapshot", "raw/krx_etf/ohlcv", "raw/krx_etf/receipts",
               "raw/krx_etf/vintages", "raw/krx_etf/archive", "receipts/krx_etf",
               "vintages/krx_etf", "archive/krx_etf", "logs"]
    results = {}
    for name in parents:
        directory = lake / name
        if not directory.exists():
            results[name] = {"exists": False, "files": 0}
            continue
        files = [p for p in directory.rglob("*") if p.is_file() and not p.is_symlink()]
        results[name] = {"exists": True, "files": len(files),
                         "suffix_counts": dict(Counter(p.suffix for p in files)),
                         "filesystem_modification_year_counts": dict(Counter(
                             datetime.fromtimestamp(p.stat().st_mtime, UTC).strftime("%Y") for p in files)),
                         "file_bodies_read": False}
    return {"root": str(lake), "directories": results,
            "filesystem_times_are_not_source_publication_receipts": True}


def main():
    import botocore.session
    from botocore.config import Config

    if OUTPUT.exists():
        raise ValueError("survey_receipt_must_be_new")
    client = botocore.session.get_session().create_client(
        "s3", region_name="ap-northeast-2",
        config=Config(retries={"mode": "standard", "max_attempts": 2}),
    )
    child_entries, children, child_pages = pages(client, "qdata-raw/raw/krx_etf/", delimiter="/")
    archive_prefixes = ["qdata-raw/receipts/krx_etf/", "qdata-raw/vintages/krx_etf/",
                        "qdata-raw/archive/krx_etf/", "qdata/receipts/krx_etf/",
                        "qdata/vintages/krx_etf/", "qdata/archive/krx_etf/", "backups/qdata/"]
    archives = {}
    for prefix in archive_prefixes:
        entries, subprefixes, count = pages(client, prefix, delimiter="/")
        archives[prefix] = {"direct_object_count": len(entries), "child_prefixes": subprefixes,
                            "listing_pages": count}
    logs, _, log_pages = pages(client, "qdata-raw/logs/")
    log_metadata = {"objects": len(logs), "listing_pages": log_pages,
                    "object_last_modified_year_counts": dict(Counter(e["LastModified"].strftime("%Y")
                                                                       for e in logs)),
                    "pipeline_key_year_counts": dict(Counter(e["Key"].rsplit("/", 1)[-1][9:13]
                        for e in logs if e["Key"].rsplit("/", 1)[-1].startswith("pipeline-"))),
                    "log_bodies_read": False}
    keys = ["qdata/clean/krx_etf.parquet", "qdata/clean/krx_etf_meta.parquet"]
    keys += ["qdata-raw/raw/krx_etf/snapshot/" + day + ".parquet"
             for day in ["20220801", "20221229", "20230102", "20251230"]]
    versions = [version_summary(client, key) for key in keys]
    client.close()
    existing = ["docs/project/evidence/etf-raw-lineage-20260930/raw-body-manifest.json",
                "docs/project/evidence/etf-raw-lineage-20260930/raw-lineage-receipt.json",
                "docs/project/evidence/research-programs-20260930/etf-provenance-recovery.json",
                "docs/project/evidence/research-programs-20260929/etf-s3-version-history-readback.json"]
    receipt = {"schema_version": 1, "observed_at": datetime.now(UTC).isoformat(),
               "scope": "listed_mirror_and_known_local_paths_only_not_all_possible_backups",
               "script_sha256": digest(Path(__file__)),
               "raw_dataset_children": {"prefix": "qdata-raw/raw/krx_etf/", "children": children,
                                        "direct_object_count": len(child_entries), "pages": child_pages},
               "named_archive_prefixes": archives, "pipeline_log_metadata": log_metadata,
               "exact_key_version_survey": versions, "local_inventory": local_inventory(),
               "prior_evidence_sha256": {name: digest(ROOT / name) for name in existing},
               "historical_publication_receipts_recovered": False,
               "preparation_clean_bytes_recovered": False,
               "other_unlisted_backups_exist": "unknown",
               "source_policy_is_not_per_session_receipt": True,
               "price_bodies_read_by_this_survey": False, "sealed_price_rows_read": False,
               "strategy_performance_computed": False, "scientific_trials_added": 0,
               "production_mutated": False}
    with OUTPUT.open("x") as stream:
        json.dump(receipt, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps({"receipt": str(OUTPUT.relative_to(ROOT)), "archive_prefixes_checked": len(archives),
                      "log_objects": len(logs), "historical_receipts_recovered": False}, sort_keys=True))


if __name__ == "__main__":
    main()
