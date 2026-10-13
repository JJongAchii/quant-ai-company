"""Run one scoped private control against the already-qualified staged runtime."""

import fcntl
import importlib.util
import json
import os
from pathlib import Path

OPERATION = Path("/var/lib/quant-company/operations/quant-feed-recovery-20261001")
SOURCE = Path("/opt/quant-company/releases/ea092f419b4b679df4c25e479a8cb361b4d046ff")
CLIENT = "quant-recovery-positive-accuracy-20261001"
RUNTIME = "quant-recovery-qualification-runtime-20261001"


def main():
    spec = importlib.util.spec_from_file_location(
        "private_quant_helper", SOURCE / "docs/project/evidence/quant-feed-structural-20260929/preview_overlay.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.OPERATION = OPERATION
    if Path("/opt/quant-company/current").resolve().name != "3c848af95dc33b7da444a55018fd41d374c10025":
        raise ValueError("production_baseline_changed")
    previous = json.loads((OPERATION / "private-receipts-prior-context/qualification.json").read_bytes())
    if (previous["state"] != "not_passed" or previous["error"] != "positive_critic_did_not_pass"
            or any(row["state"] != "returned" for row in previous["calls"])):
        raise ValueError("settled_duplicate_control_required")
    before = helper.inventory()
    row = helper.inspect("quant-company-quant-feed-worker-1")
    runtime = helper.inspect(RUNTIME)
    # Use the staged app image, not the credential-isolated model image.
    journal = json.loads(Path("/var/lib/quant-company/releases/quant-feed-ea092f419b4b679df4c25e479a8cb361b4d046ff.json").read_bytes())
    if not runtime["State"]["Running"] or runtime["Image"] != next(
            item["id"] for item in journal["images"] if item["target"] == "codex"):
        raise ValueError("matching_staged_runtime_required")
    row["Image"] = next(item["id"] for item in journal["images"] if item["target"] == "app")
    environment = helper.env(row)
    environment["MODEL_RUNTIME_URL"] = "http://" + RUNTIME + ":8080"
    receipts = OPERATION / "positive-control"
    receipts.mkdir(mode=0o700)
    os.chown(receipts, 10001, 10001)
    mounts = [*row["Mounts"], {"Type": "bind", "Source": str(receipts),
                              "Destination": "/qualification/receipts", "RW": True},
              {"Type": "bind", "Source": str(OPERATION / "check_positive_accuracy.py"),
               "Destination": "/qualification/control.py", "RW": False}]
    helper.create(CLIENT, row, SOURCE, environment, mounts, list(row["NetworkSettings"]["Networks"]),
                  ["python", "/qualification/control.py"], "384m")
    helper.run(["docker", "start", CLIENT])
    print(json.dumps({"state": "running", "slack_writes": 0}), flush=True)
    code = int(helper.run(["docker", "wait", CLIENT], timeout=1200))
    receipt = json.loads((receipts / "positive-accuracy.json").read_bytes())
    summary = {key: receipt.get(key) for key in ("state", "policy", "control", "document_writes", "slack_writes",
                                                "account_ledger_writes", "critic")}
    summary.update(returncode=code, production_services_preserved=helper.inventory() == before)
    (OPERATION / "POSITIVE-ACCURACY-SUMMARY.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)
    if code == 0 and receipt["state"] == "passed":
        helper.run(["docker", "stop", "--time", "30", RUNTIME])
        helper.run(["docker", "rm", CLIENT, "quant-recovery-qualification-client-20261001", RUNTIME])
    else:
        raise SystemExit(2)


if __name__ == "__main__":
    if os.geteuid() != 0:
        raise ValueError("root_required")
    with Path("/var/lib/quant-company/.backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        main()
