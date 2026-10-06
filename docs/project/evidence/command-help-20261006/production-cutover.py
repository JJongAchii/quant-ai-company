"""One-shot help rollout; private receipts stay on the host. No Slack probe sends."""

import contextlib
import datetime
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import threading
import time
from types import SimpleNamespace

ROOT = pathlib.Path("/opt/quant-company/operator-releases/command-help-20261006")
STATE = pathlib.Path("/var/lib/quant-company")
JOURNAL = STATE / "releases/command-help-20261006-cutover.json"
PRIOR = pathlib.Path("/opt/quant-company/operator-releases/codex-upgrade-20261006/production-cutover.py")
PRIOR_SHA256 = "bc61547f1d79a3b6c21fa62c973fbbd5547aced9aaca005d4ec4cec7bb6985a3"


def main():
    if JOURNAL.exists():
        raise RuntimeError("cutover_journal_exists_requires_reconciliation")
    if hashlib.sha256(PRIOR.read_bytes()).hexdigest() != PRIOR_SHA256:
        raise RuntimeError("reviewed_operator_helpers_changed")
    spec = importlib.util.spec_from_file_location("reviewed_cutover_helpers", PRIOR)
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    helpers.ROOT, helpers.JOURNAL = ROOT, JOURNAL
    stage = json.loads((STATE / "releases/command-help-20261006-stage.json").read_text())
    if stage["state"] != "staged":
        raise RuntimeError("cohort_not_staged")
    run, sql, save = helpers.run, helpers.sql, helpers.save
    helpers.atomic(ROOT / "backup-empty.compose.json", b'{"services":{}}')
    names = run(["docker", "ps", "-a", "--format", "{{.Names}}"])
    before = helpers.inspect([n.removeprefix("quant-company-").removesuffix("-1")
                              for n in names.splitlines() if n.startswith("quant-company-") and n.endswith("-1")])
    targets = ["account-gateway", "api", "slack-socket"]
    if set(targets) != set(stage["targets"]):
        raise RuntimeError("unexpected_target_scope")
    if any(before[n]["Image"] != stage["base_image"] for n in targets):
        raise RuntimeError("qualified_source_cohort_changed")
    if any(not r["State"]["Running"] or r["State"]["OOMKilled"] for r in before.values()):
        raise RuntimeError("baseline_fleet_not_ready")
    if run(["systemctl", "show", "quant-company-release.service", "--property=ActiveState", "--value"]).strip() != "inactive":
        raise RuntimeError("release_executor_busy")
    current = pathlib.Path("/opt/quant-company/current").resolve()
    helpers.atomic(ROOT / "baseline.json", json.dumps(before).encode())
    old_pause = sql("SELECT row_to_json(s) FROM (SELECT paused_until,reason FROM runtime_control WHERE id=1)s")
    timer_active = run(["systemctl", "show", helpers.TIMER, "--property=ActiveState", "--value"]).strip() == "active"
    record = {"state": "draining", "commit": stage["commit"], "started_at": datetime.datetime.now(datetime.UTC).isoformat(),
              "target_names": targets, "replaced": [], "current": str(current), "baseline_images": {n: r["Image"] for n, r in before.items()},
              "baseline_container_ids": {n: r["Id"] for n, r in before.items()}, "old_pause": old_pause}
    save(record)
    stop = threading.Event()
    errors = []

    def pause():
        sql("WITH s AS (UPDATE runtime_control SET paused_until=now()+interval '60 seconds',"
            "reason='deployment_command_help' WHERE id=1 RETURNING id) SELECT json_agg(id) FROM s")

    def heartbeat():
        while not stop.wait(15):
            try:
                pause()
            except Exception as error:
                errors.append(type(error).__name__)
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    try:
        run(["systemctl", "stop", helpers.TIMER])
        pause()
        thread.start()
        run(["docker", "stop", "--time", "90", "quant-company-slack-socket-1"], timeout=120)
        deadline = time.monotonic() + 600
        while busy := helpers.lanes_busy(before):
            if time.monotonic() >= deadline or errors:
                raise RuntimeError("model_lanes_not_drained")
            print(json.dumps({"state": "draining", "busy": busy}), flush=True)
            time.sleep(10)
        run(["docker", "stop", "--time", "360", *[r["Id"] for n, r in before.items()
                                                    if n not in {"postgres", "slack-socket"}]], timeout=450)
        if helpers.lanes_busy(before) or errors:
            raise RuntimeError("model_lane_or_pause_raced_drain")
        record.update(state="backing_up", frozen_requests=helpers.requests(), receipt_inventory=helpers.receipt_inventory(before),
                      assignment_policy=sql("SELECT row_to_json(s) FROM (SELECT revision,bindings FROM model_assignment_policy WHERE id=1)s"),
                      account_policy=sql("SELECT row_to_json(s) FROM (SELECT profile,revision FROM model_account_policy WHERE id=1)s"))
        save(record)
        print(json.dumps({"state": "backing_up", "frozen_requests": sum(len(v) for v in record["frozen_requests"].values())}), flush=True)
        spec = importlib.util.spec_from_file_location("standard_state_backup", current / "deploy/state_backup.py")
        backup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backup)
        for key, value in backup.config_values(STATE / "config/backup.env").items():
            os.environ[key] = value
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            backup.backup(SimpleNamespace(env_file=helpers.ENV, s3_uri=None), backup.config_values(helpers.ENV),
                          helpers.compose(before["api"], ROOT / "backup-empty.compose.json"), STATE)
        record["backup"] = json.loads(output.getvalue().splitlines()[-1])
        save(record)
        for name in targets:
            if errors:
                raise RuntimeError("deployment_pause_heartbeat_failed")
            old = before[name]
            config = old["Config"]
            settings = {"image": stage["tag"], "environment": dict(v.split("=", 1) for v in config["Env"]),
                        "command": config["Cmd"], "entrypoint": config["Entrypoint"],
                        "volumes": [{"type": m["Type"], "source": m["Source"], "target": m["Destination"], "read_only": not m["RW"]}
                                    for m in old["Mounts"] if m["Type"] in {"bind", "volume"} and not m["Destination"].startswith("/run/secrets/")]}
            override = STATE / "config" / ("command-help-20261006-" + name + ".compose.json")
            helpers.atomic(override, json.dumps({"services": {name: settings}}).encode())
            command = helpers.compose(old, override)
            compiled = json.loads(run([*command, "config", "--format", "json"]))["services"][name]
            if compiled["image"] != stage["tag"] or compiled["environment"] != settings["environment"]:
                raise RuntimeError("compiled_cohort_environment_drift:" + name)
            run([*command, "up", "--no-start", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", name], timeout=180)
            fresh = helpers.inspect([name])[name]
            if fresh["Image"] != stage["image"] or helpers.signature(fresh) != helpers.signature(old):
                raise RuntimeError("cohort_runtime_boundary_changed:" + name)
            record["replaced"].append(name)
            save(record)
            print(json.dumps({"state": "created", "service": name, "configuration_preserved": True}), flush=True)
        fresh_requests = helpers.requests()
        if any(fresh_requests[table].get(k) != v for table, rows in record["frozen_requests"].items() for k, v in rows.items()):
            raise RuntimeError("frozen_request_changed")
        fresh = helpers.inspect(before)
        if any(fresh[n]["Id"] != before[n]["Id"] for n in before if n not in targets):
            raise RuntimeError("unrelated_service_replaced")
        if helpers.receipt_inventory(before) != record["receipt_inventory"]:
            raise RuntimeError("existing_receipt_or_session_changed")
        for key, query in (("assignment_policy", "SELECT row_to_json(s) FROM (SELECT revision,bindings FROM model_assignment_policy WHERE id=1)s"),
                           ("account_policy", "SELECT row_to_json(s) FROM (SELECT profile,revision FROM model_account_policy WHERE id=1)s")):
            if sql(query) != record[key]:
                raise RuntimeError("existing_policy_changed")
        run(["docker", "start", *[before[n]["Id"] for n in before if n not in {"postgres", *targets}]])
        run(["docker", "start", "quant-company-account-gateway-1", "quant-company-api-1"])
        stop.set()
        thread.join(timeout=30)
        if errors or thread.is_alive():
            raise RuntimeError("pause_heartbeat_unconfirmed")
        stamp = "'" + old_pause["paused_until"] + "'::timestamptz" if old_pause["paused_until"] else "NULL"
        reason = "'" + old_pause["reason"].replace("'", "''") + "'" if old_pause["reason"] else "NULL"
        sql("WITH s AS (UPDATE runtime_control SET paused_until=" + stamp + ",reason=" + reason + " WHERE id=1 RETURNING id) SELECT json_agg(id) FROM s")
        run(["docker", "start", "quant-company-slack-socket-1"])
        record.update(state="active", completed_at=datetime.datetime.now(datetime.UTC).isoformat(), frozen_requests_preserved=True,
                      existing_receipts_preserved=True, employee_assignments_preserved=True, account_policy_preserved=True,
                      non_target_containers_preserved=True, override_paths={n: str(STATE / "config" / ("command-help-20261006-" + n + ".compose.json")) for n in targets})
        save(record)
        print(json.dumps({k: v for k, v in record.items() if k not in {"frozen_requests", "receipt_inventory"}}), flush=True)
    except BaseException as error:
        record.update(state="reconciliation_required", error_type=type(error).__name__)
        save(record)
        print(json.dumps({"state": record["state"], "error_type": record["error_type"], "replaced": record["replaced"]}), flush=True)
        raise
    finally:
        stop.set()
        if thread.ident:
            thread.join(timeout=30)
        if timer_active:
            run(["systemctl", "start", helpers.TIMER])


if __name__ == "__main__":
    os.umask(0o077)
    with (STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        main()
