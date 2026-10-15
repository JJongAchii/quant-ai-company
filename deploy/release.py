#!/usr/bin/env python3
"""Run every service from one reviewed main commit (root on the host, standard library only).

    release.py prepare  --sha SHA --archive FILE   unpack a `git archive` of SHA into /opt/quant-company/releases/SHA
    release.py build    --sha SHA                  build every image of that release (tagged with SHA)
    release.py plan     --sha SHA [SERVICE ...]    what each service would change; nothing is touched
    release.py migrate  --sha SHA                  one schema migration from the release image
    release.py apply    --sha SHA SERVICE ...      recreate services one at a time; roll a failed one back
    release.py verify   [--sha SHA]                every running service: commit, rendered config, package files
    release.py activate --sha SHA                  after all services run SHA: runtime.env pins and the current link
    release.py auto [--retry]                      the timer: compare production with CI-green main and release it
                                                   when allowed (RELEASE_AUTO_ENABLED=true); otherwise only report

Every service is rendered from deploy/release.json of the release, runtime.env, and the release variables below.
A service is recreated only when it is quiet, must settle on the release image with the planned config and the
release's exact package files, and is otherwise recreated from the compose set recorded on its old container.
"""

import argparse
import ast
import base64
import contextlib
import fcntl
import fnmatch
import hashlib
import http.client
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

STATE = Path(os.environ.get("QC_STATE_DIR", "/var/lib/quant-company"))
RELEASES = Path(os.environ.get("QC_RELEASES_DIR", "/opt/quant-company/releases"))
PROJECT = "quant-company"
SHA = re.compile(r"[0-9a-f]{40}")
KEEP = {"postgres", "caddy"}  # data and edge containers are never recreated by a code release
INCLUDE = ("deploy/", "src/", "pyproject.toml", "uv.lock", ".dockerignore")
REQUIRED = ("deploy/release.json", "deploy/compose.yaml", "deploy/qdata-source.json", "pyproject.toml", "uv.lock",
            "src/quant_company/__init__.py")
# Terminal states come from video/store.py and briefing/store.py.
VIDEO_DONE = ("published", "expired", "held", "superseded", "blocked", "delivered", "delivery_failed",
              "delivery_uncertain")
VIDEO_IDLE = VIDEO_DONE + ("awaiting_approval",)  # files ready, waiting on a person rather than the worker
BRIEF_ACTIVE = ("planning", "inventorying", "writing", "reviewing", "revising", "final_reviewing")


class ReleaseError(RuntimeError):
    pass


class Busy(ReleaseError):
    """Production work or another host operation is in the way; nothing is wrong with the release."""


class NeedsOperator(ReleaseError):
    """The release needs a host step this tool does not take (quant-data, host values, new or removed services)."""


def run(args, env=None, input=None, check=True, timeout=1800):
    result = subprocess.run(args, capture_output=True, text=True, env=env, input=input, timeout=timeout)
    if check and result.returncode:
        raise ReleaseError(f"{args[0]} {' '.join(args[1:4])}… failed: {(result.stderr or result.stdout)[-600:]}")
    return result.stdout


def envfile():
    return STATE / "config/runtime.env"


def root_of(sha):
    if not SHA.fullmatch(sha or ""):
        raise ReleaseError("release commit must be a full 40-character SHA")
    return RELEASES / sha


def manifest(root):
    value = json.loads((root / "deploy/release.json").read_text())
    if value.get("schema_version") != 1:
        raise ReleaseError("unsupported deploy/release.json")
    return value


def release_env(root, sha):
    """Variables that make every image of this release resolve to SHA (they override runtime.env)."""
    qdata = json.loads((root / "deploy/qdata-source.json").read_text())["commit"]
    return {"RELEASE_COMMIT": sha, "QDATA_COMMIT": qdata, "QDATA_BUILD_CONTEXT": str(root / "qdata"),
            # The worker runs this release's code; research jobs keep binding to RESEARCH_RELEASE_COMMIT.
            "PINNED_COMPANY_WORKER_IMAGE": f"quant-company-autonomous:{sha}",
            "PINNED_CODEX_RUNTIME_IMAGE": ""}


def compose_args(root):
    args = ["docker", "compose", "-p", PROJECT, "--project-directory", str(root / "deploy"),
            "--env-file", str(envfile()), "--profile", "*"]
    for name in manifest(root)["compose_files"]:
        path = root / "deploy" / name
        if not path.is_file():
            raise ReleaseError(f"release compose file missing: {name}")
        args += ["-f", str(path)]
    return args


def compose(root, sha, *args, runner=run, **kwargs):
    return runner(compose_args(root) + list(args), env={**os.environ, **release_env(root, sha)}, **kwargs)


def inspect(service, runner=run):
    out = runner(["docker", "inspect", f"{PROJECT}-{service}-1"], check=False)
    rows = json.loads(out or "[]")
    return rows[0] if rows else None


def recorded_args(info):
    labels = info["Config"]["Labels"]
    args = ["docker", "compose", "-p", PROJECT, "--project-directory", labels["com.docker.compose.project.working_dir"],
            "--env-file", labels.get("com.docker.compose.project.environment_file", str(envfile())), "--profile", "*"]
    for path in labels["com.docker.compose.project.config_files"].split(","):
        args += ["-f", path]
    return args


def config_hash(args, service, runner=run, env=None):
    out = runner(args + ["config", "--hash", service], env=env).split()
    return out[-1] if out else None


def config_hashes(args, runner=run, env=None):
    return dict(line.split()[:2] for line in runner(args + ["config", "--hash", "*"], env=env).splitlines()
                if len(line.split()) >= 2)


