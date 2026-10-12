#!/usr/bin/env python3
"""Scoped two-image housing package rollout on an already active mixed-image host."""

import argparse
import fcntl
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import time
from datetime import datetime
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

STATE = Path("/var/lib/quant-company")
ENV = STATE / "config/runtime.env"
SERVICES = ("housing-feed-worker", "dispatch")


def run(command):
    result = subprocess.run(command, capture_output=True, timeout=900)
    if result.returncode:
        raise RuntimeError(f"{command[0]} failed with exit {result.returncode}")
    return result.stdout.decode().strip()


def atomic(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def inventory():
    names = run(["docker", "ps", "-aq"]).splitlines()
    rows = json.loads(run(["docker", "inspect", *names])) if names else []
    return {row["Name"].lstrip("/"): {
        "id": row["Id"], "image_id": row["Image"], "running": row["State"]["Running"],
        "oom": row["State"]["OOMKilled"], "restarts": row["RestartCount"],
    } for row in rows}


def service(name):
    row = json.loads(run(["docker", "inspect", "quant-company-" + name + "-1"]))[0]
    labels = row["Config"]["Labels"]
    files = labels["com.docker.compose.project.config_files"].split(",")
    command = ["docker", "compose", "--project-name", "quant-company"]
    if name == "housing-feed-worker":
        command += ["--profile", "housing-feed"]
    command += ["--env-file", str(ENV)]
    for path in files:
        if not Path(path).is_file():
            raise ValueError("housing_base_compose_file_missing")
        command += ["-f", path]
    actual = run([*command, "config", "--hash", name]).split()[-1]
    if actual != labels["com.docker.compose.config-hash"]:
        raise ValueError("housing_base_compose_drift")
    return {"container_id": row["Id"], "image_id": row["Image"], "image": row["Config"]["Image"],
            "config_hash": actual, "files": files, "command": command}


def unpack(bundle, target):
    with tarfile.open(bundle, "r:gz") as archive:
        members = archive.getmembers()
        if sum(item.size for item in members) > 2_000_000:
            raise ValueError("housing_bundle_too_large")
        allowed = {"deploy/Dockerfile.housing-package-update"}
        for item in members:
            name = PurePosixPath(item.name)
            if (item.name.startswith("/") or ".." in name.parts or not (item.isdir() or item.isfile())
                    or (item.isfile() and not (item.name in allowed or
                        item.name.startswith("src/quant_company/housing_feed/")))):
                raise ValueError("housing_bundle_member_invalid")
        archive.extractall(target, filter="data")
    if not (target / "deploy/Dockerfile.housing-package-update").is_file():
        raise ValueError("housing_bundle_dockerfile_missing")


def package_hashes(root):
    package = root / "src/quant_company/housing_feed"
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in package.iterdir() if path.is_file()}


def image_package(image):
    code = ("import hashlib,importlib.util,json,pathlib;"
            "p=pathlib.Path(importlib.util.find_spec('quant_company.housing_feed').origin).parent;"
            "print(json.dumps({f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in p.iterdir() if f.is_file()}))")
    return json.loads(run(["docker", "run", "--rm", "--network=none", "--entrypoint", "python",
                           image, "-c", code]))


def official_preview(image):
    code = ("import json;from datetime import datetime;from zoneinfo import ZoneInfo;"
            "from quant_company.housing_feed.sources import collect_source;"
            "today=datetime.now(ZoneInfo('Asia/Seoul')).date();"
            "results={s:collect_source(s,today) for s in ('lh-rental','sh-rental')};"
            "print(json.dumps({s:{'ok':r['ok'],'notices':len(r.get('entries',[])),"
            "'requests':len(r['requests']),'error':r.get('error')} for s,r in results.items()}))")
    result = json.loads(run(["docker", "run", "--rm", "--network=host", "--entrypoint", "python",
                             image, "-c", code]))
    if set(result) != {"lh-rental", "sh-rental"} or any(not row["ok"] for row in result.values()):
        raise ValueError("housing_official_preview_failed")
    return result


def compose(base, *args, override=None):
    command = list(base["command"])
    if override:
        command += ["-f", override]
    return run([*command, *args])


