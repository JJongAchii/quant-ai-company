"""Scoped two-file presentation stage/cutover; no upstream data writes."""

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

COMMIT = "8508954320877144e8f24cb52c8353c0e18f514a"
STATE = Path("/var/lib/quant-company")
CURRENT = Path("/opt/quant-company/current")
ENV = STATE / "config/runtime.env"
ROOT = Path("/tmp/piddock-data-watch-readable-20261006")
JOURNAL = STATE / "releases/data-watch-readable-v6-20261006.json"
TARGETS = ("api", "slack-socket", "data-watch-worker")
FILES = ("coverage.py", "reporting.py")
TIMER = "quant-company-release.timer"
INVENTORY = """import hashlib,importlib.util,json,pathlib
root=pathlib.Path(importlib.util.find_spec('quant_company').origin).parent
print(json.dumps({str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
 for p in root.rglob('*.py') if '__pycache__' not in p.parts}))"""


def run(args, timeout=300):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"command_failed:{args[0]}:{result.returncode}")
    return result.stdout


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2))
    temporary.chmod(0o600)
    temporary.replace(path)


def active(unit):
    return subprocess.run(["systemctl", "is-active", "--quiet", unit]).returncode == 0


def inspect():
    names = run(["docker", "ps", "-a", "--format", "{{.Names}}"])
    names = [name for name in names.splitlines() if name.startswith("quant-company-")]
    return {row["Name"].removeprefix("/quant-company-").removesuffix("-1"): row
            for row in json.loads(run(["docker", "inspect", *names]))}


def source_inventory(image):
    return json.loads(run(["docker", "run", "--rm", "--network=none", "--memory=128m", "--cpus=.25",
                           "--entrypoint", "python", image, "-c", INVENTORY]))


def runtime_signature(row):
    config, host = row["Config"], row["HostConfig"]
    return {
        "config": {key: sorted(config[key]) if key == "Env" else config[key]
                   for key in ("Env", "Cmd", "Entrypoint", "User", "WorkingDir")},
        "mounts": sorted((m["Type"], m["Source"], m["Destination"], m["RW"]) for m in row["Mounts"]),
        "host": {key: host.get(key) for key in ("NetworkMode", "ReadonlyRootfs", "CapDrop", "SecurityOpt",
                 "Memory", "MemorySwap", "MemoryReservation", "NanoCpus", "PidsLimit", "RestartPolicy", "PortBindings",
                 "OomKillDisable", "Init", "IpcMode", "Privileged", "Ulimits", "LogConfig")},
    }


