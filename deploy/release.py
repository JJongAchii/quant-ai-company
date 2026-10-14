#!/usr/bin/env python3
"""Run every service from one reviewed main commit (root on the host, standard library only).

    release.py prepare  --sha SHA --archive FILE   unpack a `git archive` of SHA into /opt/quant-company/releases/SHA
    release.py build    --sha SHA                  build every image of that release (tagged with SHA)
    release.py plan     --sha SHA [SERVICE ...]    what each service would change; nothing is touched
    release.py migrate  --sha SHA                  one schema migration from the release image
    release.py apply    --sha SHA SERVICE ...      recreate services one at a time; roll a failed one back
    release.py verify   [--sha SHA]                every running service: commit, rendered config, package files
    release.py activate --sha SHA                  after all services run SHA: runtime.env pins and the current link

Every service is rendered from deploy/release.json of the release, runtime.env, and the release variables below.
A service is recreated only when it is quiet, must settle on the release image with the planned config and the
release's exact package files, and is otherwise recreated from the compose set recorded on its old container.
"""

import argparse
import contextlib
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

STATE = Path(os.environ.get("QC_STATE_DIR", "/var/lib/quant-company"))
RELEASES = Path(os.environ.get("QC_RELEASES_DIR", "/opt/quant-company/releases"))
PROJECT = "quant-company"
SHA = re.compile(r"[0-9a-f]{40}")
KEEP = {"postgres", "caddy"}  # data and edge containers are never recreated by a code release
REQUIRED = ("deploy/release.json", "deploy/compose.yaml", "deploy/qdata-source.json", "pyproject.toml", "uv.lock",
            "src/quant_company/__init__.py")
# Terminal states come from video/store.py and briefing/store.py.
VIDEO_DONE = ("published", "expired", "held", "superseded", "blocked", "delivered", "delivery_failed",
              "delivery_uncertain")
BRIEF_ACTIVE = ("planning", "inventorying", "writing", "reviewing", "revising", "final_reviewing")


class ReleaseError(RuntimeError):
    pass


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


def label(info, key):
    return (info or {}).get("Config", {}).get("Labels", {}).get(key)


@contextlib.contextmanager
def host_lock():
    """Same lock as backups and Maintainer releases: one host-changing operation at a time."""
    path = STATE / ".backup.lock"
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ReleaseError("another backup or release holds the host lock") from None
        yield


# ---------------------------------------------------------------------------------------------------------- prepare

def archive_commit(data):
    """`git archive` stores the commit in the pax global header; refuse anything else."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as bundle:
        return bundle.pax_headers.get("comment")


def unpack(data, target):
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as bundle:
        members = bundle.getmembers()
        if sum(m.size for m in members) > 512 * 1024 * 1024:
            raise ReleaseError("release archive expands beyond 512 MiB")
        for item in members:
            parts = PurePosixPath(item.name).parts
            if item.name.startswith("/") or ".." in parts or not (item.isdir() or item.isfile()):
                raise ReleaseError(f"unsafe archive member: {item.name}")
        prefix = {PurePosixPath(m.name).parts[0] for m in members if m.isfile()}
        strip = 1 if len(prefix) == 1 and not any(m.name in REQUIRED for m in members) else 0
        for item in members:
            parts = PurePosixPath(item.name).parts[strip:]
            if not parts:
                continue
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
    raise ReleaseError(f"no prepared release carries quant-data {pin}; provide it before preparing")


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


RUNNING_JOBS = r"""
import json, os, sys
d, skip, n = sys.argv[1], tuple(sys.argv[2:]), 0
for f in os.listdir(d) if os.path.isdir(d) else ():
    if f.endswith('.json') and not f.startswith(skip):
        try:
            n += (json.load(open(os.path.join(d, f))).get('state') == 'running')
        except Exception:
            pass
print(n)
"""


def running_jobs(service, skip=(), runner=run):
    out = runner(["docker", "exec", f"{PROJECT}-{service}-1", "python", "-c", RUNNING_JOBS, "/state/jobs", *skip],
                 check=False)
    try:
        return int(out.strip() or 0)
    except ValueError:
        return 0


def busy(service, runner=run):
    """Work a recreation would interrupt. Uncertain effects are never replayed, so wait instead."""
    reasons = []
    if service in {"codex-runtime", "quant-codex-runtime", "video-image-runtime", "claude-runtime"}:
        if running_jobs(service, runner=runner):
            reasons.append("model call running")
    if service == "worker":
        if running_jobs("codex-runtime", skip=("news", "quant-feed", "video"), runner=runner):
            reasons.append("company model call running")
        if sql("SELECT count(*) FROM turns WHERE status IN ('queued','running')", runner):
            reasons.append("turn queued or running")
    if service == "dispatch" and sql("SELECT count(*) FROM outbox WHERE status='sending'", runner):
        reasons.append("Slack send in flight")
    if service == "video-worker" and sql("SELECT count(*) FROM video_jobs WHERE state NOT IN ("
                                         + ",".join(f"'{s}'" for s in VIDEO_DONE) + ")", runner):
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
            raise ReleaseError(f"{service} stayed busy: {', '.join(reasons)}")
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
            compose(root, sha, "up", "-d", "--no-deps", "--no-build", "--pull", "never", service, runner=runner)
            ok, detail = settle(service, sha, root, wanted, runner=runner, sleep=sleep)
            entry["result"], entry["detail"] = ("released" if ok else "failed"), detail
            if not ok:
                runner(old + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", service])
                back = inspect(service, runner=runner)
                entry["rolled_back"] = label(back, "com.docker.compose.config-hash") == entry["old_config_hash"]
                journal(record)
                raise ReleaseError(f"{service}: {detail}; rolled back (exact={entry['rolled_back']})")
            journal(record)
    return record


def verify(sha=None, runner=run):
    sha = sha or (RELEASES.parent / "current").resolve().name
    root = root_of(sha)
    rows = []
    for service in ordered_services(root):
        info = inspect(service, runner=runner)
        if info is None:
            rows.append({"service": service, "ok": False, "problem": "not running"})
            continue
        wanted = config_hash(compose_args(root), service, runner=runner, env={**os.environ, **release_env(root, sha)})
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
    return {"commit": sha, "current": str(root)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["prepare", "build", "plan", "migrate", "apply", "verify", "activate"])
    parser.add_argument("services", nargs="*")
    parser.add_argument("--sha")
    parser.add_argument("--archive")
    parser.add_argument("--quiet-seconds", type=int, default=900)
    args = parser.parse_args(argv)
    os.umask(0o022)
    if args.command == "prepare":
        result = prepare(args.sha, args.archive)
    elif args.command == "build":
        result = build(args.sha)
    elif args.command == "plan":
        result = plan(args.sha, args.services)
    elif args.command == "migrate":
        root = root_of(args.sha)
        with host_lock():
            result = compose(root, args.sha, "run", "--rm", "--no-deps", "-T", "api", "quant-company", "migrate")
    elif args.command == "apply":
        result = apply(args.sha, args.services, quiet_seconds=args.quiet_seconds)
    elif args.command == "verify":
        result = verify(args.sha)
    else:
        result = activate(args.sha)
    print(json.dumps(result, indent=1, default=str))
    if args.command == "verify" and not all(row["ok"] for row in result):
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        sys.exit(2)
