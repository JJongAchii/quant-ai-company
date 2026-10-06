#!/usr/bin/env python3
"""Recreate only the Quant worker with the dispatch allowlist; never change images."""

import fcntl
import importlib.util
import json
import os
import subprocess
from pathlib import Path

STATE = Path("/var/lib/quant-company")
WORKER = "quant-company-quant-feed-worker-1"
DISPATCH = "quant-company-dispatch-1"
OPERATION = STATE / "operations/quant-feed-parity-20261006"


def execute(command):
    result = subprocess.run(command, check=False, capture_output=True, timeout=400)
    if result.returncode:
        raise ValueError("operator_command_failed:" + command[0])
    return result.stdout.decode()


def load_helper():
    spec = importlib.util.spec_from_file_location("quant_quality_release",
                                                Path("/opt/quant-company/current/deploy/quant_feed_quality_release.py"))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def write_receipt(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def main():
    if os.geteuid() != 0:
        raise ValueError("root_required")
    os.umask(0o077)
    with (STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        helper = load_helper()
        before = helper.inventory()
        activity = helper.activity()
        if activity["running_quant_calls"] or activity["pending_quant_outbox"]:
            raise ValueError("quant_lane_must_be_drained")
        worker, dispatch = json.loads(execute(["docker", "inspect", WORKER, DISPATCH]))
        old = dict(item.split("=", 1) for item in worker["Config"]["Env"])
        expected = dict(item.split("=", 1) for item in dispatch["Config"]["Env"])
        channels = json.loads(old["SLACK_ALLOWED_CHANNELS"])
        target = json.loads(expected["SLACK_ALLOWED_CHANNELS"])
        if (target[:len(channels)] != channels or len(target) <= len(channels)
                or old["QUANT_FEED_CHANNEL_ID"] not in channels
                or not worker["State"]["Running"] or not dispatch["State"]["Running"]):
            raise ValueError("expected_monotonic_allowlist_change_missing")
        journal = OPERATION / "alignment.json"
        if journal.exists():
            raise ValueError("existing_operation_requires_reconciliation")
        OPERATION.mkdir(parents=True, exist_ok=True)
        replacement = {**old, "SLACK_ALLOWED_CHANNELS": expected["SLACK_ALLOWED_CHANNELS"]}
        overlay = STATE / "config/quant-parity-20261006.compose.json"
        if overlay.exists():
            raise ValueError("existing_overlay_requires_reconciliation")
        write_receipt(overlay, {"services": {"quant-feed-worker": {
            "image": worker["Image"], "environment": replacement}}})
        files = worker["Config"]["Labels"]["com.docker.compose.project.config_files"].split(",")
        if not files or any(not Path(path).is_file() for path in files):
            raise ValueError("recorded_compose_files_missing")
        command = ["docker", "compose", "--project-name", "quant-company", "--profile", "quant-feed",
                   "--env-file", str(STATE / "config/runtime.env")]
        for path in [*files, str(overlay)]:
            command.extend(["-f", path])
        record = {"state": "recreate_requested", "inventory_before": before,
                  "old_channels": channels, "target_channels": target,
                  "changed_environment_keys": ["SLACK_ALLOWED_CHANNELS"], "worker_image": worker["Image"],
                  "old_worker_id": worker["Id"], "model_calls": 0}
        write_receipt(journal, record)
        execute([*command, "up", "-d", "--no-deps", "--no-build", "--pull", "never", "quant-feed-worker"])
        after = helper.inventory()
        changed = json.loads(execute(["docker", "inspect", WORKER]))[0]
        actual = dict(item.split("=", 1) for item in changed["Config"]["Env"])
        if (set(before) != set(after) or not changed["State"]["Running"] or changed["Image"] != worker["Image"]
                or actual != replacement or changed["State"]["OOMKilled"]):
            raise ValueError("quant_worker_postcondition_failed")
        for name, row in before.items():
            if name != WORKER and any(after[name][key] != row[key]
                                      for key in ("id", "image_id", "running", "oom", "restarts")):
                raise ValueError("independent_service_changed:" + name)
        record.update(state="aligned", inventory_after=after, independent_services_preserved=True)
        write_receipt(journal, record)
        print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    main()
