"""Collect bounded, secret-free 48-hour Quant deployment observations; never post."""

import argparse
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

CRITICAL = ("api", "dispatch", "quant-feed-worker", "quant-codex-runtime")
TIMER = "quant-feed-v20-observe.timer"


def atomic(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--init", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    if not args.directory.resolve().is_relative_to(Path("/var/lib/quant-company/operations")):
        raise ValueError("observation_requires_private_operation_path")
    if args.init:
        args.directory.mkdir(mode=0o700)
    baseline_path = args.directory / "baseline.json"
    observer = Path("/opt/quant-company/current/docs/project/evidence/quant-feed-structural-20260929/observe_runtime.py")
    result = subprocess.run(["python3", str(observer)], capture_output=True, timeout=40)
    now = datetime.now(UTC)
    if result.returncode or len(result.stdout) > 65536:
        snapshot = {"checked_at": now.isoformat(), "flags": ["observation_unavailable"]}
    else:
        snapshot = json.loads(result.stdout)
        snapshot["flags"] = []
        for service in CRITICAL:
            row = snapshot["services"]["/quant-company-" + service + "-1"]
            if not row["running"] or row["oom"] or row["restarts"]:
                snapshot["flags"].append(service + "_not_stable")
        if snapshot["quant_publication_enabled"] or snapshot["activity"]["publications"] != 1:
            snapshot["flags"].append("publication_state_changed")
        if snapshot["memory_kib"]["MemAvailable"] < 256 * 1024:
            snapshot["flags"].append("memory_below_admission")
    if args.init:
        if snapshot["flags"]:
            raise ValueError("observation_baseline_not_healthy")
        atomic(baseline_path, snapshot)
    baseline = json.loads(baseline_path.read_text())
    for service in CRITICAL:
        name = "/quant-company-" + service + "-1"
        if "services" in snapshot and snapshot["services"][name]["id"] != baseline["services"][name]["id"]:
            snapshot["flags"].append(service + "_container_changed")
    deadline = datetime.fromisoformat(baseline["checked_at"]) + timedelta(hours=48)
    samples = args.directory / "samples"
    samples.mkdir(mode=0o700, exist_ok=True)
    atomic(samples / (now.strftime("%Y%m%dT%H%M%S%fZ") + ".json"), snapshot)
    elapsed = now >= deadline
    atomic(args.directory / "latest.json", {"state": "window_elapsed" if elapsed else "observing",
                                            "deadline": deadline.isoformat(), "snapshot": snapshot})
    print(json.dumps({"state": "window_elapsed" if elapsed else "observing", "deadline": deadline.isoformat(),
                      "flags": snapshot["flags"], "slack_writes": 0}))
    if elapsed and not args.init:
        subprocess.run(["systemctl", "stop", TIMER], check=True, capture_output=True, timeout=10)


if __name__ == "__main__":
    main()
