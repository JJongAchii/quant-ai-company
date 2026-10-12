"""Record the approved technical review and successor research progress for at most 24 hours, without effects."""

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
    helper = Path(__file__).with_name("read-technical-review-progress-20261012.py")
    os.umask(0o077)
    output = Path("/var/lib/quant-company/releases/technical-review-monitor-20261012")
    output.mkdir(exist_ok=True, mode=0o700)
    until = time.monotonic() + 24 * 3600
    observations = errors = 0
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
        cutover = value.get("cutover_completed_at")
        current = [stage for stage in value.get("stages", [])
                   if cutover and datetime.fromisoformat(stage["created_at"]) >= datetime.fromisoformat(cutover)]
        if any(stage.get("stage") == "technical_review" and stage.get("state") == "completed"
               and stage.get("decision") == "wait" for stage in current):
            state = "independent_director_wait_observed"
            break
        if any(stage.get("error") == "repair_requires_evidence" for stage in current):
            state = "successor_engineer_evidence_hold_observed"
            break
        if any(job["state"] == "uncertain" for job in value.get("jobs", [])):
            state = "uncertain_job_observed"
            break
        if sum(trial["state"] == "reported" for trial in value.get("trials", [])) >= 2:
            state = "second_reported_trial_observed"
            break
        time.sleep(min(45, max(0, until - time.monotonic())))
    (output / "completed.json").write_text(json.dumps({
        "state": state, "completed_at": datetime.now(UTC).isoformat(),
        "observations": observations, "read_errors": errors, "read_only": True,
        "maximum_hours": 24, "automatic_alerts": False, "automatic_repairs": False,
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