def label(info, key):
    return (info or {}).get("Config", {}).get("Labels", {}).get(key)


@contextlib.contextmanager
def host_lock(wait=30, sleep=time.sleep):
    """Same lock as backups and Maintainer releases: one host-changing operation at a time. A holder that lets go within
    WAIT seconds (another tool only checking the lock) is waited for."""
    path = STATE / ".backup.lock"
    with path.open("a") as handle:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Busy("another backup or release holds the host lock") from None
                sleep(1)
        yield


# ---------------------------------------------------------------------------------------------------------- prepare

def archive_commit(data):
    """`git archive` stores the commit in the pax global header; refuse anything else."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as bundle:
        return bundle.pax_headers.get("comment")


def unpack(data, target):
    """Only what images are built from (INCLUDE): a GitHub tarball carries the whole repository."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as bundle:
        members = bundle.getmembers()
        if bad := [m.name for m in members if m.name.startswith("/") or ".." in PurePosixPath(m.name).parts]:
            raise ReleaseError(f"unsafe archive member: {bad[0]}")
        prefix = {PurePosixPath(m.name).parts[0] for m in members if m.isfile()}
        strip = 1 if len(prefix) == 1 and not any(m.name in REQUIRED for m in members) else 0
        kept = [(m, PurePosixPath(m.name).parts[strip:]) for m in members]
        kept = [(m, parts) for m, parts in kept if parts and "/".join(parts).startswith(INCLUDE)]
        if bad := [m.name for m, _ in kept if not (m.isdir() or m.isfile())]:
            raise ReleaseError(f"unsafe archive member: {bad[0]}")
        if sum(m.size for m, _ in kept) > 512 * 1024 * 1024:
            raise ReleaseError("release archive expands beyond 512 MiB")
        for item, parts in kept:
            path = target.joinpath(*parts)
            if item.isdir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(item) as source, path.open("xb") as output:
                shutil.copyfileobj(source, output)
            path.chmod(0o755 if item.mode & 0o111 else 0o644)
    for path in [target, *target.rglob("*")]:
        if path.is_dir():
            path.chmod(0o755)


def qdata_source(pin):
    """A prepared release that already carries the same quant-data commit (the build context of the qdata extra)."""
    current = RELEASES.parent / "current"
    candidates = ([current.resolve()] if current.exists() else []) + sorted(RELEASES.iterdir(), reverse=True)
    for release in candidates:
        source = release / "deploy/qdata-source.json"
        if (release / "qdata").is_dir() and source.is_file() and json.loads(source.read_text()).get("commit") == pin:
            return release / "qdata"
    raise NeedsOperator(f"no prepared release carries quant-data {pin}; provide it before preparing")


def prepare(sha, archive):
    target, data = root_of(sha), Path(archive).read_bytes()
    if archive_commit(data) != sha:
        raise ReleaseError("archive is not `git archive` of the requested commit")
    digest = hashlib.sha256(data).hexdigest()
    receipt = target / ".release.json"
    if receipt.exists():
        if json.loads(receipt.read_text()).get("archive_sha256") != digest:
            raise ReleaseError("release directory exists with different content")
        return json.loads(receipt.read_text())
    if target.exists():
        raise ReleaseError("release directory exists without a receipt; inspect it before reusing the commit")
    staging = target.with_name(target.name + ".staging")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    unpack(data, staging)
    missing = [name for name in REQUIRED if not (staging / name).is_file()]
    if missing:
        shutil.rmtree(staging)
        raise ReleaseError(f"archive lacks {', '.join(missing)}")
    pin = json.loads((staging / "deploy/qdata-source.json").read_text())["commit"]
    shutil.copytree(qdata_source(pin), staging / "qdata")
    record = {"commit": sha, "archive_sha256": digest, "qdata_commit": pin,
              "prepared_at": datetime.now(UTC).isoformat(timespec="seconds")}
    (staging / ".release.json").write_text(json.dumps(record, indent=1) + "\n")
    staging.rename(target)
    return record


# ------------------------------------------------------------------------------------------------------- build/plan

def rendered(root, sha, runner=run):
    return json.loads(compose(root, sha, "config", "--format", "json", runner=runner))["services"]


def ordered_services(root):
    return [service for group in manifest(root)["groups"] for service in group]


def build(sha, runner=run):
    root = root_of(sha)
    for service in manifest(root)["build"]:  # api first: Dockerfile.video builds FROM the app image
        compose(root, sha, "build", service, runner=runner, timeout=3600)
    services, problems = rendered(root, sha, runner=runner), []
    for service in ordered_services(root):
        image = services[service]["image"]
        out = runner(["docker", "image", "inspect", image], check=False)
        rows = json.loads(out or "[]")
        revision = rows[0]["Config"]["Labels"].get("org.opencontainers.image.revision") if rows else None
        if revision != sha:
            problems.append(f"{service}: {image} revision {revision}")
    if problems:
        raise ReleaseError("images not built from the release: " + "; ".join(problems))
    return {service: services[service]["image"] for service in ordered_services(root)}


def plan(sha, services=(), runner=run):
    root = root_of(sha)
    target = rendered(root, sha, runner=runner)
    rows = []
    for service in services or ordered_services(root):
        info = inspect(service, runner=runner)
        wanted = config_hash(compose_args(root), service, runner=runner, env={**os.environ, **release_env(root, sha)})
        current = label(info, "com.docker.compose.config-hash")
        rows.append({
            "service": service, "running": bool(info and info["State"]["Running"]),
            "image": info["Config"]["Image"] if info else None, "target_image": target[service]["image"],
            "recreate": current != wanted,
            # Rolling back recreates from the old container's recorded files; they must still render the same.
            "rollback_exact": bool(info) and config_hash(recorded_args(info), service, runner=runner) == current,
        })
    return rows


