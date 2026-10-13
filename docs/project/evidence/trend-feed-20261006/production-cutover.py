"""Receipted two-service preview cutover; credential values arrive only on stdin."""

import contextlib
import datetime
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

STATE = Path("/var/lib/quant-company")
ENV = STATE / "config/runtime.env"
ROOT = Path("/opt/quant-company/operator-releases/trend-feed-20261006")
STAGE = STATE / "releases/trend-feed-20261006-stage.json"
JOURNAL = STATE / "releases/trend-feed-20261006-cutover.json"
TARGETS = ("news-worker", "dispatch")
CHANNEL = "C0C6WTA9ECV"
OWNER = "U0C250E23NW"


def run(args, *, timeout=180, input=None):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, input=input)
    if result.returncode:
        # Command output can contain environment or credentials; retain it privately.
        private = STATE / "releases/trend-feed-command-error.txt"
        atomic(private, (result.stdout + result.stderr).encode())
        raise RuntimeError("operator_command_failed:" + args[0])
    return result.stdout


def atomic(path, content, *, mode=0o600, owner=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(mode)
    if owner:
        os.chown(temporary, *owner)
    temporary.replace(path)


def save(record):
    atomic(JOURNAL, json.dumps(record, indent=2).encode())


def sql(query):
    return json.loads(run(["docker", "exec", "-u", "postgres", "quant-company-postgres-1", "psql",
                           "-XAt", "-v", "ON_ERROR_STOP=1", "-d", "quant_company", "-c", query]))


def inspect():
    names = run(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=quant-company",
                 "--format", "{{.Names}}"]).splitlines()
    return {r["Name"].removeprefix("/quant-company-").removesuffix("-1"): r
            for r in json.loads(run(["docker", "inspect", *names]))}


def compose(row, override):
    files = row["Config"]["Labels"]["com.docker.compose.project.config_files"].split(",")
    command = ["docker", "compose", "--project-name", "quant-company", "--profile", "*", "--env-file", str(ENV)]
    for path in [*files, str(override)]:
        command.extend(["-f", path])
    return command


def signature(row, *, trend=False):
    config, host = row["Config"], row["HostConfig"]
    env = dict(v.split("=", 1) for v in config["Env"])
    if trend:
        env = {k: v for k, v in env.items() if not k.startswith("TREND_FEED_")}
        channels = json.loads(env["SLACK_ALLOWED_CHANNELS"])
        env["SLACK_ALLOWED_CHANNELS"] = json.dumps([c for c in channels if c != CHANNEL])
    elif "SLACK_ALLOWED_CHANNELS" in env:
        env["SLACK_ALLOWED_CHANNELS"] = json.dumps(json.loads(env["SLACK_ALLOWED_CHANNELS"]))
    return {"env": env, "config": {k: config[k] for k in ("Cmd", "Entrypoint", "User", "WorkingDir")},
            "host": {k: host.get(k) for k in ("NetworkMode", "ReadonlyRootfs", "CapDrop", "SecurityOpt", "Memory",
                     "MemorySwap", "NanoCpus", "PidsLimit", "RestartPolicy", "PortBindings", "Init", "Privileged")},
            "mounts": sorted((m["Type"], m["Source"], m["Destination"], m["RW"]) for m in row["Mounts"]
                             if not (trend and m["Destination"] == "/run/secrets/trend_naver_credentials"))}


def lanes_busy(rows):
    busy = []
    for name in ("codex-runtime", "quant-codex-runtime", "claude-runtime"):
        jobs = next(Path(m["Source"]) for m in rows[name]["Mounts"] if m["Destination"] == "/state/jobs")
        for path in jobs.glob(".runtime*.lock"):
            with path.open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    busy.append(name)
    return sorted(set(busy))


def frozen_requests():
    tables = sql("SELECT coalesce(json_agg(json_build_object('table',table_name,'column',column_name)),'[]') "
                 "FROM information_schema.columns WHERE table_schema='public' AND column_name "
                 "IN ('request','provider_request') AND data_type='jsonb'")
    result = {}
    for item in tables:
        table, column = item["table"], item["column"]
        if not all(re.fullmatch(r"[a-z][a-z0-9_]*", s) for s in (table, column)):
            raise RuntimeError("unrecognized_request_table")
        rows = sql(f"SELECT coalesce(json_agg(s),'[]') FROM (SELECT id::text,md5({column}::text) AS digest "
                   f"FROM {table} WHERE {column} IS NOT NULL ORDER BY id)s")
        result[table] = {r["id"]: r["digest"] for r in rows}
    return result


