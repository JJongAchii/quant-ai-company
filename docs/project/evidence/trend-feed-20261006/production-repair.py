"""Repair the observed news-worker PID limit and preserve raw Trend Scout message text."""

import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
import time
from pathlib import Path

STATE = Path("/var/lib/quant-company")
ROOT = Path("/opt/quant-company/operator-releases/trend-feed-20261006")
JOURNAL = STATE / "releases/trend-feed-20261006-repair.json"


def main():
    spec = importlib.util.spec_from_file_location("trend_operator", ROOT / "production-cutover.py")
    operator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(operator)
    spec = importlib.util.spec_from_file_location("trend_stage", ROOT / "production-stage.py")
    stage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stage)
    if JOURNAL.exists():
        raise RuntimeError("repair_journal_exists_requires_readback")
    cutover = json.loads(operator.JOURNAL.read_text())
    if cutover["state"] != "preview_active":
        raise RuntimeError("not_preview_active")
    before = operator.inspect()
    if any(before[n]["Id"] != cutover["target_ids"][n] for n in operator.TARGETS):
        raise RuntimeError("preview_cohort_changed")
    base = before["news-worker"]["Image"]
    if before["dispatch"]["Image"] != base:
        raise RuntimeError("consumer_images_differ")
    plan = json.loads((ROOT / "repair-plan.json").read_text())
    package = "/opt/company/src/quant_company"
    previous = stage.inventory(base, package)
    if previous["slack.py"] != plan["slack_before_sha256"]:
        raise RuntimeError("repair_source_changed")
    incoming = ROOT / "format-repair/overlay/quant_company/slack.py"
    if hashlib.sha256(incoming.read_bytes()).hexdigest() != plan["slack_after_sha256"]:
        raise RuntimeError("repair_overlay_mismatch")
    base_tag = "quant-company-trend-repair-base:" + base[7:]
    operator.run(["docker", "tag", base, base_tag])
    tag = "quant-company-trend:" + plan["commit"] + "-repair"
    operator.run(["docker", "build", "--network=none", "--pull=false", "--build-arg", "BASE_IMAGE=" + base_tag,
                  "--build-arg", "BASE_IMAGE_ID=" + base, "--build-arg", "PACKAGE_ROOT=" + package,
                  "--build-arg", "TREND_COMMIT=" + plan["commit"], "-t", tag,
                  "-f", str(ROOT / "Dockerfile.trend-feed"), str(ROOT / "format-repair")], timeout=300)
    built = json.loads(operator.run(["docker", "image", "inspect", tag]))[0]
    previous["slack.py"] = plan["slack_after_sha256"]
    if stage.inventory(built["Id"], package) != previous:
        raise RuntimeError("repair_inventory_mismatch")
    oldimage = json.loads(operator.run(["docker", "image", "inspect", base]))[0]
    if any(built["Config"][k] != oldimage["Config"][k] for k in ("Env", "Cmd", "Entrypoint", "User", "WorkingDir")):
        raise RuntimeError("repair_image_runtime_changed")
    record = {"state": "repairing", "started_at": datetime.datetime.now(datetime.UTC).isoformat(),
              "commit": plan["commit"], "base_image": base, "image_id": built["Id"], "tag": tag,
              "changed_files": ["slack.py"], "inventory_verified": True,
              "news_worker_pids_limit": {"before": 128, "after": 256}, "publish_enabled": False}
    operator.atomic(JOURNAL, json.dumps(record, indent=2).encode())
    oldpause = operator.sql("SELECT row_to_json(s) FROM (SELECT paused_until,reason FROM runtime_control WHERE id=1)s")
    operator.sql("WITH s AS (UPDATE runtime_control SET paused_until=now()+interval '25 minutes',"
                 "reason='deployment_trend_feed_resource_repair' WHERE id=1 RETURNING id) SELECT json_agg(id) FROM s")
    try:
        deadline = time.monotonic() + 1200
        while operator.lanes_busy(before):
            if time.monotonic() >= deadline:
                raise RuntimeError("model_lanes_not_drained")
            print(json.dumps({"state": "draining_for_repair"}), flush=True)
            time.sleep(10)
        requests = operator.frozen_requests()
        for name in operator.TARGETS:
            path = STATE / "config" / ("trend-feed-20261006-" + name + ".compose.json")
            operator.atomic(STATE / "releases" / ("trend-feed-repair-" + name + ".before.json"), path.read_bytes())
            override = json.loads(path.read_text())
            override["services"][name]["image"] = tag
            if name == "news-worker":
                override["services"][name]["pids_limit"] = 256
            operator.atomic(path, json.dumps(override).encode())
            command = operator.compose(before[name], path)
            operator.run([*command, "up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate",
                          "--wait", "--wait-timeout", "120", name], timeout=180)
            fresh = operator.inspect()[name]
            expected = operator.signature(before[name])
            if name == "news-worker":
                expected["host"]["PidsLimit"] = 256
            if fresh["Image"] != built["Id"] or operator.signature(fresh) != expected:
                raise RuntimeError("repair_runtime_drift")
            print(json.dumps({"state": "repaired", "service": name}), flush=True)
        fresh = operator.inspect()
        if any(fresh[n]["Id"] != r["Id"] for n, r in before.items() if n not in operator.TARGETS):
            raise RuntimeError("unrelated_container_replaced")
        after = operator.frozen_requests()
        if any(after[t].get(k) != v for t, rows in requests.items() for k, v in rows.items()):
            raise RuntimeError("frozen_request_changed")
        record.update(state="repaired", completed_at=datetime.datetime.now(datetime.UTC).isoformat(),
                      frozen_requests_preserved=True, unrelated_containers_preserved=True)
        operator.atomic(JOURNAL, json.dumps(record, indent=2).encode())
        cutover.update(commit=plan["commit"], resource_repair=str(JOURNAL),
                       target_ids={n: fresh[n]["Id"] for n in operator.TARGETS})
        operator.save(cutover)
        print(json.dumps(record))
    except BaseException as exc:
        record.update(state="reconciliation_required", error=type(exc).__name__ + ":" + str(exc))
        operator.atomic(JOURNAL, json.dumps(record, indent=2).encode())
        raise
    finally:
        operator.sql("WITH s AS (UPDATE runtime_control SET paused_until="
                     + ("'" + oldpause["paused_until"] + "'::timestamptz" if oldpause["paused_until"] else "NULL")
                     + ",reason=" + ("'" + oldpause["reason"].replace("'", "''") + "'" if oldpause["reason"] else "NULL")
                     + " WHERE id=1 RETURNING id) SELECT json_agg(id) FROM s")


if __name__ == "__main__":
    os.umask(0o077)
    with (STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        main()