# ------------------------------------------------------------------------------------------------------- quietness

def sql(query, runner=run):
    out = runner(["docker", "exec", f"{PROJECT}-postgres-1", "psql", "-U", "postgres", "-d", "quant_company",
                  "-Atc", query], check=False)
    try:
        return int(out.strip() or 0)
    except ValueError:
        return 0


# A receipt left "running" by a crash never changes again, so only recently written ones count as live; runtimes
# share one jobs directory across lanes, so a runtime's own calls are counted from its processes instead.
RUNNING_JOBS = r"""
import json, os, sys, time
d, fresh, skip, n, now = sys.argv[1], float(sys.argv[2]), tuple(sys.argv[3:]), 0, time.time()
for f in os.listdir(d) if os.path.isdir(d) else ():
    path = os.path.join(d, f)
    if f.endswith('.json') and not f.startswith(skip) and now - os.path.getmtime(path) <= fresh:
        try:
            n += (json.load(open(path)).get('state') == 'running')
        except Exception:
            pass
print(n)
"""
LIVE_CALL = {"codex-runtime": "exec --ignore-user-config", "quant-codex-runtime": "exec --ignore-user-config",
             "video-image-runtime": "exec --ignore-user-config", "claude-runtime": " -p --safe-mode"}


def running_jobs(service, skip=(), fresh=1200, runner=run):
    out = runner(["docker", "exec", f"{PROJECT}-{service}-1", "python", "-c", RUNNING_JOBS, "/state/jobs", str(fresh),
                  *skip], check=False)
    try:
        return int(out.strip() or 0)
    except ValueError:
        return 0


def processes(service, needle, runner=run):
    out = runner(["docker", "top", f"{PROJECT}-{service}-1", "-eo", "pid,args"], check=False)
    return sum(needle in line for line in out.splitlines()[1:])


def busy(service, runner=run):
    """Work a recreation would interrupt. Uncertain effects are never replayed, so wait instead."""
    reasons = []
    if service in LIVE_CALL and processes(service, LIVE_CALL[service], runner=runner):
        reasons.append("model call running")
    if service == "worker":
        if running_jobs("codex-runtime", skip=("news", "quant-feed", "video"), runner=runner):
            reasons.append("company model call running")
        if sql("SELECT count(*) FROM turns WHERE status IN ('queued','running')", runner):
            reasons.append("turn queued or running")
    if service == "dispatch" and sql("SELECT count(*) FROM outbox WHERE status='sending'", runner):
        reasons.append("Slack send in flight")
    if service == "video-worker" and sql("SELECT count(*) FROM video_jobs WHERE state NOT IN ("
                                         + ",".join(f"'{s}'" for s in VIDEO_IDLE) + ")", runner):
        reasons.append("video job in production")
    if service in {"news-worker", "briefing-data-worker"}:
        if sql("SELECT count(*) FROM brief_calls WHERE state='running'", runner):
            reasons.append("brief model call running")
        if sql("SELECT count(*) FROM brief_editions WHERE state IN ("
               + ",".join(f"'{s}'" for s in BRIEF_ACTIVE) + ")", runner):
            reasons.append("brief edition being written")
    return reasons


def wait_quiet(service, seconds, runner=run, sleep=time.sleep):
    deadline = time.monotonic() + seconds
    while reasons := busy(service, runner=runner):
        if time.monotonic() >= deadline:
            raise Busy(f"{service} stayed busy: {', '.join(reasons)}")
        sleep(10)


# ---------------------------------------------------------------------------------------------------- apply/verify

PACKAGE = r"""
import hashlib, importlib.util, json, os
spec = importlib.util.find_spec('quant_company')
root = list(spec.submodule_search_locations)[0]
out = {}
for d, dirs, files in os.walk(root):
    dirs[:] = [x for x in dirs if x != '__pycache__']
    for f in files:
        data = open(os.path.join(d, f), 'rb').read()
        out[os.path.relpath(os.path.join(d, f), root)] = hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()
print(json.dumps(out))
"""


def tree(root):
    base, out = root / "src/quant_company", {}
    for path in base.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            data = path.read_bytes()
            out[path.relative_to(base).as_posix()] = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    return out


def package_drift(service, root, runner=run):
    """Files the container's quant_company differs on; the release's package must be there byte for byte."""
    out = runner(["docker", "exec", f"{PROJECT}-{service}-1", "python", "-c", PACKAGE], check=False)
    try:
        running = json.loads(out)
    except ValueError:
        return ["<package unreadable>"]
    expected = tree(root)
    changed = [p for p, blob in running.items() if p in expected and expected[p] != blob]
    extra = [p for p in running if p not in expected]
    missing = [p for p in expected if p.endswith(".py") and p not in running]
    return sorted(changed + extra + missing)