def pending():
    sql = ("SELECT (SELECT count(*) FROM housing_feed_publications p JOIN outbox o ON o.id=p.id "
           "WHERE o.status='pending'),(SELECT count(*) FROM housing_feed_publications p JOIN outbox o "
           "ON o.id=p.id WHERE o.status='sending'),"
           "(SELECT count(*) FROM housing_feed_sources WHERE lease_token IS NOT NULL),"
           "(SELECT count(*) FROM housing_feed_sources WHERE error IS NOT NULL)")
    output = run(["docker", "exec", "-u", "postgres", "quant-company-postgres-1", "psql",
                  "-X", "-A", "-t", "-d", "quant_company", "-c", sql])
    return [int(value) for value in output.split("|")]


def rental_sources_ready(since):
    sql = ("SELECT id,coalesce(extract(epoch FROM last_success)::bigint,0),"
           "coalesce(error,'') FROM housing_feed_sources WHERE id IN ('lh-rental','sh-rental')")
    output = run(["docker", "exec", "-u", "postgres", "quant-company-postgres-1", "psql",
                  "-X", "-A", "-t", "-d", "quant_company", "-c", sql])
    rows = [line.split("|", 2) for line in output.splitlines() if line]
    return len(rows) == 2 and all(int(row[1]) >= since and not row[2] for row in rows)


def stage(args, target, journal):
    if target.exists() or journal.exists() or shutil.disk_usage(STATE).free < 3 * 1024**3:
        raise ValueError("housing_stage_target_exists_or_disk_low")
    bundle = args.bundle.read_bytes()
    if hashlib.sha256(bundle).hexdigest() != args.sha256:
        raise ValueError("housing_bundle_digest_mismatch")
    before = inventory()
    bases = {name: service(name) for name in SERVICES}
    if any(not before["quant-company-" + name + "-1"]["running"] for name in SERVICES):
        raise ValueError("housing_base_service_stopped")
    target.mkdir(mode=0o700)
    unpack(args.bundle, target)
    expected = package_hashes(target)
    if len(expected) < 10:
        raise ValueError("housing_package_incomplete")
    images, overrides = {}, {}
    for name in SERVICES:
        base = bases[name]
        tag = f"quant-company-housing-rental:{args.commit}-{name}"
        pinned = "housing-rental-base:" + base["image_id"].split(":", 1)[1]
        run(["docker", "image", "tag", base["image_id"], pinned])
        if json.loads(run(["docker", "image", "inspect", pinned]))[0]["Id"] != base["image_id"]:
            raise ValueError("housing_base_image_changed")
        run(["docker", "build", "--network=none", "--pull=false", "-f",
             str(target / "deploy/Dockerfile.housing-package-update"),
             "--build-arg", "BASE_IMAGE=" + pinned,
             "--build-arg", "HOUSING_COMMIT=" + args.commit, "-t", tag, str(target)])
        if image_package(tag) != expected:
            raise ValueError("housing_image_package_mismatch")
        image = json.loads(run(["docker", "image", "inspect", tag]))[0]
        if image["Config"]["Labels"].get("org.quant-company.housing-feed-revision") != args.commit:
            raise ValueError("housing_image_revision_mismatch")
        images[name] = {"tag": tag, "id": image["Id"], "base_id": base["image_id"]}
        override = target / (name + ".compose.json")
        atomic(override, {"services": {name: {"image": tag}}})
        original = json.loads(compose(base, "config", "--format", "json"))
        candidate = json.loads(compose(base, "config", "--format", "json", override=str(override)))
        candidate["services"][name]["image"] = original["services"][name]["image"]
        if candidate != original:
            raise ValueError("housing_compose_override_changes_other_configuration")
        overrides[name] = str(override)
    preview = official_preview(images["housing-feed-worker"]["tag"])
    if inventory() != before:
        raise ValueError("housing_stage_changed_running_services")
    receipt = {"phase": "staged", "commit": args.commit, "bundle_sha256": args.sha256,
               "current_release": str(Path("/opt/quant-company/current").resolve()),
               "created_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
               "before": before, "bases": bases, "images": images, "overrides": overrides,
               "package": expected, "official_preview": preview}
    atomic(journal, receipt)
    return {"phase": "staged", "images": images, "official_preview": preview,
            "other_services_preserved": True}


