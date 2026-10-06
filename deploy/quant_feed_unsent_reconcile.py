#!/usr/bin/env python3
"""Persist before/after receipts for the reviewed, explicit seven-post repair."""

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

STATE = Path("/var/lib/quant-company")
OPERATION = STATE / "operations/quant-feed-parity-20261006"
IDENTITIES = (
    "f81016bb-f7ec-5d4f-b53c-f3c9a1219882", "94e0dac9-d677-5a11-aafb-f971c7990711",
    "bcb61b6f-1bf5-5308-8859-1aa70dc21f4e", "5770b8a4-050e-5753-9d23-d62cecefef25",
    "d4a8fb1e-b071-537f-9dc5-2e0c6a82b6df", "9e85fbec-71ae-5db4-b18b-d4d0f2d7941d",
    "5d5e81ce-b0c0-5013-b42e-902a73f47858",
)
OLD_POLICY = "0c3a76a09091720ad899d2b9c4d1aefe4aa6f36d58d0d47528076f034c38f5f6"


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("plan", "apply"))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--source-sha256", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError("root_required")
    os.umask(0o077)
    with (STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        source = args.source.read_bytes()
        if hashlib.sha256(source).hexdigest() != args.source_sha256:
            raise ValueError("reviewed_source_digest_mismatch")
        alignment = json.loads((OPERATION / "alignment.json").read_text())
        if alignment["state"] != "aligned":
            raise ValueError("worker_alignment_not_verified")
        path = OPERATION / (args.action + ".json")
        if path.exists():
            raise ValueError("existing_receipt_requires_reconciliation")
        command = ["docker", "exec", "-i", "quant-company-quant-feed-worker-1", "python",
                   "/app/entrypoint.py", "python", "-", "--old-policy", OLD_POLICY,
                   "--old-channels", json.dumps(alignment["old_channels"])]
        for identity in IDENTITIES:
            command.extend(["--id", identity])
        if args.action == "apply":
            planned = json.loads((OPERATION / "plan.json").read_text())
            if planned["state"] != "planned" or planned["operator_source_sha256"] != args.source_sha256:
                raise ValueError("reviewed_plan_missing")
            command.extend(["--apply-digest", planned["plan_digest"]])
            save(path, {"state": "apply_requested", "plan_digest": planned["plan_digest"],
                        "identities": IDENTITIES, "requested_at": datetime.now(UTC).isoformat()})
        result = subprocess.run(command, input=source, capture_output=True, check=False, timeout=120)
        if result.returncode:
            raise ValueError("container_reconciliation_failed:" + result.stderr.decode()[-1200:])
        receipt = json.loads(result.stdout)
        receipt.update(operator_source_sha256=args.source_sha256, checked_at=datetime.now(UTC).isoformat())
        save(path, receipt)
        print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
