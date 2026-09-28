#!/usr/bin/env python3
"""Scoped Quant editorial release; keep publication off and preserve other workers."""

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

STATE = Path("/var/lib/quant-company")
CURRENT = Path("/opt/quant-company/current")
SELECTED = ("api", "dispatch", "quant-feed-worker")


def run(command, **kwargs):
    result = subprocess.run(command, capture_output=True, check=False, timeout=1800, **kwargs)
    if result.returncode:
        raise RuntimeError(f"{command[0]} failed with exit {result.returncode}")
    return result.stdout.decode().strip()


def module(path):
    spec = importlib.util.spec_from_file_location("reviewed_release", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def inventory():
    ids = run(["docker", "ps", "-aq"]).split()
    rows = json.loads(run(["docker", "inspect", *ids])) if ids else []
    return {
        row["Name"].lstrip("/"): {
            "id": row["Id"],
            "image_id": row["Image"],
            "running": row["State"]["Running"],
            "oom": row["State"]["OOMKilled"],
            "restarts": row["RestartCount"],
            "health": row["State"].get("Health", {}).get("Status"),
            "image_revision": row["Config"].get("Labels", {}).get("org.opencontainers.image.revision"),
            "quant_publish": next(
                (
                    item.split("=", 1)[1]
                    for item in row["Config"]["Env"]
                    if item.startswith("QUANT_FEED_PUBLISH_ENABLED=")
                ),
                None,
            ),
        }
        for row in rows
    }


def activity():
    # The API and dispatch are stopped during cutover. Query the independent
    # database container so the second drain check is still executable.
    query = """SELECT json_build_object(
      'running_quant_calls', (SELECT count(*) FROM quant_feed_calls WHERE state='running'),
      'pending_quant_outbox', (SELECT count(*) FROM quant_feed_publications p
          JOIN outbox o ON o.id=p.id WHERE o.status IN ('pending','sending')),
      'sending_outbox', (SELECT count(*) FROM outbox WHERE status='sending'))::text"""
    return json.loads(
        run(
            [
                "docker",
                "exec",
                "-u",
                "postgres",
                "quant-company-postgres-1",
                "psql",
                "-X",
                "-A",
                "-t",
                "-v",
                "ON_ERROR_STOP=1",
                "-d",
                "quant_company",
                "-c",
                query,
            ]
        )
    )


def compose(helper, root, *args):
    overlay = root / "deploy/data-watch.compose.yaml"
    if not overlay.is_file():
        raise ValueError("data_watch_overlay_missing")
    return run([*helper.compose_command(root), "--profile", "data-watch", "-f", str(overlay), *args])


def check(before, commit, app_image_id):
    after = inventory()
    if set(after) != set(before):
        raise ValueError("container_inventory_changed")
    selected = {"quant-company-" + name + "-1" for name in SELECTED}
    for name, expected in before.items():
        row = after[name]
        if name in selected:
            if (
                not row["running"]
                or row["oom"]
                or row["health"] not in (None, "healthy")
                or row["image_revision"] != commit
                or row["image_id"] != app_image_id
                or row["quant_publish"] != "false"
            ):
                raise ValueError("quant_release_service_unhealthy")
        elif {key: row[key] for key in ("id", "image_id", "running", "oom", "restarts")} != {
            key: expected[key] for key in ("id", "image_id", "running", "oom", "restarts")
        }:
            raise ValueError("independent_service_changed")
    return {
        "selected": list(SELECTED),
        "independent_service_ids_preserved": True,
        "publication_enabled": False,
    }


def cutover(args, previous, target, journal, helper):
    record = json.loads(journal.read_text())
    if (
        record.get("phase") != "staged"
        or record.get("commit") != args.commit
        or record.get("previous") != str(previous)
        or not target.is_dir()
    ):
        raise ValueError("quality_release_not_staged")
    app_images = [image["id"] for image in record["images"] if image["target"] == "app"]
    if len(app_images) != 1:
        raise ValueError("quality_release_app_image_missing")
    env = STATE / "config/runtime.env"
    original = env.read_bytes()
    values = dict(
        line.split("=", 1)
        for line in original.decode().splitlines()
        if "=" in line and not line.startswith("#")
    )
    if (
        values.get("RELEASE_COMMIT") != args.base
        or values.get("QUANT_FEED_ENABLED") != "true"
        or values.get("QUANT_FEED_PUBLISH_ENABLED") != "false"
    ):
        raise ValueError("quality_release_environment_changed")
    before = inventory()
    selected = {"quant-company-" + name + "-1" for name in SELECTED}
    if any(not before[name]["running"] or before[name]["oom"] for name in selected):
        raise ValueError("quality_release_service_not_running")
    if any(activity().values()):
        raise ValueError("quality_release_activity_not_drained")
    # Stop only the Quant consumer first. Its durable workflow may start a final
    # activity between the first idle check and shutdown; inspect it again.
    run(["docker", "stop", "--time", "900", "quant-company-quant-feed-worker-1"])
    try:
        if any(activity().values()):
            raise ValueError("quality_release_activity_started_during_stop")
    except BaseException:
        run(["docker", "start", "quant-company-quant-feed-worker-1"])
        raise
    old_env_path = journal.with_suffix(".env")
    helper.atomic(old_env_path, original)
    record.update(
        phase="cutover_started",
        cutover_started_at=time.time(),
        original_env_sha256=hashlib.sha256(original).hexdigest(),
        independent_before={key: value for key, value in before.items() if key not in selected},
    )
    helper.atomic(journal, json.dumps(record).encode())
    changed = False
    try:
        run(["docker", "stop", "--time", "360", "quant-company-dispatch-1", "quant-company-api-1"])
        if any(activity().values()):
            raise ValueError("quality_release_activity_after_dispatch_stop")
        release = module(previous / "deploy/quant_feed_release.py")
        release.setenv(
            helper,
            {
                "RELEASE_COMMIT": args.commit,
                "QDATA_COMMIT": record["qdata_commit"],
                "QDATA_BUILD_CONTEXT": str(target / "qdata"),
            },
        )
        changed = True
        helper.link(target)
        compose(
            helper,
            target,
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "--wait",
            "--wait-timeout",
            "120",
            *SELECTED,
        )
        result = check(before, args.commit, app_images[0])
        for _ in range(2):
            time.sleep(5)
            result = check(before, args.commit, app_images[0])
        if any(activity()[key] for key in ("pending_quant_outbox", "sending_outbox")):
            raise ValueError("quality_release_unexpected_outbox_activity")
        record.update(
            phase="preview_active",
            cutover_completed_at=time.time(),
            result=result,
            original_roles_and_credentials_preserved=True,
        )
        helper.atomic(journal, json.dumps(record).encode())
        print(json.dumps({"phase": record["phase"], "commit": args.commit, **result}), flush=True)
    except BaseException as exc:
        if changed:
            helper.atomic(env, original)
            helper.link(previous)
        compose(
            helper,
            previous,
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "--wait",
            "--wait-timeout",
            "120",
            *SELECTED,
        )
        record.update(phase="rolled_back", error=type(exc).__name__)
        helper.atomic(journal, json.dumps(record).encode())
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stage", "cutover"))
    parser.add_argument("base")
    parser.add_argument("commit")
    parser.add_argument("--archive")
    parser.add_argument("--archive-sha256")
    parser.add_argument("--qdata-tree-sha256")
    args = parser.parse_args()
    if not all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (args.base, args.commit)):
        raise ValueError("invalid_release_commit")
    if os.geteuid() != 0:
        raise ValueError("root_required")
    os.umask(0o077)
    with (STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = CURRENT.resolve()
        if previous.name != args.base:
            raise ValueError("production_baseline_changed")
        target = CURRENT.parent / "releases" / args.commit
        journal = STATE / "releases" / ("quant-feed-" + args.commit + ".json")
        helper = module(previous / "deploy/maintenance_release.py")
        if args.action == "stage":
            if not args.archive or not args.archive_sha256 or not args.qdata_tree_sha256 or journal.exists():
                raise ValueError("quality_stage_requires_new_exact_inputs")
            module(previous / "deploy/quant_feed_release.py").stage(args, previous, target, journal, helper)
        else:
            cutover(args, previous, target, journal, helper)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": type(exc).__name__,
                    "code": str(exc) if isinstance(exc, ValueError) else "release_command_failed",
                }
            ),
            flush=True,
        )
        sys.exit(1)
