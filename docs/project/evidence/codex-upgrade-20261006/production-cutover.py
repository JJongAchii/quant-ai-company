"""Root-owned runtime-only upgrade. Existing requests, receipts and employee models are preserved."""

import contextlib
import datetime
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import subprocess
import tarfile
import threading
import time
from types import SimpleNamespace

STATE = pathlib.Path("/var/lib/quant-company")
ENV = STATE / "config/runtime.env"
ROOT = pathlib.Path("/opt/quant-company/operator-releases/codex-upgrade-20261006")
STAGE = STATE / "releases/codex-upgrade-20261006-stage.json"
JOURNAL = STATE / "releases/codex-upgrade-20261006-cutover.json"
TIMER = "quant-company-release.timer"


def run(args, *, timeout=180, input=None):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout, input=input)
    if result.returncode:
        raise RuntimeError("cutover_command_failed:" + args[0])
    return result.stdout


def sql(query):
    return json.loads(run(["docker", "exec", "-u", "postgres", "quant-company-postgres-1", "psql", "-XAt",
                           "-v", "ON_ERROR_STOP=1", "-d", "quant_company", "-c", query]))


def atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.chmod(0o600)
    temporary.replace(path)


def save(record):
    atomic(JOURNAL, json.dumps(record, indent=2).encode())


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for part in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def inspect(names):
    return {r["Name"].removeprefix("/quant-company-").removesuffix("-1"): r
            for r in json.loads(run(["docker", "inspect", *["quant-company-" + n + "-1" for n in names]]))}


def compose(row, override):
    files = row["Config"]["Labels"]["com.docker.compose.project.config_files"].split(",")
    command = ["docker", "compose", "--project-name", "quant-company", "--profile", "*", "--env-file", str(ENV)]
    for path in [*files, str(override)]:
        command.extend(["-f", path])
    return command


def signature(row, *, new=False):
    config, host = row["Config"], row["HostConfig"]
    env = dict(v.split("=", 1) for v in config["Env"])
    if new:
        env.pop("MODEL_ASSIGNMENTS_ENABLED", None)
        env.pop("MODEL_ASSIGNMENTS_COMMIT", None)
    return {"env": env, "config": {k: config[k] for k in ("Cmd", "Entrypoint", "User", "WorkingDir")},
            "host": {k: host.get(k) for k in ("NetworkMode", "ReadonlyRootfs", "CapDrop", "SecurityOpt", "Memory",
                     "MemorySwap", "NanoCpus", "PidsLimit", "RestartPolicy", "PortBindings", "Init", "Privileged")},
            "mounts": sorted((m["Type"], m["Source"], m["Destination"], m["RW"]) for m in row["Mounts"])}


def lanes_busy(rows):
    busy = []
    for name in ("codex-runtime", "quant-codex-runtime", "claude-runtime"):
        row = rows[name]
        jobs = next(pathlib.Path(m["Source"]) for m in row["Mounts"] if m["Destination"] == "/state/jobs")
        for path in jobs.glob(".runtime*.lock"):
            with path.open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    busy.append(name + ":" + path.name)
    return busy


def requests():
    tables = sql("SELECT json_agg(json_build_object('table',table_name,'column',column_name)) FROM information_schema.columns "
                 "WHERE table_schema='public' AND column_name IN ('request','provider_request') AND data_type='jsonb'")
    result = {}
    for item in tables:
        table, column = item["table"], item["column"]
        if not all(re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in (table, column)):
            raise RuntimeError("unrecognized_request_table")
        rows = sql(f"SELECT coalesce(json_agg(s),'[]') FROM (SELECT id::text,md5({column}::text) AS digest "
                   f"FROM {table} WHERE {column} IS NOT NULL ORDER BY id)s")
        result[table] = {r["id"]: r["digest"] for r in rows}
    return result


def receipt_inventory(rows):
    roots = {m["Source"] for row in rows.values() if "runtime" in row["Name"]
             for m in row["Mounts"] if m["Destination"] == "/state/jobs"}
    return {root: {str(p.relative_to(pathlib.Path(root))): sha256(p)
                  for p in pathlib.Path(root).rglob("*") if p.is_file() and p.suffix == ".json"}
            for root in sorted(roots)}