def settle(service, sha, root, wanted, runner=run, sleep=time.sleep, wait=150, hold=30):
    """Running, healthy, not restarting, on the release image and config, with the release's package files."""
    deadline, info = time.monotonic() + wait, None
    while time.monotonic() < deadline:
        info = inspect(service, runner=runner)
        state = (info or {}).get("State", {})
        if state.get("Running") and state.get("Health", {}).get("Status", "healthy") == "healthy":
            break
        sleep(5)
    else:
        return False, "did not become running and healthy"
    restarts = info["RestartCount"]
    sleep(hold)
    info = inspect(service, runner=runner)
    if not info["State"].get("Running") or info["RestartCount"] != restarts or info["State"].get("OOMKilled"):
        return False, "restarted or stopped while settling"
    if label(info, "org.opencontainers.image.revision") != sha:
        return False, f"image revision {label(info, 'org.opencontainers.image.revision')}"
    if label(info, "com.docker.compose.config-hash") != wanted:
        return False, "configuration differs from the plan"
    drift = package_drift(service, root, runner=runner)
    if drift:
        return False, f"package differs from the release: {drift[:5]}"
    return True, "settled"


def migrate(sha, runner=run):
    """One schema migration from the release image; services skip unchanged schema digests on start."""
    root = root_of(sha)
    with host_lock():
        return compose(root, sha, "run", "--rm", "--no-deps", "-T", "api", "quant-company", "migrate", runner=runner)


def deploy_window(sha, service, minutes=10):
    """Marks a recreation in progress (STATE/ops/deploy-window.json), so the ops watch does not alert on the container
    being replaced. It expires by itself if this process dies."""
    path = STATE / "ops/deploy-window.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    write_json(path, {"commit": sha, "services": [service], "pid": os.getpid(),
                      "started_at": now.isoformat(timespec="seconds"),
                      "expires_at": (now + timedelta(minutes=minutes)).isoformat(timespec="seconds")})
    return path


def journal(record):
    directory = STATE / "release-journal"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / f"{record['started_at'].replace(':', '')}-{record['commit'][:12]}.json"
    path.write_text(json.dumps(record, indent=1) + "\n")
    return path