def install_credentials(payload, record):
    slack = payload["slack"]
    naver = payload["naver"]
    if (set(slack) != {"trend_scout"} or slack["trend_scout"].get("app_id") != "A0C7VRH8JV6"
            or slack["trend_scout"].get("bot_user_id") != "U0C7VT8TN1W"
            or not slack["trend_scout"].get("bot_token", "").startswith("xoxb-")
            or set(naver) != {"client_id", "client_secret"} or not all(naver.values())):
        raise RuntimeError("invalid_operator_credentials")
    secret = STATE / "secrets/slack-credentials.json"
    original = json.loads(secret.read_text())
    if "trend_scout" in original:
        raise RuntimeError("trend_slack_role_already_installed")
    metadata = secret.stat()
    atomic(STATE / "releases/trend-feed-20261006-slack.before.json", secret.read_bytes())
    updated = {**original, **slack}
    atomic(secret, (json.dumps(updated, indent=2) + "\n").encode(), mode=metadata.st_mode & 0o777,
           owner=(metadata.st_uid, metadata.st_gid))
    if any(json.loads(secret.read_text())[k] != v for k, v in original.items()):
        raise RuntimeError("existing_slack_credentials_changed")
    target = STATE / "secrets/trend-naver-credentials.json"
    if target.exists():
        raise RuntimeError("naver_secret_already_exists")
    atomic(target, (json.dumps(naver) + "\n").encode(), mode=0o400, owner=(10001, 10001))
    record.update(credentials_installed=True, existing_slack_roles_preserved=True,
                  naver_secret_readers=["news-worker"], credentials_in_git=False)
    save(record)


