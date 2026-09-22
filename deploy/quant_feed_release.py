#!/usr/bin/env python3
"""Explicit operator release: full images, preview first, preserve stopped services.

Run as root on the reviewed office host with stage/cutover, exact base/commit and
an archive whose SHA-256 was recorded locally. No credential or environment output.
This is not an autonomous maintenance policy and never starts the research worker.
"""

import argparse
import contextlib
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
import time
from pathlib import Path
from types import SimpleNamespace

STATE = Path("/var/lib/quant-company")
CURRENT = Path("/opt/quant-company/current")
APP_ID = "A0C3HFP831Q"
BOT_USER_ID = "U0C3288AXPH"
SERVICES = ("codex-runtime", "claude-runtime", "api", "news-worker", "dispatch", "slack-socket", "maintenance")
IMAGES = (("app", "quant-company"), ("autonomous-research", "quant-company-autonomous"),
          ("codex", "quant-company-codex"), ("maintenance", "quant-company-maintenance"),
          ("claude", "quant-company-claude"))


def run(command, **kwargs):
    return subprocess.run(command, check=True, capture_output=True, timeout=1800, **kwargs).stdout


def emit(**values):
    print(json.dumps(values), flush=True)


def helper(root):
    spec = importlib.util.spec_from_file_location("quant_release_helpers", root / "deploy/maintenance_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inspect(names):
    return json.loads(run(["docker", "inspect", *["quant-company-" + s + "-1" for s in names]]))


def active_services():
    rows = inspect(list(SERVICES))
    return [name for name, row in zip(SERVICES, rows, strict=True) if row["State"]["Running"]]


def worker_state():
    row = inspect(["worker"])[0]
    return {"id": row["Id"], "running": row["State"]["Running"],
            "oom_killed": row["State"]["OOMKilled"], "restarts": row["RestartCount"]}


def memory_available_mib():
    values = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    return int(values["MemAvailable"].split()[0]) // 1024


def health(commit, names, postgres_id, worker_before):
    rows = inspect(["postgres", "worker", *names])
    worker_after = {"id": rows[1]["Id"], "running": rows[1]["State"]["Running"],
                    "oom_killed": rows[1]["State"]["OOMKilled"], "restarts": rows[1]["RestartCount"]}
    if rows[0]["Id"] != postgres_id or worker_after != worker_before:
        raise ValueError("database_or_research_worker_changed")
    for row in [rows[0], *rows[2:]]:
        state = row["State"]
        if not state["Running"] or state["OOMKilled"] or state.get("Health", {}).get("Status", "healthy") != "healthy":
            raise ValueError("release_service_unhealthy")
        if row["Name"] != "/quant-company-postgres-1" and row["Config"]["Labels"].get("org.opencontainers.image.revision") != commit:
            raise ValueError("release_image_revision_mismatch")
    return {"running": names, "postgres_recreated": False, "research_worker_preserved": worker_after,
            "host_mem_available_mib": memory_available_mib()}


def setenv(module, updates):
    env = STATE / "config/runtime.env"
    lines = [line for line in env.read_text().splitlines() if line.split("=", 1)[0] not in updates]
    module.atomic(env, ("\n".join(lines + [key + "=" + value for key, value in updates.items()]) + "\n").encode())


def source_inventory(root):
    return {"quant_company/" + str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


def qdata_tree_digest(root):
    values = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    if any(p.is_symlink() for p in root.rglob("*")):
        raise ValueError("qdata_tree_symlink")
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def take_backup_preserving_worker(previous, env, module):
    """Use the qualified backup while excluding the independently owned research process."""
    spec = importlib.util.spec_from_file_location("quant_state_backup", previous / "deploy/state_backup.py")
    backup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backup)
    backup.APP_SERVICES = tuple(name for name in backup.APP_SERVICES if name != "worker")
    for key, value in backup.config_values(STATE / "config/backup.env").items():
        os.environ[key] = value
    backup.backup(SimpleNamespace(env_file=env, s3_uri=None), backup.config_values(env),
                  module.compose_command(previous), STATE)


def stage(args, previous, target, journal, module):
    builder = "quant-feed-" + args.base[:12]
    builder_reused = subprocess.run(["docker", "buildx", "inspect", builder], capture_output=True).returncode == 0
    required_disk = (4 if builder_reused else 10) * 1024**3
    required_memory = 512 if builder_reused else 640
    if target.exists() or memory_available_mib() < required_memory or shutil.disk_usage(target.parent).free < required_disk:
        raise ValueError("stage_target_exists_or_insufficient_headroom")
    archive = Path(args.archive)
    data = archive.read_bytes()
    if hashlib.sha256(data).hexdigest() != args.archive_sha256:
        raise ValueError("archive_digest_mismatch")
    record = {"phase": "staging", "commit": args.commit, "previous": str(previous),
              "archive_sha256": args.archive_sha256, "started_at": time.time(), "images": [],
              "running_before": active_services(),
              "build_method": "full images from committed Dockerfile, lock, package and deployment inputs",
              "worker_at_stage": worker_state(), "builder_memory_mib": 512, "builder_cpus": 1,
              "builder_cache_reused": builder_reused, "admission_memory_mib": required_memory}
    module.atomic(journal, json.dumps(record).encode())
    target.mkdir()
    module.unpack(data, target)
    pin = json.loads((target / "deploy/qdata-source.json").read_text())
    if pin != json.loads((previous / "deploy/qdata-source.json").read_text()):
        raise ValueError("qdata_pin_changed")
    if qdata_tree_digest(previous / "qdata") != args.qdata_tree_sha256:
        raise ValueError("qdata_tree_differs_from_exact_local_archive")
    shutil.copytree(previous / "qdata", target / "qdata")
    record["qdata_commit"] = pin["commit"]
    record["qdata_tree_sha256"] = args.qdata_tree_sha256
    expected = source_inventory(target / "src/quant_company")
    try:
        if not builder_reused:
            run(["docker", "buildx", "create", "--name", builder, "--driver", "docker-container",
                 "--driver-opt", "memory=512m,memory-swap=512m,cpu-period=100000,cpu-quota=100000,restart-policy=no"])
        for build_target, repository in IMAGES:
            emit(phase="building", target=build_target)
            tag = repository + ":" + args.commit
            command = ["docker", "buildx", "build", "--builder", builder, "--load", "--progress", "plain",
                       "--build-context", "qdata=" + str(target / "qdata"), "--build-arg", "RELEASE_COMMIT=" + args.commit,
                       "--build-arg", "QDATA_COMMIT=" + pin["commit"], "--target", build_target,
                       "-f", str(target / "deploy/Dockerfile"), "-t", tag, str(target)]
            log = STATE / "releases" / ("quant-build-" + args.commit + "-" + build_target + ".log")
            with log.open("wb") as output:
                subprocess.run(command, check=True, stdout=output, stderr=subprocess.STDOUT, timeout=1800)
            verify = (
                "import hashlib,importlib.util,json,pathlib;"
                "root=pathlib.Path(importlib.util.find_spec('quant_company').origin).parent;"
                "print(json.dumps({'quant_company/'+str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() "
                "for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}))"
            )
            actual = json.loads(run(["docker", "run", "--rm", "--network=none", "--memory=128m", "--cpus=0.5",
                                     "--entrypoint", "python", tag, "-c", verify]))
            if actual != expected:
                raise ValueError("installed_source_mismatch")
            image = json.loads(run(["docker", "image", "inspect", tag]))[0]
            record["images"].append({"target": build_target, "id": image["Id"], "source_matches_commit": True})
            module.atomic(journal, json.dumps(record).encode())
        record.update(phase="staged", staged_at=time.time())
    except Exception as exc:
        record.update(phase="stage_failed", error=type(exc).__name__)
        raise
    finally:
        # Keep the scoped build cache for diagnosis; release its memory/CPU.
        with contextlib.suppress(Exception):
            run(["docker", "buildx", "stop", builder])
        module.atomic(journal, json.dumps(record).encode())
    emit(**record)


def cutover(args, previous, target, journal, module):
    record = json.loads(journal.read_text())
    if record["phase"] != "staged" or record["previous"] != str(previous):
        raise ValueError("cutover_precondition")
    selected = active_services()
    if selected != record["running_before"] or memory_available_mib() < 384:
        raise ValueError("running_services_changed_or_memory_admission_failed")
    # The independently owned research task may legitimately start or stop between
    # image staging and cutover. Preserve its state as observed at this boundary.
    worker_at_cutover = worker_state()
    if not {"api", "dispatch", "news-worker", "codex-runtime", "slack-socket"} <= set(selected):
        raise ValueError("required_services_not_running")
    if not re.fullmatch(r"[CG][A-Z0-9]+", args.channel or ""):
        raise ValueError("quant_channel_required")
    credentials = json.loads((STATE / "secrets/slack-credentials.json").read_text())
    credential = credentials.get("quant_scout", {})
    if set(credential) != {"app_id", "bot_user_id", "bot_token"} or credential["app_id"] != APP_ID or credential["bot_user_id"] != BOT_USER_ID:
        raise ValueError("quant_credential_identity")
    env = STATE / "config/runtime.env"
    roles_file = STATE / "config/roles.json"
    oldenv, oldroles = env.read_bytes(), roles_file.read_bytes()
    values = dict(line.split("=", 1) for line in oldenv.decode().splitlines() if "=" in line and not line.startswith("#"))
    owners = json.loads(values["SLACK_ALLOWED_USERS"])
    channels = json.loads(values["SLACK_ALLOWED_CHANNELS"])
    if len(owners) != 1 or args.channel in {values.get("NEWS_CHANNEL_ID"), values.get("TECH_FEED_CHANNEL_ID")}:
        raise ValueError("owner_or_channel_ambiguous")
    roles = json.loads(oldroles)
    candidate = next(r for r in json.loads((target / "src/quant_company/roles.json").read_text()) if r["id"] == "quant_scout")
    if any(r["id"] == "quant_scout" for r in roles):
        raise ValueError("quant_role_already_configured")
    preserved = {name: (STATE / name).read_bytes() for name in (
        "secrets/slack-credentials.json", "config/research-profiles.json", "config/research-qlab.json")}
    rows = inspect(["postgres"])
    record.update(postgres_id=rows[0]["Id"], worker_at_cutover=worker_at_cutover,
                  phase="draining", drain_started_at=time.time())
    module.atomic(STATE / "releases" / ("quant-feed-" + args.commit + ".env"), oldenv)
    module.atomic(STATE / "releases" / ("quant-feed-" + args.commit + ".roles"), oldroles)
    module.atomic(journal, json.dumps(record).encode())
    lock_fds = []
    changed = False
    new_module = helper(target)

    def unlock():
        while lock_fds:
            os.close(lock_fds.pop())

    try:
        code = """import json
from quant_company.company import Company
from quant_company.config import Settings
c=Company(Settings())
with c.db.transaction() as x:
 print(json.dumps({
  'research_jobs':x.execute("SELECT count(*) AS n FROM research_jobs WHERE state IN ('claimed','running','cancel_requested')").fetchone()['n'],
  'sending_outbox':x.execute("SELECT count(*) AS n FROM outbox WHERE status='sending'").fetchone()['n']}))
"""
        activity = json.loads(run(["docker", "exec", "-i", "quant-company-dispatch-1", "python", "/app/entrypoint.py", "python", "-"], input=code.encode()))
        if any(activity.values()):
            raise ValueError("external_activity_not_drained")
        module.compose(previous, "stop", "slack-socket", "dispatch")
        deadline = time.monotonic() + 1000
        for relative in ("codex/jobs/.runtime.lock", "codex/jobs/.runtime-news.lock", "codex/jobs/.runtime-quant.lock", "claude/jobs/.runtime.lock"):
            path = STATE / relative
            if path.is_symlink():
                raise ValueError("model_lock_symlink")
            existed = path.exists()
            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            if not existed:
                os.fchown(fd, 10001, 10001)
            lock_fds.append(fd)
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ValueError("model_drain_timeout") from None
                    time.sleep(0.2)
        module.compose(previous, "stop", *selected)
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            take_backup_preserving_worker(previous, env, module)
        record["backup"] = json.loads(capture.getvalue().strip().splitlines()[-1])
        changed = True
        setenv(module, {"RELEASE_COMMIT": args.commit, "QDATA_COMMIT": record["qdata_commit"],
                        "QDATA_BUILD_CONTEXT": str(target / "qdata"), "QUANT_FEED_ENABLED": "true",
                        "QUANT_FEED_PUBLISH_ENABLED": "false", "QUANT_FEED_CHANNEL_ID": args.channel,
                        "QUANT_FEED_OWNER_USER": owners[0],
                        "SLACK_ALLOWED_CHANNELS": json.dumps(list(dict.fromkeys([*channels, args.channel])), separators=(",", ":"))})
        module.atomic(roles_file, json.dumps([*roles, candidate], ensure_ascii=False).encode())
        module.link(target)
        # Additive schema only; default constructor does not migrate implicitly.
        migration = "from quant_company.company import Company;from quant_company.config import Settings;c=Company(Settings());c.db.migrate()"
        new_module.compose(target, "run", "--rm", "--no-deps", "-T", "api", "python", "-c", migration)
        unlock()
        new_selected = [*selected, "quant-feed-worker"]
        new_module.compose(target, "up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "120", *new_selected)
        for _ in range(3):
            result = health(args.commit, new_selected, record["postgres_id"], record["worker_at_cutover"])
            time.sleep(5)
        if not all((STATE / name).read_bytes() == content for name, content in preserved.items()):
            raise ValueError("unrelated_config_changed")
        record.update(phase="preview_active", activated_at=time.time(), health=result,
                      publication_enabled=False, preserved_existing_roles=True, research_worker_untouched=True,
                      model_allowance_changed=False,
                      schema_change="additive quant tables only", uncertain_receipts_replayed=False)
        module.atomic(journal, json.dumps(record).encode())
        emit(**record)
    except BaseException as exc:
        unlock()
        if changed:
            with contextlib.suppress(Exception):
                new_module.compose(target, "stop", *selected, "quant-feed-worker")
            module.atomic(env, oldenv)
            module.atomic(roles_file, oldroles)
            module.link(previous)
        module.compose(previous, "up", "-d", "--no-deps", "--wait", "--wait-timeout", "120", *selected)
        record.update(phase="rolled_back", error=type(exc).__name__,
                      error_code=str(exc) if isinstance(exc, ValueError) else "release_command_failed",
                      health=health(previous.name, selected, record["postgres_id"], record["worker_at_cutover"]))
        module.atomic(journal, json.dumps(record).encode())
        emit(**record)
        raise
    finally:
        unlock()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["stage", "cutover"])
    parser.add_argument("base")
    parser.add_argument("commit")
    parser.add_argument("--archive")
    parser.add_argument("--archive-sha256")
    parser.add_argument("--qdata-tree-sha256")
    parser.add_argument("--channel")
    args = parser.parse_args()
    if not all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (args.base, args.commit)):
        raise ValueError("invalid_commit")
    os.umask(0o077)
    with (STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = CURRENT.resolve()
        if previous.name != args.base:
            raise ValueError("deployment_baseline_changed")
        target = CURRENT.parent / "releases" / args.commit
        journal = STATE / "releases" / ("quant-feed-" + args.commit + ".json")
        function = stage if args.action == "stage" else cutover
        module = helper(previous)
        try:
            function(args, previous, target, journal, module)
        except Exception as exc:
            # Pre-build validation failures happen before stage() enters its builder cleanup block.
            # Preserve an explicit terminal receipt instead of leaving an ambiguous staging record.
            if args.action == "stage" and journal.exists():
                record = json.loads(journal.read_text())
                if record.get("phase") == "staging":
                    record.update(phase="stage_failed", error=type(exc).__name__,
                                  error_code=str(exc) if isinstance(exc, ValueError) else "stage_command_failed")
                    module.atomic(journal, json.dumps(record).encode())
            raise


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        emit(ok=False, error=type(exc).__name__, code=str(exc) if isinstance(exc, ValueError) else "operator_command_failed")
        sys.exit(1)