def apply(sha, services, quiet_seconds=900, runner=run, sleep=time.sleep):
    root = root_of(sha)
    if not (root / ".release.json").is_file():
        raise ReleaseError("prepare and build the release first")
    known = set(ordered_services(root))
    if unknown := [s for s in services if s not in known or s in KEEP]:
        raise ReleaseError(f"not releasable here: {unknown}")
    record = {"commit": sha, "started_at": datetime.now(UTC).isoformat(timespec="seconds"), "services": []}
    with host_lock():
        for service in services:
            info = inspect(service, runner=runner)
            if info is None:
                raise ReleaseError(f"{service} is not running; create it explicitly before releasing")
            entry = {"service": service, "old_image": info["Config"]["Image"],
                     "old_config_hash": label(info, "com.docker.compose.config-hash"),
                     "old_config_files": label(info, "com.docker.compose.project.config_files"),
                     "old_working_dir": label(info, "com.docker.compose.project.working_dir")}
            record["services"].append(entry)
            old = recorded_args(info)
            entry["rollback_exact"] = config_hash(old, service, runner=runner) == entry["old_config_hash"]
            wanted = config_hash(compose_args(root), service, runner=runner, env={**os.environ, **release_env(root, sha)})
            if wanted == entry["old_config_hash"] and label(info, "org.opencontainers.image.revision") == sha:
                entry["result"] = "unchanged"
                journal(record)
                continue
            wait_quiet(service, quiet_seconds, runner=runner, sleep=sleep)
            entry["recreated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
            journal(record)
            window = deploy_window(sha, service)
            try:
                compose(root, sha, "up", "-d", "--no-deps", "--no-build", "--pull", "never", service, runner=runner)
                ok, detail = settle(service, sha, root, wanted, runner=runner, sleep=sleep)
                entry["result"], entry["detail"] = ("released" if ok else "failed"), detail
                if not ok:
                    runner(old + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", service])
                    back = inspect(service, runner=runner)
                    entry["rolled_back"] = label(back, "com.docker.compose.config-hash") == entry["old_config_hash"]
            finally:
                window.unlink(missing_ok=True)
            journal(record)
            if not ok:
                raise ReleaseError(f"{service}: {detail}; rolled back (exact={entry['rolled_back']})")
    return record


def verify(sha=None, runner=run):
    sha = sha or (RELEASES.parent / "current").resolve().name
    root = root_of(sha)
    rows, hashes = [], config_hashes(compose_args(root), runner=runner, env={**os.environ, **release_env(root, sha)})
    for service in ordered_services(root):
        info = inspect(service, runner=runner)
        if info is None:
            rows.append({"service": service, "ok": False, "problem": "not running"})
            continue
        wanted = hashes.get(service)
        problems = []
        if label(info, "org.opencontainers.image.revision") != sha:
            problems.append(f"revision {label(info, 'org.opencontainers.image.revision')}")
        if label(info, "com.docker.compose.config-hash") != wanted:
            problems.append("config differs")
        if drift := package_drift(service, root, runner=runner):
            problems.append(f"{len(drift)} package files differ")
        rows.append({"service": service, "ok": not problems, "problem": "; ".join(problems)})
    return rows


def set_env(text, values):
    lines, seen = [], set()
    for line in text.splitlines():
        key = line.split("=", 1)[0] if "=" in line and not line.lstrip().startswith("#") else None
        if key in values:
            seen.add(key)
            if values[key] is not None:
                lines.append(f"{key}={values[key]}")
            continue
        lines.append(line)
    lines += [f"{k}={v}" for k, v in values.items() if k not in seen and v is not None]
    return "\n".join(lines) + "\n"


def activate(sha, runner=run):
    root = root_of(sha)
    if bad := [row for row in verify(sha, runner=runner) if not row["ok"]]:
        raise ReleaseError(f"services not on {sha[:12]}: {bad}")
    with host_lock():
        path = envfile()
        env = release_env(root, sha)
        text = set_env(path.read_text(), {"RELEASE_COMMIT": sha, "QDATA_COMMIT": env["QDATA_COMMIT"],
                                          "QDATA_BUILD_CONTEXT": env["QDATA_BUILD_CONTEXT"],
                                          "PINNED_COMPANY_WORKER_IMAGE": env["PINNED_COMPANY_WORKER_IMAGE"],
                                          "PINNED_CODEX_RUNTIME_IMAGE": None})
        temp = path.with_name(path.name + ".release-tmp")
        temp.write_text(text)
        stat = path.stat()
        temp.chmod(stat.st_mode & 0o777)
        os.chown(temp, stat.st_uid, stat.st_gid)
        os.replace(temp, path)
        current = RELEASES.parent / "current"
        link = current.with_name("current.release-tmp")
        link.unlink(missing_ok=True)
        link.symlink_to(root)
        os.replace(link, current)
    refresh_status(runner=runner)
    return {"commit": sha, "current": str(root)}


AUTO_UNIT = Path("/etc/systemd/system/quant-company-release-auto.service")


def refresh_status(runner=run):
    """Runs the automatic check right away when it is installed, so nothing reads a status written before a release.
    Inside that check itself (an automatic release) systemd merges the request into the running job."""
    if AUTO_UNIT.exists():
        runner(["systemctl", "start", "--no-block", AUTO_UNIT.name], check=False)


# ----------------------------------------------------------------------------------------------- automatic release

GITHUB = "https://api.github.com"
WORKFLOW = "quant-company-ci.yml"
# KST starts: after the AM edition's video, done well before PM collection (KRX close + 20 minutes), and from 18:30,
# when the PM video job already exists, so the quiet check holds a release until that video is delivered.
WINDOWS = "10:00-14:30,18:30-23:00"
KST = timezone(timedelta(hours=9))
# The Fargate render task (`video render-task`) runs its own image (deploy/Dockerfile.render). The modules it imports,
# its design assets, that image, its template and the dependencies must match the render image's commit
# (VIDEO_RENDER_COMMIT). A change needs a new image and task definition, which the operator makes with owner approval.
# An operator who reviews the reported files and finds they cannot change rendering (a shared module the task imports
# for other names) records that commit as VIDEO_RENDER_ACCEPTED; later commits are compared with it instead.
RENDER_ENTRY = "quant_company.video.remote"
RENDER_FILES = ("deploy/Dockerfile.render", "deploy/render.json", "pyproject.toml", "uv.lock")
RENDER_ASSETS = "src/quant_company/video/design/"
# Host files a release does not install. A change is reported for the operator; it does not hold the release.
OPERATOR_FILES = ("deploy/*.service", "deploy/*.timer", "deploy/Caddyfile", "deploy/storage_cleanup.py",
                  "deploy/init-database.sh", "deploy/bootstrap-host.sh", "deploy/provision.py", "deploy/*.example*")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GitHub:
    """Read-only access as the Maintainer GitHub App: every token is limited to contents and actions read. Errors
    carry only the HTTP status, because response bodies can hold private details."""

    def __init__(self, config=None, key=None, opener=None, signer=None):
        value = json.loads(Path(config or STATE / "config/maintenance.json").read_text())
        self.app_id, self.installation, self.repo = str(value["app_id"]), value["installation_id"], value["repository"]
        self.key, self._token = Path(key or STATE / "secrets/maintenance_github_key"), None
        self.open = opener or urllib.request.build_opener(NoRedirect).open
        self.signer = signer or self.openssl

    def openssl(self, unsigned):
        signed = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(self.key)], input=unsigned,
                                capture_output=True, timeout=10)
        if signed.returncode:
            raise ReleaseError("github_app_signing_failed")
        return signed.stdout

    @staticmethod
    def request(url, token=None, data=None):
        headers = {"User-Agent": "quant-company-release"}
        if token:
            headers |= {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        return urllib.request.Request(url, headers=headers, method="GET" if data is None else "POST",
                                      data=None if data is None else json.dumps(data).encode())

    def read(self, request, sink, limit, timeout=60):
        """Copy the response into SINK, at most LIMIT bytes. A redirect is not followed: its Location is returned."""
        try:
            with self.open(request, timeout=timeout) as response:
                size = 0
                while chunk := response.read(1 << 20):
                    size += len(chunk)
                    if size > limit:
                        raise ReleaseError("github_response_too_large")
                    sink.write(chunk)
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308) and exc.headers.get("Location"):
                return exc.headers["Location"]
            raise ReleaseError(f"github_http_{exc.code}") from None
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError):
            raise ReleaseError("github_unreachable") from None
        return None

    def call(self, path, token=None, data=None):
        sink = io.BytesIO()
        if self.read(self.request(GITHUB + path, token or self.token(), data), sink, 32 * 1024 * 1024):
            raise ReleaseError("github_unexpected_redirect")
        return json.loads(sink.getvalue())

    def token(self):
        if self._token is None:
            def encode(value):
                return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=")
            at = int(time.time())
            unsigned = encode({"alg": "RS256", "typ": "JWT"}) + b"." + encode({"iat": at - 60, "exp": at + 540,
                                                                              "iss": self.app_id})
            jwt = (unsigned + b"." + base64.urlsafe_b64encode(self.signer(unsigned)).rstrip(b"=")).decode()
            wanted = {"contents": "read", "actions": "read"}
            data = self.call(f"/app/installations/{self.installation}/access_tokens", jwt,
                             {"repositories": [self.repo.split("/")[1]], "permissions": wanted})
            if {k: v for k, v in data["permissions"].items() if k != "metadata"} != wanted:
                raise ReleaseError("github_token_not_read_only")
            self._token = data["token"]
        return self._token

    def latest_green(self, branch="main"):
        """The newest commit on BRANCH whose CI push run succeeded."""
        runs = self.call(f"/repos/{self.repo}/actions/workflows/{WORKFLOW}/runs"
                         f"?branch={branch}&event=push&status=success&per_page=20")["workflow_runs"]
        runs = [r for r in runs if r["head_branch"] == branch and r["conclusion"] == "success"]
        if not runs:
            return None
        newest = max(runs, key=lambda r: r["run_number"])
        return {"sha": newest["head_sha"], "run": newest["html_url"], "completed_at": newest["updated_at"]}

    def compare(self, base, head):
        value = self.call(f"/repos/{self.repo}/compare/{base}...{head}?per_page=1")
        return {"status": value["status"], "ahead_by": value["ahead_by"]}

    def tarball(self, sha, path):
        """The repository at SHA. GitHub redirects to codeload, which is fetched without the installation token (the
        redirect carries its own short-lived one)."""
        location = self.read(self.request(f"{GITHUB}/repos/{self.repo}/tarball/{sha}", self.token()), io.BytesIO(),
                             1024 * 1024)
        if not (location or "").startswith("https://codeload.github.com/"):
            raise ReleaseError("github_tarball_redirect_unexpected")
        with path.open("wb") as sink:
            if self.read(self.request(location), sink, 400 * 1024 * 1024, timeout=300):
                raise ReleaseError("github_unexpected_redirect")


