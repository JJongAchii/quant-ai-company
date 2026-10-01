"""Bounded operator readback of research state and source archive metadata.

This command reads public research records and named source-object metadata.
It does not query credentials, fetch price bodies, write production or replay.
"""

import argparse
import json
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path

PROGRAM = "e06537d3-fac3-5c8c-bf25-ddabb3c7e282"
DIGEST = "53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b"
BUCKET = "insight-invest-datalake"


def run_json(command):
    result = subprocess.run(command, text=True, capture_output=True, check=True, timeout=60)
    return json.loads(result.stdout)


def aws(*args):
    return run_json(["aws", "s3api", *args, "--output", "json"])


def listed(prefix, delimiter=None):
    args = ["list-objects-v2", "--bucket", BUCKET, "--prefix", prefix, "--no-paginate"]
    if delimiter:
        args += ["--delimiter", delimiter]
    result = aws(*args)
    if result.get("IsTruncated"):
        raise ValueError("inventory_truncated:" + prefix)
    return {"prefixes": [item["Prefix"] for item in result.get("CommonPrefixes", [])],
            "objects": [{name: item[name] for name in ("Key", "Size", "LastModified", "ETag")}
                        for item in result.get("Contents", [])]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[4]
    output = args.output.resolve()
    output.relative_to(root / "docs/project/evidence")
    if output.exists():
        raise ValueError("receipt_destination_must_be_new")
    sql = f"""SELECT json_build_object(
      'observed_at',clock_timestamp(),
      'program',(SELECT json_build_object('id',id,'state',state,'manifest_digest',manifest_digest)
                 FROM research_programs WHERE id='{PROGRAM}'),
      'latest_task',(SELECT row_to_json(x) FROM (
        SELECT id,state,proposal->>'title' AS title,data_assessment,decision,mission_id
        FROM research_program_tasks WHERE program_id='{PROGRAM}' ORDER BY created_at DESC LIMIT 1)x),
      'stages',(SELECT json_agg(x) FROM (
        SELECT id,stage,state,attempt,task_id,error,updated_at FROM research_mission_stages
        WHERE program_id='{PROGRAM}' ORDER BY updated_at DESC LIMIT 5)x),
      'program_task_counts',(SELECT json_object_agg(state,n) FROM (
        SELECT state,count(*) n FROM research_program_tasks WHERE program_id='{PROGRAM}' GROUP BY state)x),
      'mission_count',(SELECT count(*) FROM research_missions WHERE program_id='{PROGRAM}'));
    """
    remote = "sudo docker exec quant-company-postgres-1 psql -U postgres -d quant_company -At -v ON_ERROR_STOP=1 -c " + shlex.quote(sql)
    state = run_json(["ssh", "-i", "/Users/achii/.ssh/quant-company-lightsail-rsa",
                      "-o", "UserKnownHostsFile=" + str(root / ".local/account-deploy/known_hosts"),
                      "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=15",
                      "ubuntu@15.165.137.122", remote])
    if state["program"]["manifest_digest"] != DIGEST:
        raise ValueError("approved_program_changed")
    versioning = aws("get-bucket-versioning", "--bucket", BUCKET,
                     "--query", "{Status:Status,MFADelete:MFADelete}")
    versions = {}
    for name in ("krx_etf", "krx_etf_meta"):
        key = "qdata/clean/" + name + ".parquet"
        value = aws("list-object-versions", "--bucket", BUCKET, "--prefix", key, "--no-paginate")
        if value.get("IsTruncated"):
            raise ValueError("version_history_truncated:" + key)
        versions[key] = {"versions": [{name: item[name] for name in
                                      ("Key", "VersionId", "Size", "LastModified", "ETag")}
                                     for item in value.get("Versions", [])],
                         "delete_markers": value.get("DeleteMarkers", [])}
    qdata_prefixes = listed("qdata/", delimiter="/")
    backup_prefixes = listed("backups/", delimiter="/")
    public = {"schema_version": 1, "observed_at": datetime.now(UTC).isoformat(),
              "scope": "research_records_and_s3_metadata_read_only",
              "production": state,
              "source_bucket": BUCKET, "bucket_versioning": versioning,
              "source_object_versions": versions,
              "published_qdata_prefixes": qdata_prefixes,
              "bucket_backup_prefixes": backup_prefixes,
              "historical_price_bodies_read": False, "production_mutated": False,
              "strategy_performance_computed": False,
              "scope_limit": "This inventory does not prove no original copy exists on an uninspected host or archive."}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        stream.write(json.dumps(public, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"receipt": str(output.relative_to(root)),
                      "program_state": state["program"]["state"],
                      "latest_task_state": state["latest_task"]["state"],
                      "mission_count": state["mission_count"],
                      "version_counts": {key: len(value["versions"]) for key, value in versions.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