def cutover():
    if JOURNAL.exists():
        raise RuntimeError("cutover_journal_exists_requires_reconciliation")
    stage = json.loads(STAGE.read_text())
    if stage["state"] != "staged":
        raise RuntimeError("cohort_not_staged")
    for case in ("legacy_seed", "legacy_resume", "structured", "native_quant", "web_quant"):
        proof = json.loads((STATE / "releases" / ("codex-upgrade-20261006-qualification-" + case + ".json")).read_text())
        if proof["state"] != "passed" or (case != "legacy_seed" and proof["cli_version"] != "0.160.1"):
            raise RuntimeError("actual_account_qualification_incomplete")
    release = json.loads((ROOT / "codex-runtime" / "codex_release.json").read_text())
    cli_version = release["version"]
    if cli_version != "0.160.1":
        raise RuntimeError("qualified_release_version_changed")
    commit = stage["commit"]
    # Another receipted deployment may have changed unrelated service cohorts while
    # this CLI candidate was tested. Take a fresh full baseline under the host lock.
    names = run(["docker", "ps", "-a", "--format", "{{.Names}}"])
    names = [n.removeprefix("quant-company-").removesuffix("-1") for n in names.splitlines()
             if n.startswith("quant-company-") and n.endswith("-1")]
    before = inspect(names)
    current = pathlib.Path("/opt/quant-company/current").resolve()
    original = {r["Name"].removeprefix("/quant-company-").removesuffix("-1"): r for r in stage["targets"]}
    for name in ("codex-runtime", "quant-codex-runtime"):
        if before[name]["Id"] != original[name]["Id"] or before[name]["Image"] != original[name]["Image"]:
            raise RuntimeError("qualified_runtime_baseline_changed")
        if signature(before[name]) != signature(original[name]):
            raise RuntimeError("qualified_runtime_configuration_changed")
    if any(not r["State"]["Running"] or r["State"]["OOMKilled"] for r in before.values()):
        raise RuntimeError("baseline_fleet_not_ready")
    if run(["systemctl", "show", "quant-company-release.service", "--property=ActiveState", "--value"]).strip() != "inactive":
        raise RuntimeError("release_executor_busy")
    selected = sql("SELECT row_to_json(s) FROM (SELECT profile,revision FROM model_account_policy WHERE id=1)s")
    for case in ("legacy_seed", "legacy_resume", "structured", "native_quant", "web_quant"):
        proof = json.loads((STATE / "releases" / ("codex-upgrade-20261006-qualification-" + case + ".json")).read_text())
        name = "quant-codex-runtime" if case in {"native_quant", "web_quant"} else "codex-runtime"
        expected = before[name]["Image"] if case == "legacy_seed" else stage["images"][before[name]["Image"]]["id"]
        if proof["account"] != selected or proof["image"] != expected:
            raise RuntimeError("qualification_image_or_selected_account_changed")
    atomic(STATE / "releases/codex-upgrade-20261006-cutover-baseline.json", json.dumps(before, indent=2).encode())
    oldenv = ENV.read_bytes()
    oldpause = sql("SELECT row_to_json(s) FROM (SELECT paused_until,reason FROM runtime_control WHERE id=1)s")
    targets = sorted({s for image in stage["images"].values() for s in image["services"]})
    if targets != ["codex-runtime", "quant-codex-runtime"]:
        raise RuntimeError("runtime_only_scope_required")
    writers = [n for n, row in before.items() if n != "postgres" and row["State"]["Running"]]
    record = {"state": "draining", "commit": commit, "current": str(current),
              "started_at": datetime.datetime.now(datetime.UTC).isoformat(), "replaced": [],
              "target_names": targets, "old_pause": oldpause,
              "baseline_environment_sha256": hashlib.sha256(oldenv).hexdigest(),
              "baseline_container_ids": {n: r["Id"] for n, r in before.items()},
              "baseline_images": {n: r["Image"] for n, r in before.items()}}
    atomic(STATE / "releases/codex-upgrade-20261006.env", oldenv)
    save(record)
    paused_timer = False
    pause_stop = threading.Event()
    pause_errors = []
    pause_thread = None

    def hold_pause():
        while not pause_stop.wait(15):
            try:
                sql("WITH changed AS (UPDATE runtime_control SET paused_until=now()+interval '60 seconds',"
                    "reason='deployment_codex_upgrade' WHERE id=1 RETURNING id) SELECT json_agg(id) FROM changed")
            except Exception as exc:
                pause_errors.append(type(exc).__name__)
                return

    try:
        run(["systemctl", "stop", TIMER])
        paused_timer = True
        sql("WITH changed AS (UPDATE runtime_control SET paused_until=now()+interval '60 seconds',"
            "reason='deployment_codex_upgrade' WHERE id=1 RETURNING id) SELECT json_agg(id) FROM changed")
        pause_thread = threading.Thread(target=hold_pause, daemon=True)
        pause_thread.start()
        run(["docker", "stop", "--time", "360", "quant-company-slack-socket-1"], timeout=400)
        deadline = time.monotonic() + 1200
        while busy := lanes_busy(before):
            if time.monotonic() >= deadline:
                raise RuntimeError("model_lanes_not_drained")
            print(json.dumps({"state": "draining", "busy": busy}), flush=True)
            time.sleep(10)
        run(["docker", "stop", "--time", "360", *["quant-company-" + n + "-1" for n in writers
                                                    if n != "slack-socket"]], timeout=450)
        if lanes_busy(before):
            raise RuntimeError("model_lane_raced_drain")
        record["frozen_requests"] = requests()
        record["assignment_policy"] = sql("SELECT row_to_json(s) FROM (SELECT revision,bindings FROM model_assignment_policy WHERE id=1)s")
        record["receipt_inventory"] = receipt_inventory(before)
        record["account_policy"] = sql("SELECT row_to_json(s) FROM (SELECT profile,revision FROM model_account_policy WHERE id=1)s")
        record["state"] = "backing_up"
        save(record)
        print(json.dumps({"state": "backing_up", "frozen_request_counts": {k: len(v) for k, v in record["frozen_requests"].items()}}), flush=True)
        spec = importlib.util.spec_from_file_location("cohort_backup", current / "deploy/state_backup.py")
        backup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backup)
        for key, value in backup.config_values(STATE / "config/backup.env").items():
            os.environ[key] = value
        cfg = backup.config_values(ENV)
        # All writers are already stopped. Calling the standard backup directly avoids
        # taking our deployment lock again and does not restart an old writer.
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            backup.backup(SimpleNamespace(env_file=ENV, s3_uri=None), cfg,
                          compose(before["api"], ROOT / "backup-empty.compose.json"), STATE)
        record["backup"] = json.loads(output.getvalue().splitlines()[-1])
        # The separate Quant runtime has its own receipt tree. Preserve it without auth.
        quant_jobs = next(pathlib.Path(m["Source"]) for m in before["quant-codex-runtime"]["Mounts"]
                          if m["Destination"] == "/state/jobs")
        if quant_jobs.resolve() == (STATE / "codex/jobs").resolve():
            record["quant_receipt_backup"] = {"included_in_standard_backup": True, "shared_jobs_path": str(quant_jobs)}
        else:
            supplement = STATE / "backups/codex-upgrade-20261006-quant-receipts.tar.gz"
            if any(p.is_symlink() for p in quant_jobs.rglob("*")):
                raise RuntimeError("unsafe_quant_receipt_tree")
            with tarfile.open(supplement, "x:gz") as bundle:
                bundle.add(quant_jobs, arcname="quant-jobs")
            destination = record["backup"]["s3_uri"].rsplit("/", 1)[0] + "/" + supplement.name
            run(["aws", "s3", "cp", str(supplement), destination, "--sse", "AES256", "--only-show-errors"], timeout=600)
            record["quant_receipt_backup"] = {"path": str(supplement), "s3_uri": destination,
                                               "sha256": sha256(supplement)}
        save(record)
        values = {"PINNED_CODEX_RUNTIME_IMAGE": stage["images"][before["codex-runtime"]["Image"]]["tag"]}
        lines = [line for line in oldenv.decode().splitlines() if line.split("=", 1)[0] not in values]
        atomic(ENV, ("\n".join([*lines, *[k + "=" + v for k, v in values.items()]]) + "\n").encode())
        dropin = pathlib.Path("/etc/systemd/system/quant-company-release.service.d/model-assignments.conf")
        if not dropin.is_file():
            raise RuntimeError("mixed_fleet_release_guard_missing")
        record["release_guard_preserved"] = str(dropin)
        record["state"] = "cutover"
        save(record)
        order = targets
        for name in order:
            if pause_errors:
                raise RuntimeError("deployment_pause_heartbeat_failed")
            old = before[name]
            image = stage["images"][old["Image"]]
            env = dict(item.split("=", 1) for item in old["Config"]["Env"])
            settings = {"image": image["tag"], "environment": env, "command": old["Config"]["Cmd"],
                        "entrypoint": old["Config"]["Entrypoint"], "user": old["Config"]["User"],
                        "working_dir": old["Config"]["WorkingDir"],
                        "volumes": [{"type": m["Type"], "source": m.get("Name", m["Source"]) if m["Type"] == "volume" else m["Source"],
                                     "target": m["Destination"], "read_only": not m["RW"]} for m in old["Mounts"]
                                    if m["Type"] in {"bind", "volume"} and not m["Destination"].startswith("/run/secrets/")]}
            override = STATE / "config" / ("codex-upgrade-20261006-" + name + ".compose.json")
            atomic(override, json.dumps({"services": {name: settings}}).encode())
            command = compose(old, override)
            compiled = json.loads(run([*command, "config", "--format", "json"]))["services"][name]
            if compiled["image"] != image["tag"] or compiled["environment"] != env:
                raise RuntimeError("compiled_cohort_environment_drift:" + name)
            run([*command, "up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate",
                 "--wait", "--wait-timeout", "120", name], timeout=180)
            fresh = inspect([name])[name]
            if (fresh["Image"] != image["id"] or not fresh["State"]["Running"] or fresh["State"]["OOMKilled"]
                    or signature(fresh) != signature(old)):
                raise RuntimeError("cohort_runtime_boundary_changed:" + name)
            record["replaced"].append(name)
            save(record)
            print(json.dumps({"state": "cutover", "service": name, "runtime_preserved": True}), flush=True)
        final_requests = requests()
        if any(final_requests[table].get(k) != v for table, rows in record["frozen_requests"].items() for k, v in rows.items()):
            raise RuntimeError("frozen_request_changed")
        final = inspect(before)
        if any(final[n]["Id"] != before[n]["Id"] for n in before if n not in targets):
            raise RuntimeError("unrelated_database_or_reviewer_replaced")
        if receipt_inventory(before) != record["receipt_inventory"]:
            raise RuntimeError("existing_receipt_or_session_changed")
        if sql("SELECT row_to_json(s) FROM (SELECT profile,revision FROM model_account_policy WHERE id=1)s") != record["account_policy"]:
            raise RuntimeError("account_policy_changed")
        if sql("SELECT row_to_json(s) FROM (SELECT revision,bindings FROM model_assignment_policy WHERE id=1)s") != record["assignment_policy"]:
            raise RuntimeError("employee_assignment_changed")
        runtime_manifest = {"schema_version": 1, "cli_version": cli_version, "implementation_commit": commit,
            "runtimes": {n: {"image": stage["images"][before[n]["Image"]]["id"],
                "tag": stage["images"][before[n]["Image"]]["tag"],
                "base_image": before[n]["Image"], "package_root": stage["images"][before[n]["Image"]]["package_root"],
                "override": str(STATE / "config" / ("codex-upgrade-20261006-" + n + ".compose.json"))} for n in targets}}
        atomic(STATE / "config/codex-runtime-release.json", json.dumps(runtime_manifest, indent=2).encode())
        for n in targets:
            observed = run(["docker", "exec", "quant-company-" + n + "-1", "codex", "--version"]).strip()
            if observed != "codex-cli " + runtime_manifest["cli_version"]:
                raise RuntimeError("live_cli_version_mismatch")
        # Keep the original IDs/configuration for all unaffected services.
        run(["docker", "start", *[before[n]["Id"] for n in writers if n not in targets and n != "slack-socket"]])
        run(["docker", "start", before["slack-socket"]["Id"]])
        pause_stop.set()
        pause_thread.join(timeout=60)
        if pause_thread.is_alive() or pause_errors:
            raise RuntimeError("deployment_pause_heartbeat_unconfirmed")
        sql("WITH changed AS (UPDATE runtime_control SET paused_until="
            + ("'" + oldpause["paused_until"] + "'::timestamptz" if oldpause["paused_until"] else "NULL")
            + ",reason=" + ("'" + oldpause["reason"].replace("'", "''") + "'" if oldpause["reason"] else "NULL")
            + " WHERE id=1 RETURNING id) SELECT json_agg(id) FROM changed")
        record.update(state="active", cli_version=cli_version, completed_at=datetime.datetime.now(datetime.UTC).isoformat(),
                      frozen_requests_preserved=True, database_and_claude_preserved=True,
                      existing_roles_and_research_artifacts_preserved=True,
                      employee_assignments_preserved=True, existing_receipts_preserved=True)
        save(record)
        print(json.dumps({k: v for k, v in record.items() if k not in {"frozen_requests", "receipt_inventory"}}), flush=True)
    except BaseException as exc:
        record.update(state="reconciliation_required", error=type(exc).__name__ + ":" + str(exc))
        save(record)
        # Do not guess after a partial cutover, rerun requests, or discard receipts.
        print(json.dumps({"state": record["state"], "error": record["error"], "replaced": record["replaced"]}), flush=True)
        raise
    finally:
        pause_stop.set()
        if pause_thread:
            pause_thread.join(timeout=60)
        if paused_timer:
            run(["systemctl", "start", TIMER])


if __name__ == "__main__":
    os.umask(0o077)
    atomic(ROOT / "backup-empty.compose.json", b'{"services":{}}')
    with (STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cutover()