def files(root):
    """Blob ids of a release's repository files (not its quant-data copy), keyed by repository path."""
    out = {}
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if path.is_file() and rel.parts[0] not in {"qdata", ".release.json"} and "__pycache__" not in rel.parts:
            data = path.read_bytes()
            out[rel.as_posix()] = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    return out


def changed(old, new):
    a, b = files(old), files(new)
    return sorted(p for p in a.keys() | b.keys() if a.get(p) != b.get(p))


def imported(root, entry):
    """Repository paths of the quant_company modules ENTRY imports, directly or through other modules (static)."""
    src, seen, todo, paths = root / "src", set(), [entry], set()
    while todo:
        name = todo.pop()
        if name in seen or not (name == "quant_company" or name.startswith("quant_company.")):
            continue
        seen.add(name)
        base = src.joinpath(*name.split("."))
        path = base / "__init__.py" if (base / "__init__.py").is_file() else base.with_suffix(".py")
        if not path.is_file():
            continue  # a name imported from a module, not a module
        paths.add(path.relative_to(root).as_posix())
        parts = name.split(".")
        package = parts if path.name == "__init__.py" else parts[:-1]
        todo += [".".join(parts[:i]) for i in range(1, len(parts))]  # importing a module runs its packages
        for node in ast.walk(ast.parse(path.read_bytes())):
            if isinstance(node, ast.Import):
                todo += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                stem = package[:len(package) - node.level + 1] if node.level else []
                module = ".".join(stem + ([node.module] if node.module else []))
                todo += [module, *(f"{module}.{alias.name}" for alias in node.names)]
    return paths


def render_changes(paths, roots):
    modules = set().union(*(imported(root, RENDER_ENTRY) for root in roots))
    return [p for p in paths if p in modules or p in RENDER_FILES or p.startswith(RENDER_ASSETS)]


def operator_changes(paths):
    return [p for p in paths if any(fnmatch.fnmatch(p, pattern) for pattern in OPERATOR_FILES)]


def env_values():
    values = {}
    for line in envfile().read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
    return values


def in_window(now, spec):
    at = now.astimezone(KST).strftime("%H:%M")
    return any(start <= at < end for start, end in (part.strip().split("-") for part in spec.split(",") if part.strip()))


def status_file():
    return STATE / "release-status.json"


def progress_file():
    """The release this tool has started and not finished (or a restore it still owes); production state, not a report."""
    return STATE / "release-auto-progress.json"


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def write_json(path, value):
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=1, default=str) + "\n")
    temp.chmod(0o644)
    os.replace(temp, path)


def read_status():
    return read_json(status_file())


def write_status(status, previous):
    """The status other tools read (ops alerts); each change of action or target is also journaled."""
    write_json(status_file(), status)
    if (status.get("action"), status.get("target")) != (previous.get("action"), previous.get("target")):
        directory = STATE / "release-journal"
        directory.mkdir(mode=0o700, exist_ok=True)
        with (directory / "auto.jsonl").open("a") as log:
            entry = {key: status.get(key) for key in ("checked_at", "action", "target", "current", "detail")}
            log.write(json.dumps(entry, default=str) + "\n")
    return status


def production_busy(runner=run):
    """Work a full release would wait on; it starts only while there is none."""
    return sorted({reason for service in ("worker", "news-worker", "video-worker")
                   for reason in busy(service, runner=runner)})


def host_free():
    try:
        with host_lock(wait=0):
            return True
    except Busy:
        return False


def fetch(sha, github, protect=(), runner=run):
    """SHA's prepared release, downloaded from GitHub unless an earlier run prepared it. Preparing one prunes old
    releases that were never built, so commits that were only reported on do not pile up."""
    root = root_of(sha)
    if not (root / ".release.json").is_file():
        archives = STATE / "release-archives"
        archives.mkdir(mode=0o700, exist_ok=True)
        archive = archives / f"{sha}.tar.gz"
        try:
            github.tarball(sha, archive)
            prepare(sha, archive)
        finally:
            archive.unlink(missing_ok=True)
        prune(protect={sha, *protect}, runner=runner, unbuilt_only=True)
    return root


