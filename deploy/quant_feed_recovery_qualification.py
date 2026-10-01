"""Qualify staged Quant images with private receipts; never replace production.

The temporary model runtime inherits only the existing Quant auth/jobs mounts,
shares its lane lock, and never receives database or Slack credentials.
"""

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

STATE = Path("/var/lib/quant-company")
CURRENT = Path("/opt/quant-company/current")
OPERATION = STATE / "operations/quant-feed-recovery-20261001"
RUNTIME = "quant-recovery-qualification-runtime-20261001"
CLIENT = "quant-recovery-qualification-client-20261001"


def load(path):
    spec = importlib.util.spec_from_file_location("quant_recovery_helper", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(args):
    if (os.geteuid() != 0 or not all(re.fullmatch(r"[a-f0-9]{40}", value) for value in (args.base, args.commit))
            or CURRENT.resolve().name != args.base):
        raise ValueError("exact_current_base_required")
    source = CURRENT.parent / "releases" / args.commit
    journal = json.loads((STATE / "releases" / ("quant-feed-" + args.commit + ".json")).read_text())
    if journal["phase"] != "staged" or journal["commit"] != args.commit:
        raise ValueError("verified_staged_images_required")
    images = {item["target"]: item["id"] for item in journal["images"]}
    release = load(source / "deploy/quant_feed_quality_release.py")
    release.protocol_preflight(images["app"], images["codex"])
    helper = load(source / "docs/project/evidence/quant-feed-structural-20260929/preview_overlay.py")
    helper.OPERATION = OPERATION
    if any(release.activity().values()):
        raise ValueError("drained_quant_lane_required")
    existing = helper.run(["docker", "ps", "-a", "--format", "{{.Names}}"])
    if RUNTIME in existing.splitlines() or CLIENT in existing.splitlines():
        raise ValueError("existing_private_containers_require_reconciliation")
    OPERATION.mkdir(mode=0o700, parents=True, exist_ok=True)
    receipts = OPERATION / "private-receipts"
    receipts.mkdir(mode=0o700)
    os.chown(receipts, 10001, 10001)
    before = helper.inventory()
    runtime = helper.inspect("quant-company-quant-codex-runtime-1")
    client = helper.inspect("quant-company-quant-feed-worker-1")
    runtime_env, client_env = helper.env(runtime), helper.env(client)
    allowed_runtime = {"/state/auth", "/state/backup-auth", "/state/jobs", "/run/secrets/model_runtime_token"}
    allowed_client = {"/etc/quant-company/roles.json", "/run/secrets/database_password",
                      "/run/secrets/model_runtime_token", "/run/secrets/temporal_api_key"}
    if ({item["Destination"] for item in runtime["Mounts"]} != allowed_runtime
            or not {item["Destination"] for item in client["Mounts"]} <= allowed_client
            or any(runtime_env.get(key) or client_env.get(key)
                   for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_ACCESS_TOKEN"))):
        raise ValueError("private_qualification_isolation_required")
    runtime["Image"], client["Image"] = images["codex"], images["app"]
    helper.create(RUNTIME, runtime, source, runtime_env, runtime["Mounts"],
                  list(runtime["NetworkSettings"]["Networks"]),
                  ["uvicorn", "quant_company.providers.codex_runtime:app", "--host", "0.0.0.0", "--port", "8080"], "1g")
    helper.run(["docker", "start", RUNTIME])
    # Docker's healthcheck runs in this temporary runtime. It has no DB or Slack access.
    helper.run(["docker", "exec", RUNTIME, "python", "-c",
                "import time,urllib.request\n"
                "for i in range(30):\n"
                " try: urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=2); break\n"
                " except Exception: time.sleep(1)\n"
                "else: raise SystemExit(2)"])
    client_env["MODEL_RUNTIME_URL"] = "http://" + RUNTIME + ":8080"
    mounts = [*client["Mounts"], {"Type": "bind", "Source": str(receipts),
                                "Destination": "/qualification/receipts", "RW": True}]
    helper.create(CLIENT, client, source, client_env, mounts, list(client["NetworkSettings"]["Networks"]),
                  ["python", "/qualification/source/scripts/qualify_quant_recovery.py", "--output",
                   "/qualification/receipts/qualification.json"], "384m")
    helper.run(["docker", "start", CLIENT])
    print(json.dumps({"state": "running", "production_services_replaced": 0, "slack_writes": 0}), flush=True)
    result = subprocess.run(["docker", "wait", CLIENT], capture_output=True, text=True, timeout=1800)
    if result.returncode:
        raise ValueError("private_client_wait_requires_reconciliation")
    output = receipts / "qualification.json"
    receipt = json.loads(output.read_bytes()) if output.is_file() else {"state": "missing"}
    preserved = helper.inventory() == before and CURRENT.resolve().name == args.base
    summary = {key: receipt.get(key) for key in ("state", "policy", "slack_writes", "document_writes", "account_ledger_writes", "cases",
                                                "stalled_input", "fault", "error")}
    summary.update(commit=args.commit, base=args.base, producer_consumer_matched=True,
                   production_services_preserved=preserved, returncode=int(result.stdout.strip()),
                   call_count=len(receipt.get("calls", [])),
                   private_receipt_sha256=hashlib.sha256(output.read_bytes()).hexdigest() if output.exists() else None)
    (OPERATION / "SUMMARY.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)
    # Never remove a runtime whose invocation may still be uncertain.
    if receipt["state"] == "passed" and int(result.stdout.strip()) == 0:
        helper.run(["docker", "stop", "--time", "30", RUNTIME])
        helper.run(["docker", "rm", CLIENT, RUNTIME])
    if receipt["state"] != "passed" or not preserved:
        raise SystemExit(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base")
    parser.add_argument("commit")
    args = parser.parse_args()
    os.umask(0o077)
    with (STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        main(args)
