"""Restore an empty, accidentally created calendar directory; preserve service specs.

Dry-run by default. Run as root on the host; only API and dispatch are recreated.
The empty directory is retained as a backup. No credentials are printed.
"""

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

STATE = Path("/var/lib/quant-company")
CALENDAR = STATE / "config/data-watch-contracts.json"
NAMES = ("quant-company-api-1", "quant-company-dispatch-1")


def run(args):
    value = subprocess.run(args, capture_output=True, timeout=180)
    if value.returncode:
        raise RuntimeError(f"{args[0]} failed with status {value.returncode}")
    return value.stdout.decode().strip()


def inventory():
    ids = run(["docker", "ps", "-aq"]).split()
    return {row["Name"].lstrip("/"): row for row in json.loads(run(["docker", "inspect", *ids]))
            if row["Name"].startswith("/quant-company-")}


def signature(row):
    return {"id": row["Id"], "image": row["Image"], "running": row["State"]["Running"],
            "oom": row["State"]["OOMKilled"], "restarts": row["RestartCount"]}


def verify(record, after, evidence):
    before = record["before"]
    for name in NAMES:
        row = after[name]
        if (not row["State"]["Running"] or row["State"]["OOMKilled"] or row["RestartCount"]
                or row["Image"] != before[name]["image"]):
            raise ValueError("recovered_service_changed_or_unhealthy")
        result = run(["docker", "exec", name, "python", "-c",
                      "import pathlib,json; p=pathlib.Path('/etc/quant-company/data-watch-contracts.json');"
                      "assert p.is_file() and json.loads(p.read_text())==[];print('valid')"])
        if result != "valid":
            raise ValueError("calendar_readback_failed")
    for name, row in before.items():
        if name not in NAMES and (after[name]["Id"] != row["id"] or after[name]["Image"] != row["image"]):
            raise ValueError("independent_service_replaced")
    record.update(state="recovered", completed_at=time.time(),
                  after={name: signature(row) for name, row in after.items()},
                  independent_service_ids_preserved=True)
    (evidence / "receipt.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"state": "recovered", "selected": list(NAMES),
                      "calendar": "unregistered_empty_array", "independent_service_ids_preserved": True}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--verify", action="store_true", help="Reconcile an already executed repair without repeating it")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError("root_required")
    os.umask(0o077)
    with (STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not args.verify and (CALENDAR.is_symlink() or not CALENDAR.is_dir() or any(CALENDAR.iterdir())):
            raise ValueError("calendar_must_be_the_confirmed_empty_directory")
        before = inventory()
        config_files = before[NAMES[0]]["Config"]["Labels"]["com.docker.compose.project.config_files"]
        if before[NAMES[1]]["Config"]["Labels"]["com.docker.compose.project.config_files"] != config_files:
            raise ValueError("selected_service_compose_configs_differ")
        command = ["docker", "compose", "--env-file", str(STATE / "config/runtime.env")]
        for file in config_files.split(","):
            command.extend(["-f", file])
        config = json.loads(run([*command, "config", "--format", "json"]))["services"]
        environment_digest = hashlib.sha256((STATE / "config/runtime.env").read_bytes()).hexdigest()
        for name in NAMES:
            row, service = before[name], config[name.removeprefix("quant-company-").removesuffix("-1")]
            expected_env = dict(item.split("=", 1) for item in row["Config"]["Env"])
            image = json.loads(run(["docker", "image", "inspect", service["image"]]))[0]
            if (image["Id"] != row["Image"]
                    or any(str(value) != expected_env.get(key) for key, value in service["environment"].items())
                    or service.get("command") != row["Config"]["Cmd"]
                    or service.get("user") != row["Config"]["User"]):
                raise ValueError("selected_service_spec_changed")
        sending = int(run(["docker", "exec", "-u", "postgres", "quant-company-postgres-1", "psql",
                           "-XAt", "-v", "ON_ERROR_STOP=1", "-d", "quant_company", "-c",
                           "SELECT count(*) FROM outbox WHERE status='sending'"]))
        if sending and not args.verify:
            raise ValueError("outbox_write_requires_reconciliation")
        evidence = STATE / "operations/quant-feed-structural-20260929/dispatch-recovery"
        if args.verify:
            record = json.loads((evidence / "receipt.json").read_text())
            if not CALENDAR.is_file() or json.loads(CALENDAR.read_text()) != []:
                raise ValueError("recovery_calendar_changed")
            verify(record, before, evidence)
            return
        record = {"state": "preflight_passed", "selected": list(NAMES), "calendar": "unregistered_empty_array",
                  "sending_outbox": sending, "before": {name: signature(row) for name, row in before.items()}}
        if not args.repair:
            print(json.dumps({key: value for key, value in record.items() if key != "before"}))
            return
        evidence.mkdir(mode=0o700)
        (evidence / "receipt.json").write_text(json.dumps(record, indent=2) + "\n")
        if hashlib.sha256((STATE / "config/runtime.env").read_bytes()).hexdigest() != environment_digest:
            raise ValueError("environment_changed_after_preflight")
        run(["docker", "stop", "--time", "60", *NAMES])
        CALENDAR.rename(evidence / "original-empty-directory")
        CALENDAR.write_text("[]\n")
        CALENDAR.chmod(0o444)
        # The old bind mount retains the directory inode; recreate both consumers.
        run([*command, "up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "120",
             "api", "dispatch"])
        time.sleep(10)
        after = inventory()
        for name in NAMES:
            if dict(item.split("=", 1) for item in after[name]["Config"]["Env"]) != dict(
                    item.split("=", 1) for item in before[name]["Config"]["Env"]):
                raise ValueError("recovered_environment_changed")
        verify(record, after, evidence)


if __name__ == "__main__":
    main()
