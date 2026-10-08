"""Bounded read-only observer for the already-approved programme's successor.

Records metadata locally; never invokes a model, sends Slack, changes company
records, retries a scientific job, or supplies a research decision.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", required=True)
    parser.add_argument("--helper-sha256", required=True)
    args = parser.parse_args()
    assert re.fullmatch(r"[0-9a-f]{40}", args.commit)
    assert re.fullmatch(r"[0-9a-f]{64}", args.helper_sha256)
    helper = Path(__file__).with_name("read-revision-loop-live-progress-20261008.py")
    assert hashlib.sha256(helper.read_bytes()).hexdigest() == args.helper_sha256
    os.umask(0o077)
    output = Path("/var/lib/quant-company/releases/revision-loop-monitor-20261008")
    output.mkdir(exist_ok=True, mode=0o700)
    until = time.monotonic() + 4 * 3600
    observations = 0
    errors = 0
    state = "runtime_limit_reached"
    while time.monotonic() < until:
        assert hashlib.sha256(helper.read_bytes()).hexdigest() == args.helper_sha256
        result = subprocess.run(["python3", str(helper), "--commit", args.commit],
                                capture_output=True, text=True, timeout=40)
        observations += 1
        if result.returncode:
            errors += 1
            value = {"observed_at": datetime.now(UTC).isoformat(), "read_failed": True,
                     "returncode": result.returncode, "database_mutated": False}
        else:
            value = json.loads(result.stdout)
        with (output / "observations.jsonl").open("a") as log:
            log.write(json.dumps(value, sort_keys=True) + "\n")
        temporary = output / "latest.tmp"
        temporary.write_text(json.dumps(value, indent=2) + "\n")
        os.replace(temporary, output / "latest.json")
        if sum(trial["state"] == "reported" for trial in value.get("trials", [])) >= 2:
            state = "second_reported_trial_observed"
            break
        time.sleep(min(45, max(0, until - time.monotonic())))
    (output / "completed.json").write_text(json.dumps({
        "state": state, "completed_at": datetime.now(UTC).isoformat(),
        "observations": observations, "read_errors": errors,
        "read_only": True, "automatic_alerts": False, "automatic_repairs": False,
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