def stage():
    archive = Path("/tmp/piddock-data-watch-readable-20261006.tar.gz")
    if ROOT.exists() or JOURNAL.exists():
        raise RuntimeError("stage_already_exists")
    if shutil.disk_usage("/var/lib/docker").free < 1024**3:
        raise RuntimeError("insufficient_disk_headroom")
    ROOT.mkdir(mode=0o700)
    with tarfile.open(archive) as bundle:
        if any((not item.isfile() and not item.isdir()) or item.name.startswith("/") or ".." in Path(item.name).parts
               for item in bundle.getmembers()):
            raise RuntimeError("invalid_archive_member")
        bundle.extractall(ROOT, filter="data")
    manifest = json.loads((ROOT / "docs/project/evidence/data-watch-readability-20261006/presentation-manifest.json").read_text())
    before = inspect()
    record = {"state": "staging", "presentation_commit": COMMIT, "at": time.time(),
              "current": CURRENT.resolve().name, "runtime_env_sha256": hashlib.sha256(ENV.read_bytes()).hexdigest(),
              "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "images": {}, "targets": {}}
    for name in TARGETS:
        row = before[name]
        if not row["State"]["Running"]:
            raise RuntimeError("target_not_running:" + name)
        record["targets"][name] = {"id": row["Id"], "base_image": row["Image"], "base_tag": row["Config"]["Image"],
                                  "source_revision": row["Config"]["Labels"].get("org.opencontainers.image.revision")}
    atomic(JOURNAL, record)
    for base in {row["base_image"] for row in record["targets"].values()}:
        target = next(row for row in record["targets"].values() if row["base_image"] == base)
        image = json.loads(run(["docker", "image", "inspect", target["base_tag"]]))[0]
        if image["Id"] != base or image["Config"]["User"] != "10001:10001":
            raise RuntimeError("base_image_tag_or_user_changed")
        original = source_inventory(base)
        expected = dict(original)
        for name in FILES:
            relative = "data_watch/" + name
            changes = manifest["files"]["src/quant_company/" + relative]
            if original.get(relative) != changes["before"]:
                raise RuntimeError("base_presentation_changed:" + name)
            expected[relative] = changes["after"]
        tag = "quant-company-data-watch:" + COMMIT + "-" + base.split(":")[1][:12]
        run(["docker", "build", "--network=none", "--pull=false", "--build-arg", "BASE_IMAGE=" + target["base_tag"],
             "--build-arg", "PRESENTATION_COMMIT=" + COMMIT, "--label", "io.quant-company.data-watch.base-id=" + base,
             "-t", tag, "-f", str(ROOT / "deploy/Dockerfile.data-watch-presentation"), str(ROOT)], timeout=900)
        built = json.loads(run(["docker", "image", "inspect", tag]))[0]
        if source_inventory(built["Id"]) != expected:
            raise RuntimeError("source_delta_not_exactly_two_files")
        for key in ("Env", "Cmd", "Entrypoint", "User", "WorkingDir"):
            if built["Config"][key] != image["Config"][key]:
                raise RuntimeError("image_runtime_config_changed:" + key)
        if (built["Config"]["Labels"].get("org.opencontainers.image.revision") != target["source_revision"]
                or built["Config"]["Labels"].get("io.quant-company.data-watch.presentation") != COMMIT):
            raise RuntimeError("image_source_provenance_changed")
        record["images"][base] = {"tag": tag, "id": built["Id"], "source_files": len(original),
                                  "only_changed_files": ["data_watch/" + name for name in FILES]}
        atomic(JOURNAL, record)
    record["state"] = "staged"
    atomic(JOURNAL, record)
    print(json.dumps({"state": record["state"], "current": record["current"], "images": record["images"]}))


def compose_for(row, override):
    labels = row["Config"]["Labels"]
    files = labels["com.docker.compose.project.config_files"].split(",")
    if any(not Path(path).is_file() for path in files):
        raise RuntimeError("recorded_compose_file_missing")
    command = ["docker", "compose", "--project-name", "quant-company", "--profile", "*", "--env-file", str(ENV)]
    for path in (*files, str(override)):
        command.extend(["-f", path])
    return command


def cutover(name):
    record = json.loads(JOURNAL.read_text())
    if (name not in TARGETS or record["state"] not in {"staged", "partially_live"}
            or CURRENT.resolve().name != record["current"]
            or hashlib.sha256(ENV.read_bytes()).hexdigest() != record["runtime_env_sha256"]
            or active("quant-company-release.service") or not active(TIMER)):
        raise RuntimeError("release_baseline_changed_or_busy")
    stage = record["targets"][name]
    image = record["images"][stage["base_image"]]
    if json.loads(run(["docker", "image", "inspect", image["tag"]]))[0]["Id"] != image["id"]:
        raise RuntimeError("staged_image_tag_changed")
    paused = False
    try:
        with (STATE / ".backup.lock").open("a") as lock:
            deadline = time.monotonic() + 20
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("another_operation_holds_deployment_lock") from None
                    time.sleep(.5)
            run(["systemctl", "stop", TIMER])
            paused = True
            if CURRENT.resolve().name != record["current"] or active("quant-company-release.service"):
                raise RuntimeError("release_changed_after_lock")
            before = inspect()
            old = before[name]
            if old["Id"] != stage["id"] or old["Image"] != stage["base_image"]:
                raise RuntimeError("target_container_changed")
            if any(not row["State"]["Running"] or row["State"]["OOMKilled"] for row in before.values()):
                raise RuntimeError("fleet_not_ready")
            if name == "data-watch-worker":
                sql = "SELECT (SELECT count(*) FROM data_watch_inventory WHERE state='running' AND lease_until>now())+(SELECT count(*) FROM data_watch_datasets WHERE lease_until>now())+(SELECT count(*) FROM data_watch_checks WHERE state='running' AND lease_until>now())"
                if run(["docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company", "-Atc", sql]).strip() != "0":
                    raise RuntimeError("data_watch_check_in_flight")
            config = old["Config"]
            settings = {"image": image["tag"], "environment": dict(item.split("=", 1) for item in config["Env"]),
                        "command": config["Cmd"], "entrypoint": config["Entrypoint"],
                        "user": config["User"], "working_dir": config["WorkingDir"],
                        "volumes": [{"type": m["Type"], "source": m.get("Name", m["Source"]) if m["Type"] == "volume" else m["Source"],
                                     "target": m["Destination"], "read_only": not m["RW"]} for m in old["Mounts"]]}
            override = STATE / ("config/data-watch-readable-v6-" + COMMIT + "-" + name + ".compose.json")
            atomic(override, {"services": {name: settings}})
            command = compose_for(old, override)
            compiled = json.loads(run([*command, "config", "--format", "json"]))["services"][name]
            if compiled["image"] != image["tag"] or any(compiled["environment"].get(k) != v for k, v in settings["environment"].items()):
                raise RuntimeError("compiled_target_policy_changed")
            record["cutover"] = {"service": name, "state": "prepared", "at": time.time()}
            atomic(JOURNAL, record)
            try:
                run([*command, "up", "-d", "--no-deps", "--no-build", "--force-recreate", "--wait", "--wait-timeout", "120", name], timeout=180)
                after = inspect()
                fresh = after[name]
                if (fresh["Image"] != image["id"] or not fresh["State"]["Running"] or fresh["State"]["OOMKilled"]
                        or runtime_signature(fresh) != runtime_signature(old)):
                    raise RuntimeError("target_runtime_or_health_changed")
                if CURRENT.resolve().name != record["current"] or hashlib.sha256(ENV.read_bytes()).hexdigest() != record["runtime_env_sha256"]:
                    raise RuntimeError("release_or_policy_changed_during_cutover")
                for peer, previous in before.items():
                    if peer != name and (after[peer]["Id"] != previous["Id"] or after[peer]["RestartCount"] != previous["RestartCount"]
                                         or not after[peer]["State"]["Running"]):
                        raise RuntimeError("other_service_changed:" + peer)
                stage.update(live=True, new_id=fresh["Id"], new_image=fresh["Image"],
                             preserved_runtime_spec=True, other_service_ids_preserved=True)
                record["state"] = "live" if all(item.get("live") for item in record["targets"].values()) else "partially_live"
                record["cutover"]["state"] = "verified"
                atomic(JOURNAL, record)
                print(json.dumps({"state": record["state"], "service": name, "image": fresh["Image"],
                                  "preserved_runtime_spec": True, "other_service_ids_preserved": True}))
            except BaseException:
                settings["image"] = stage["base_tag"]
                atomic(override, {"services": {name: settings}})
                run([*command, "up", "-d", "--no-deps", "--no-build", "--force-recreate", "--wait", "--wait-timeout", "120", name], timeout=180)
                record["state"] = "rollback_attempted"
                atomic(JOURNAL, record)
                raise
    finally:
        if paused:
            run(["systemctl", "start", TIMER])


if __name__ == "__main__":
    os.umask(0o077)
    if sys.argv[1] == "stage":
        stage()
    elif sys.argv[1] == "cutover":
        cutover(sys.argv[2])
    else:
        raise RuntimeError("unknown_action")