def check_release(current_root, root, sha, runner=run):
    """What a release cannot do alone: add or remove services, or render without a host value it needs."""
    old, new = set(ordered_services(current_root)), set(ordered_services(root))
    if old != new:
        raise NeedsOperator(f"services change: added {sorted(new - old)}, removed {sorted(old - new)}")
    try:
        compose(root, sha, "config", "--quiet", runner=runner)
    except ReleaseError as exc:
        raise NeedsOperator(f"compose does not render with runtime.env: {exc}") from None


def moved(sha, services, runner=run):
    return [s for s in services if label(inspect(s, runner=runner), "org.opencontainers.image.revision") == sha]


def restore(previous, target, runner=run):
    """After a failed release, recreate every service already on TARGET from the release it replaced."""
    services = moved(target, ordered_services(root_of(previous)), runner=runner)
    result = {"services": services, "ok": True}
    if services:
        try:
            apply(previous, services, runner=runner)
        except ReleaseError as exc:
            result.update(ok=False, detail=str(exc)[-300:])
    return result


def prune(keep=3, protect=(), runner=run, unbuilt_only=False):
    """Releases this tool prepared beyond the newest KEEP, with their images (about 4.7 GB of layers each). Never the
    current release, one a running container was created from, or a PROTECTed commit (the render image's).
    UNBUILT_ONLY removes only releases that were never built, keeping every rollback point."""
    current = (RELEASES.parent / "current").resolve()
    used = set(runner(["docker", "ps", "--format", '{{.Label "com.docker.compose.project.working_dir"}}'],
                      check=False).split())
    made = sorted((d for d in RELEASES.iterdir() if (d / ".release.json").is_file()),
                  key=lambda d: json.loads((d / ".release.json").read_text())["prepared_at"], reverse=True)
    removed = []
    for directory in made[keep:]:
        if directory.resolve() == current or str(directory / "deploy") in used or directory.name in protect:
            continue
        tags = runner(["docker", "image", "ls", "--filter", f"reference=*:{directory.name}",
                       "--format", "{{.Repository}}:{{.Tag}}"], check=False).split()
        if tags and unbuilt_only:
            continue
        for tag in tags:
            runner(["docker", "image", "rm", tag], check=False)
        shutil.rmtree(directory)
        removed.append(directory.name)
    return removed


def release_to(status, target, previous, values, runner=run):
    """Build, migrate, apply every group, activate. Busy (quiet or lock) leaves the rest to the next run; any other
    failure returns the services that moved to the release they replaced, and the commit is not tried again."""
    started = status.get("started_at") or status["checked_at"]
    write_json(progress_file(), {"phase": "releasing", "target": target, "previous": previous, "started_at": started})
    status = {**status, "action": "releasing", "detail": None, "started_at": started}

    def step(name):  # the status shows where a long release is; the timer starts no run while this one lasts
        status["step"] = {"name": name, "at": datetime.now(UTC).isoformat(timespec="seconds")}
        write_status(status, read_status())

    try:
        step("build")
        build(target, runner=runner)
        step("migrate")
        migrate(target, runner=runner)
        groups, released = manifest(root_of(target))["groups"], []
        for index, group in enumerate(groups, 1):
            step(f"apply {index}/{len(groups)}: {' '.join(group)}")
            record = apply(target, group, quiet_seconds=600, runner=runner)
            released += [e["service"] for e in record["services"] if e["result"] == "released"]
        step("activate")
        activate(target, runner=runner)
    except Busy as exc:
        return {**status, "action": "incomplete", "detail": str(exc)[-600:]}
    except Exception as exc:  # noqa: BLE001 - recorded, production restored, the run exits non-zero
        detail = f"{type(exc).__name__}: {exc}"[-600:]
        # Until the restore succeeds, later runs keep restoring (at any hour) instead of resuming the release.
        write_json(progress_file(), {"phase": "restoring", "target": target, "previous": previous, "detail": detail})
        restored = restore(previous, target, runner=runner)
        if restored["ok"]:
            progress_file().unlink(missing_ok=True)
        return {**status, "action": "failed", "failed_commit": target, "detail": detail, "restored": restored}
    progress_file().unlink(missing_ok=True)
    pruned = prune(protect={values.get("VIDEO_RENDER_COMMIT"), values.get("VIDEO_RENDER_ACCEPTED")}, runner=runner)
    return {**status, "action": "released", "current": target, "target": None, "failed_commit": None, "drift": [],
            "step": None, "released": {"commit": target, "services": released,
                                       "finished_at": datetime.now(UTC).isoformat(timespec="seconds")},
            "pruned": pruned}