def quarantine():
    sql = ("UPDATE outbox SET status=CASE WHEN status='sending' THEN 'uncertain' ELSE 'stale' END,"
           "error='housing_rental_release_rollback' WHERE status IN ('pending','sending') "
           "AND id IN (SELECT id FROM housing_feed_publications)")
    run(["docker", "exec", "-u", "postgres", "quant-company-postgres-1", "psql", "-X", "-q",
         "-v", "ON_ERROR_STOP=1", "-d", "quant_company", "-c", sql])


def activate(args, journal):
    receipt = json.loads(journal.read_text())
    if receipt["phase"] != "staged" or receipt["commit"] != args.commit:
        raise ValueError("housing_stage_receipt_mismatch")
    if receipt["current_release"] != str(Path("/opt/quant-company/current").resolve()):
        raise ValueError("housing_current_release_changed")
    if inventory() != receipt["before"] or pending() != [0, 0, 0, 0]:
        raise ValueError("housing_pending_or_service_drift")
    for name in SERVICES:
        current = service(name)
        if current != receipt["bases"][name]:
            raise ValueError("housing_compose_or_base_changed")
        if image_package(receipt["images"][name]["tag"]) != receipt["package"]:
            raise ValueError("housing_staged_image_changed")
    receipt["phase"] = "cutover_started"
    receipt["cutover_epoch"] = int(time.time())
    atomic(journal, receipt)
    try:
        for name in SERVICES:
            compose(receipt["bases"][name], "stop", "-t", "90", name)
        for name in SERVICES:
            compose(receipt["bases"][name], "up", "-d", "--no-deps", "--no-build", "--force-recreate",
                    name, override=receipt["overrides"][name])
        after = inventory()
        for name, previous in receipt["before"].items():
            if name not in {"quant-company-" + value + "-1" for value in SERVICES} and after.get(name) != previous:
                raise ValueError("housing_unrelated_service_changed")
        for name in SERVICES:
            row = after["quant-company-" + name + "-1"]
            if not row["running"] or row["oom"] or row["image_id"] != receipt["images"][name]["id"]:
                raise ValueError("housing_updated_service_unhealthy")
        result = json.loads(run(["docker", "exec", "quant-company-housing-feed-worker-1",
                                 "python", "/app/entrypoint.py", "quant-company", "housing-feed", "collect"]))
        for _ in range(24):
            if rental_sources_ready(receipt["cutover_epoch"]):
                break
            time.sleep(5)
        else:
            raise ValueError("housing_live_collection_failed")
        receipt.update({"phase": "activated", "after": after, "collection": result,
                        "activated_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat()})
        atomic(journal, receipt)
        return {"phase": "activated", "rental_sources_collected": True, "other_services_preserved": True}
    except BaseException:
        try:
            for name in SERVICES:
                compose(receipt["bases"][name], "stop", "-t", "90", name,
                        override=receipt["overrides"][name])
            try:
                quarantine()
            except BaseException:
                receipt["quarantine_failed"] = True
            for name in SERVICES:
                compose(receipt["bases"][name], "up", "-d", "--no-deps", "--no-build", "--force-recreate", name)
            receipt["phase"] = "rollback_incomplete" if receipt.get("quarantine_failed") else "rolled_back"
        except BaseException:
            receipt["phase"] = "rollback_incomplete"
        atomic(journal, receipt)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("stage", "activate"))
    parser.add_argument("--commit", required=True)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--sha256")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.commit):
        raise ValueError("housing_invalid_commit")
    if args.action == "stage" and (not args.bundle or not re.fullmatch(r"[0-9a-f]{64}", args.sha256 or "")):
        raise ValueError("housing_stage_requires_bundle")
    target = STATE / "releases" / ("housing-rental-" + args.commit)
    journal = STATE / "releases" / ("housing-rental-" + args.commit + ".json")
    with (STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = stage(args, target, journal) if args.action == "stage" else activate(args, journal)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
