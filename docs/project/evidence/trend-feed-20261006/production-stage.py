"""Build a candidate image and compare inventories; never restart a live service."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

STATE = Path("/var/lib/quant-company")
CURRENT = Path("/opt/quant-company/current")
ROOT = Path("/opt/quant-company/operator-releases/trend-feed-20261006")
JOURNAL = STATE / "releases/trend-feed-20261006-stage.json"
INVENTORY = """import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1])
print(json.dumps({str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}))"""


def run(args, timeout=90):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("stage_command_failed:" + args[0])
    return result.stdout


def save(record):
    temp = JOURNAL.with_suffix(".tmp")
    temp.write_text(json.dumps(record, indent=2) + "\n")
    temp.chmod(0o600)
    temp.replace(JOURNAL)


def inventory(image, package_root):
    return json.loads(run(["docker", "run", "--rm", "--network=none", "--read-only", "--memory=256m",
                           "--entrypoint", "python", image, "-c", INVENTORY, package_root]))


def stage(archive, digest, commit):
    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest or len(commit) != 40:
        raise RuntimeError("invalid_stage_archive_or_commit")
    if ROOT.exists() or JOURNAL.exists():
        raise RuntimeError("stage_already_exists_requires_readback")
    if shutil.disk_usage("/var/lib/docker").free < 1024**3:
        raise RuntimeError("insufficient_disk_headroom")
    ROOT.mkdir(parents=True, mode=0o700)
    with tarfile.open(archive) as bundle:
        if any(not (m.isfile() or m.isdir()) or m.name.startswith("/") or ".." in Path(m.name).parts
               for m in bundle.getmembers()):
            raise RuntimeError("unsafe_stage_archive")
        if sum(m.size for m in bundle.getmembers()) > 16 * 1024 * 1024:
            raise RuntimeError("oversized_stage_archive")
        bundle.extractall(ROOT, filter="data")
    baseline = json.loads((ROOT / "baseline.json").read_text())
    plans = json.loads((ROOT / "plan.json").read_text())
    targets = {r["name"]: r for r in baseline["services"]
               if r["name"] in ("/quant-company-news-worker-1", "/quant-company-dispatch-1")}
    full = json.loads(run(["docker", "inspect", *targets]))
    if (str(CURRENT.resolve()) != baseline["current_release"]
            or hashlib.sha256((STATE / "config/runtime.env").read_bytes()).hexdigest() != baseline["runtime_env_sha256"]
            or any(row["Id"] != targets[row["Name"]]["id"] or row["Image"] != targets[row["Name"]]["image_id"]
                   or not row["State"]["Running"] for row in full)):
        raise RuntimeError("production_baseline_changed")
    record = {"state": "staging", "checked_at": time.time(), "commit": commit,
              "archive_sha256": digest, "current": baseline["current_release"],
              "runtime_env_sha256": baseline["runtime_env_sha256"], "targets": targets, "images": {},
              "running_services_changed": False, "credentials_installed": False, "messages_sent": 0}
    save(record)
    for plan in plans:
        base = plan["base_image"]
        before = json.loads(run(["docker", "image", "inspect", base]))[0]
        if inventory(base, plan["package_root"]) != plan["before"]:
            raise RuntimeError("base_source_inventory_changed")
        context = ROOT / base.removeprefix("sha256:")
        overlay = context / "overlay/quant_company"
        incoming = {str(p.relative_to(overlay)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in overlay.rglob("*") if p.is_file()}
        if incoming != {r["file"]: r["after"] for r in plan["changed"]}:
            raise RuntimeError("overlay_manifest_mismatch")
        base_tag = "quant-company-trend-base:" + base.removeprefix("sha256:")
        run(["docker", "tag", base, base_tag])
        if json.loads(run(["docker", "image", "inspect", base_tag]))[0]["Id"] != base:
            raise RuntimeError("base_tag_changed")
        tag = "quant-company-trend:" + commit + "-" + base[7:19]
        run(["docker", "build", "--network=none", "--pull=false", "--build-arg", "BASE_IMAGE=" + base_tag,
             "--build-arg", "BASE_IMAGE_ID=" + base, "--build-arg", "PACKAGE_ROOT=" + plan["package_root"],
             "--build-arg", "TREND_COMMIT=" + commit, "-t", tag, "-f", str(ROOT / "Dockerfile.trend-feed"),
             str(context)], timeout=300)
        built = json.loads(run(["docker", "image", "inspect", tag]))[0]
        if inventory(built["Id"], plan["package_root"]) != plan["after"]:
            raise RuntimeError("built_source_inventory_mismatch")
        if any(built["Config"][key] != before["Config"][key]
               for key in ("Env", "Cmd", "Entrypoint", "User", "WorkingDir")):
            raise RuntimeError("image_runtime_config_changed")
        labels = built["Config"]["Labels"]
        if (labels.get("org.opencontainers.image.revision") != before["Config"]["Labels"].get("org.opencontainers.image.revision")
                or labels.get("io.quant-company.trend-feed") != commit):
            raise RuntimeError("source_provenance_changed")
        record["images"][base] = {"id": built["Id"], "tag": tag, "services": plan["services"],
                                  "package_root": plan["package_root"], "changed": plan["changed"],
                                  "inventory_verified": True, "runtime_config_preserved": True}
        save(record)
    record["state"] = "staged"
    save(record)
    print(json.dumps(record))


if __name__ == "__main__":
    os.umask(0o077)
    stage(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