def cutover(payload):
    if JOURNAL.exists():
        raise RuntimeError("cutover_journal_exists_requires_readback")
    stage = json.loads(STAGE.read_text())
    before = inspect()
    current = Path(stage["current"])
    if (stage["state"] != "staged" or str(Path("/opt/quant-company/current").resolve()) != str(current)
            or hashlib.sha256(ENV.read_bytes()).hexdigest() != stage["runtime_env_sha256"]
            or any(before[n]["Id"] != stage["targets"]["/quant-company-" + n + "-1"]["id"]
                   or before[n]["Image"] not in stage["images"] for n in TARGETS)
            or not all(r["State"]["Running"] for r in before.values())
            or run(["systemctl", "show", "quant-company-release.service", "-p", "ActiveState", "--value"]).strip() != "inactive"):
        raise RuntimeError("production_baseline_changed")
    if shutil.disk_usage(STATE).free < 3 * 1024**3:
        raise RuntimeError("insufficient_backup_headroom")
    if sql("SELECT count(*) FROM outbox WHERE status IN ('pending','sending')"):
        raise RuntimeError("outbox_not_quiet")
    oldenv = ENV.read_bytes()
    oldpause = sql("SELECT row_to_json(s) FROM (SELECT paused_until,reason FROM runtime_control WHERE id=1)s")
    record = {"state": "draining", "commit": stage["commit"], "current": str(current),
              "started_at": datetime.datetime.now(datetime.UTC).isoformat(), "replaced": [],
              "authorized_scope": "preview, one connection message, conditional daily publication after three real preview days",
              "publish_enabled": False, "messages_sent": 0, "old_pause": oldpause}
    atomic(ROOT / "live-before.json", json.dumps(before).encode())
    for name in ("runtime.env", "roles.json"):
        atomic(STATE / "releases" / ("trend-feed-20261006-" + name + ".before"), (STATE / "config" / name).read_bytes())
    save(record)
    active_timers = [t for t in ("quant-company-release.timer", "quant-company-backup.timer")
                     if run(["systemctl", "show", t, "-p", "ActiveState", "--value"]).strip() == "active"]
    stop = threading.Event()
    errors = []
    thread = None
    stopped = []
    restored_pause = False

    def pause():
        sql("WITH s AS (UPDATE runtime_control SET paused_until=now()+interval '90 seconds',"
            "reason='deployment_trend_feed' WHERE id=1 RETURNING id) SELECT json_agg(id) FROM s")

    def heartbeat():
        while not stop.wait(15):
            try:
                pause()
            except Exception:
                errors.append("pause_heartbeat_failed")
                return

    try:
        for timer in active_timers:
            run(["systemctl", "stop", timer])
        pause()
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        run(["docker", "stop", "--time", "360", "quant-company-slack-socket-1"], timeout=400)
        stopped.append("slack-socket")
        deadline = time.monotonic() + 1200
        while busy := lanes_busy(before):
            if time.monotonic() >= deadline:
                raise RuntimeError("model_lanes_not_drained")
            print(json.dumps({"state": "draining", "busy_lanes": busy}), flush=True)
            time.sleep(10)
        writers = [n for n in before if n not in {"postgres", "slack-socket"}]
        run(["docker", "stop", "--time", "360", *["quant-company-" + n + "-1" for n in writers]], timeout=450)
        stopped.extend(writers)
        if lanes_busy(before):
            raise RuntimeError("model_lane_raced_drain")
        record["frozen_requests"] = frozen_requests()
        record["state"] = "backing_up"
        save(record)
        print(json.dumps({"state": "backing_up"}), flush=True)
        spec = importlib.util.spec_from_file_location("trend_backup", current / "deploy/state_backup.py")
        backup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backup)
        for key, value in backup.config_values(STATE / "config/backup.env").items():
            os.environ[key] = value
        empty = ROOT / "backup-empty.compose.json"
        atomic(empty, b'{"services":{}}')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            backup.backup(SimpleNamespace(env_file=ENV, s3_uri=None), backup.config_values(ENV),
                          compose(before["api"], empty), STATE)
        record["backup"] = json.loads(output.getvalue().splitlines()[-1])
        save(record)
        install_credentials(payload, record)
        roles_file = STATE / "config/roles.json"
        roles = json.loads(roles_file.read_text())
        original_roles = json.loads(json.dumps(roles))
        incoming_role = json.loads((ROOT / "trend-role.json").read_text())
        if incoming_role["id"] != "trend_scout" or incoming_role["active"] or incoming_role["tools"] or incoming_role["can_delegate_to"]:
            raise RuntimeError("trend_role_not_outbound_only")
        if any(r["id"] == "trend_scout" for r in roles):
            raise RuntimeError("trend_role_already_exists")
        roles.append(incoming_role)
        metadata = roles_file.stat()
        atomic(roles_file, (json.dumps(roles, indent=2) + "\n").encode(), mode=metadata.st_mode & 0o777,
               owner=(metadata.st_uid, metadata.st_gid))
        if json.loads(roles_file.read_text())[:-1] != original_roles:
            raise RuntimeError("existing_role_changed")
        schema = (ROOT / "trend_schema.sql").read_text()
        run(["docker", "exec", "-i", "-u", "postgres", "quant-company-postgres-1", "psql", "-X", "-1",
             "-v", "ON_ERROR_STOP=1", "-d", "quant_company", "-f", "-"], input="SET ROLE company;\n" + schema)
        cfg = backup.config_values(ENV)
        channels = json.loads(cfg["SLACK_ALLOWED_CHANNELS"])
        if CHANNEL in channels or cfg["SLACK_TEAM_ID"] != "T0C1YRDRPNF" or OWNER not in json.loads(cfg["SLACK_ALLOWED_USERS"]):
            raise RuntimeError("unexpected_workspace_scope")
        flags = {"TREND_FEED_ENABLED": "true", "TREND_FEED_PUBLISH_ENABLED": "false", "TREND_FEED_CHANNEL_ID": CHANNEL,
                 "TREND_FEED_OWNER_USER": OWNER, "TREND_FEED_NAVER_ENABLED": "true"}
        values = {**flags, "SLACK_ALLOWED_CHANNELS": json.dumps([*channels, CHANNEL])}
        lines = [line for line in oldenv.decode().splitlines() if line.split("=", 1)[0] not in values]
        atomic(ENV, ("\n".join([*lines, *[k + "=" + v for k, v in values.items()]]) + "\n").encode())
        record["state"] = "cutover"
        save(record)
        for name in TARGETS:
            if errors:
                raise RuntimeError("deployment_pause_heartbeat_failed")
            old = before[name]
            image = stage["images"][old["Image"]]
            env = dict(v.split("=", 1) for v in old["Config"]["Env"])
            env.update(values)
            service = {"image": image["tag"], "environment": env, "command": old["Config"]["Cmd"],
                       "entrypoint": old["Config"]["Entrypoint"], "user": old["Config"]["User"],
                       "working_dir": old["Config"]["WorkingDir"]}
            override = {"services": {name: service}}
            if name == "news-worker":
                env["TREND_FEED_NAVER_CREDENTIALS_FILE"] = "/run/secrets/trend_naver_credentials"
                service["secrets"] = ["trend_naver_credentials"]
                override["secrets"] = {"trend_naver_credentials": {"file": str(STATE / "secrets/trend-naver-credentials.json")}}
            path = STATE / "config" / ("trend-feed-20261006-" + name + ".compose.json")
            atomic(path, json.dumps(override).encode())
            command = compose(old, path)
            compiled = json.loads(run([*command, "config", "--format", "json"]))["services"][name]
            if compiled["image"] != image["tag"] or compiled["environment"] != env:
                raise RuntimeError("compiled_environment_drift:" + name)
            run([*command, "up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate",
                 "--wait", "--wait-timeout", "120", name], timeout=180)
            fresh = inspect()[name]
            if (fresh["Image"] != image["id"] or not fresh["State"]["Running"] or fresh["State"]["OOMKilled"]
                    or signature(fresh, trend=True) != signature(old)):
                raise RuntimeError("runtime_boundary_changed:" + name)
            record["replaced"].append(name)
            save(record)
            print(json.dumps({"state": "cutover", "service": name, "existing_runtime_preserved": True}), flush=True)
        unchanged = [n for n in stopped if n not in TARGETS]
        run(["docker", "start", *["quant-company-" + n + "-1" for n in unchanged]], timeout=180)
        stopped.clear()
        fresh = inspect()
        if any(fresh[n]["Id"] != r["Id"] or signature(fresh[n]) != signature(r)
               for n, r in before.items() if n not in TARGETS):
            raise RuntimeError("unrelated_container_changed")
        if not all(r["State"]["Running"] for r in fresh.values()):
            raise RuntimeError("fleet_not_running")
        after_requests = frozen_requests()
        if any(after_requests[t].get(k) != v for t, rows in record["frozen_requests"].items() for k, v in rows.items()):
            raise RuntimeError("frozen_model_request_changed")
        stop.set()
        thread.join(timeout=20)
        if errors or thread.is_alive():
            raise RuntimeError("pause_heartbeat_unconfirmed")
        sql("WITH s AS (UPDATE runtime_control SET paused_until="
            + ("'" + oldpause["paused_until"] + "'::timestamptz" if oldpause["paused_until"] else "NULL")
            + ",reason=" + ("'" + oldpause["reason"].replace("'", "''") + "'" if oldpause["reason"] else "NULL")
            + " WHERE id=1 RETURNING id) SELECT json_agg(id) FROM s")
        restored_pause = True
        record.update(state="preview_active", completed_at=datetime.datetime.now(datetime.UTC).isoformat(),
                      frozen_requests_preserved=True, unrelated_containers_preserved=True,
                      existing_roles_preserved=True, runtime_env_sha256=hashlib.sha256(ENV.read_bytes()).hexdigest(),
                      target_ids={n: fresh[n]["Id"] for n in TARGETS}, timers_restored=active_timers)
        save(record)
        print(json.dumps({k: v for k, v in record.items() if k != "frozen_requests"}), flush=True)
    except BaseException as exc:
        record.update(state="reconciliation_required", error=type(exc).__name__ + ":" + str(exc),
                      runtime_pause_restored=restored_pause)
        save(record)
        print(json.dumps({"state": record["state"], "error": record["error"], "replaced": record["replaced"]}), flush=True)
        raise
    finally:
        stop.set()
        if thread:
            thread.join(timeout=20)
        # Preserve container identities. Never replay model requests or Slack effects on error.
        for name in stopped:
            if name not in record["replaced"]:
                run(["docker", "start", "quant-company-" + name + "-1"])
        for timer in active_timers:
            run(["systemctl", "start", timer])


if __name__ == "__main__":
    os.umask(0o077)
    payload = json.load(sys.stdin)
    with (STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cutover(payload)