def decide(status, previous, values, now, github, runner=run):
    current, progress = status["current"], read_json(progress_file())
    if status["mode"] == "paused":  # an operator is changing production by hand: drift is their work in progress
        return {**status, "action": "paused", "detail": "RELEASE_AUTO_ENABLED=paused"}
    if progress.get("phase") == "restoring":
        status.update(target=progress["target"], detail=progress["detail"])
        if status["mode"] != "release":
            return {**status, "action": "would_restore"}
        restored = restore(current, progress["target"], runner=runner)
        if restored["ok"]:
            progress_file().unlink(missing_ok=True)
        return {**status, "action": "failed", "restored": restored}
    status["drift"] = [row for row in verify(current, runner=runner) if not row["ok"]]
    target = progress.get("target") if progress.get("phase") == "releasing" else None
    # A release that already moved services is finished first, at any hour and without GitHub; one that moved none
    # starts over under the usual checks.
    if target and target != current and moved(target, ordered_services(root_of(current)), runner):
        status.update(target=target, started_at=progress.get("started_at"))
        status["operator_files"] = operator_changes(changed(root_of(current), root_of(target)))
        resuming = True
    else:
        resuming = False
        latest = status["latest_green"] = github.latest_green()
        if not latest or latest["sha"] == current:
            idle = "up_to_date" if latest else "no_green_main"
            return {**status, "action": "drift" if status["drift"] else idle}
        target = status["target"] = latest["sha"]
        order = github.compare(current, target)
        status["behind_by"] = order["ahead_by"]
        if order["status"] == "behind":  # production runs a newer main commit whose CI has not passed yet
            return {**status, "target": None, "action": "drift" if status["drift"] else "ahead_of_green",
                    "detail": f"the running release is newer than the latest CI-green main {target[:12]}"}
        if order["status"] != "ahead":
            return {**status, "action": "refused",
                    "detail": f"{target[:12]} is {order['status']} of the running release"}
        render_commit = values.get("VIDEO_RENDER_COMMIT") or current
        render_base = values.get("VIDEO_RENDER_ACCEPTED") or render_commit
        root = fetch(target, github, protect={render_commit, render_base}, runner=runner)
        if not (difference := changed(root_of(current), root)):  # docs or tests only: production runs the same files
            return {**status, "action": "drift" if status["drift"] else "up_to_date", "target": None,
                    "detail": f"main {target[:12]} changes no file a release deploys"}
        status["operator_files"] = operator_changes(difference)
        base = root_of(current) if render_base == current else fetch(render_base, github, {target, render_commit}, runner)
        if render := render_changes(changed(base, root), [base, root]):
            whose = "the render image's commit" if render_base == render_commit else "the commit accepted for the render image"
            return {**status, "action": "render_required", "render_files": render[:20],
                    "detail": f"{len(render)} render files differ from {whose} {render_base[:12]}"}
        check_release(root_of(current), root, target, runner=runner)
        if previous.get("failed_commit") == target:
            return {**status, "action": "held_after_failure",
                    "detail": "this commit's release failed; `release.py auto --retry` tries it again"}
        if status["drift"]:
            return {**status, "action": "held_for_drift", "detail": "production differs from its own release"}
        if not in_window(now, values.get("RELEASE_AUTO_WINDOWS") or WINDOWS):
            return {**status, "action": "waiting_for_window"}
        if reasons := production_busy(runner=runner):
            return {**status, "action": "waiting_for_quiet", "detail": ", ".join(reasons)}
    if not host_free():
        return {**status, "action": "waiting_for_lock", "detail": "a backup or another release holds the host lock"}
    if status["mode"] != "release":
        return {**status, "action": "would_resume" if resuming else "would_release"}
    return release_to(status, target, current, values, runner=runner)


def auto(github=None, runner=run, now=None, retry=False):
    """The timer's run (docs/adr/0044): compare production with CI-green main and write STATE/release-status.json;
    with RELEASE_AUTO_ENABLED=true also release main when nothing holds it. RETRY tries a failed commit again."""
    with (STATE / ".release-auto.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"action": "already_running"}
        previous, values, now = read_status(), env_values(), now or datetime.now(UTC)
        if retry:
            previous["failed_commit"] = None
        status = {"checked_at": now.isoformat(timespec="seconds"),
                  "mode": {"true": "release", "paused": "paused"}.get(values.get("RELEASE_AUTO_ENABLED"), "report"),
                  "current": (RELEASES.parent / "current").resolve().name,
                  "render": {"commit": values.get("VIDEO_RENDER_COMMIT"), "accepted": values.get("VIDEO_RENDER_ACCEPTED"),
                             "task_definition": values.get("VIDEO_RENDER_TASK_DEFINITION")},
                  "failed_commit": previous.get("failed_commit")}
        try:
            status = decide(status, previous, values, now, github or GitHub(), runner=runner)
        except NeedsOperator as exc:
            status.update(action="operator_required", detail=str(exc)[-600:])
        except Exception as exc:  # noqa: BLE001 - recorded in the status; the run exits non-zero
            status.update(action="error", detail=f"{type(exc).__name__}: {exc}"[-600:])
        return write_status(status, read_status())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["prepare", "build", "plan", "migrate", "apply", "verify", "activate", "auto"])
    parser.add_argument("services", nargs="*")
    parser.add_argument("--sha")
    parser.add_argument("--archive")
    parser.add_argument("--quiet-seconds", type=int, default=900)
    parser.add_argument("--retry", action="store_true", help="auto: try a commit whose release failed again")
    args = parser.parse_intermixed_args(argv)
    os.umask(0o022)
    if args.command == "prepare":
        result = prepare(args.sha, args.archive)
    elif args.command == "build":
        result = build(args.sha)
    elif args.command == "plan":
        result = plan(args.sha, args.services)
    elif args.command == "migrate":
        result = migrate(args.sha)
    elif args.command == "apply":
        result = apply(args.sha, args.services, quiet_seconds=args.quiet_seconds)
    elif args.command == "verify":
        result = verify(args.sha)
    elif args.command == "auto":
        result = auto(retry=args.retry)
    else:
        result = activate(args.sha)
    print(json.dumps(result, indent=1, default=str))
    if args.command == "verify" and not all(row["ok"] for row in result):
        return 1
    if args.command == "auto" and result.get("action") in {"failed", "error"}:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        sys.exit(2)
