"""Build and inspect model assignment overlays without changing running services."""

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tarfile

STATE = pathlib.Path("/var/lib/quant-company")
CURRENT = pathlib.Path("/opt/quant-company/current")
ROOT = pathlib.Path("/opt/quant-company/operator-releases/model-assignments-20261006")
JOURNAL = STATE / "releases/model-assignments-20261006-stage.json"
INVENTORY = """import hashlib,importlib.util,json,pathlib
root=pathlib.Path(importlib.util.find_spec('quant_company').origin).parent
print(json.dumps({str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}))"""


def run(args, timeout=300):
    r = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError("stage_command_failed:" + args[0])
    return r.stdout


def save(record):
    temporary = JOURNAL.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2))
    temporary.chmod(0o600)
    temporary.replace(JOURNAL)


def inventory(image):
    return json.loads(run(["docker", "run", "--rm", "--network=none", "--read-only", "--memory=256m",
                           "--entrypoint", "/app/.venv/bin/python", image, "-c", INVENTORY]))


def stage(archive, digest, commit):
    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest or len(commit) != 40:
        raise RuntimeError("stage_archive_or_commit_invalid")
    if ROOT.exists() or JOURNAL.exists():
        raise RuntimeError("stage_exists_requires_readback")
    ROOT.mkdir(parents=True, mode=0o700)
    with tarfile.open(archive) as bundle:
        if any(not (m.isfile() or m.isdir()) or pathlib.PurePosixPath(m.name).is_absolute()
               or ".." in pathlib.PurePosixPath(m.name).parts for m in bundle.getmembers()):
            raise RuntimeError("unsafe_stage_archive")
        bundle.extractall(ROOT, filter="data")
    plans = json.loads((ROOT / "plan.json").read_text())
    baseline = json.loads((ROOT / "baseline.json").read_text())
    full = json.loads(run(["docker", "inspect", *["quant-company-" + n + "-1"
                                                  for n in baseline["services"]]]))
    by_name = {r["Name"].removeprefix("/quant-company-").removesuffix("-1"): r for r in full}
    if (str(CURRENT.resolve()) != baseline["current"]
            or any(by_name[n]["Id"] != row["id"] for n, row in baseline["services"].items())
            or hashlib.sha256((STATE / "config/runtime.env").read_bytes()).hexdigest() != baseline["runtime_env_sha256"]):
        raise RuntimeError("production_baseline_changed")
    record = {"state": "staging", "commit": commit, "archive_sha256": digest,
              "current": baseline["current"], "runtime_env_sha256": baseline["runtime_env_sha256"],
              "targets": full, "images": {}}
    save(record)
    for plan in plans:
        base = plan["base_image"]
        before = json.loads(run(["docker", "image", "inspect", base]))[0]
        if inventory(base) != plan["before"]:
            raise RuntimeError("base_source_inventory_changed")
        overlay = ROOT / base.split(":")[1] / "overlay/quant_company"
        if {str(p.relative_to(overlay)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in overlay.rglob("*") if p.is_file()} != {r["file"]: r["after"] for r in plan["changed"]}:
            raise RuntimeError("overlay_manifest_mismatch")
        tag = "quant-company-model-assignments:" + commit + "-" + base[7:19]
        run(["docker", "build", "--network=none", "--pull=false", "--build-arg", "BASE_IMAGE=" + base,
             "--build-arg", "ASSIGNMENTS_COMMIT=" + commit, "-t", tag,
             "-f", str(ROOT / "Dockerfile.model-assignments"), str(overlay.parent.parent)], timeout=900)
        built = json.loads(run(["docker", "image", "inspect", tag]))[0]
        if inventory(built["Id"]) != plan["after"]:
            raise RuntimeError("built_source_delta_mismatch")
        for field in ("Env", "Cmd", "Entrypoint", "User", "WorkingDir"):
            if built["Config"][field] != before["Config"][field]:
                raise RuntimeError("base_runtime_spec_changed:" + field)
        if (built["Config"]["Labels"].get("org.opencontainers.image.revision")
                != before["Config"]["Labels"].get("org.opencontainers.image.revision")
                or built["Config"]["Labels"].get("io.quant-company.model-assignments") != commit):
            raise RuntimeError("source_provenance_labels_invalid")
        record["images"][base] = {"id": built["Id"], "tag": tag, "services": plan["services"],
                                  "changed": plan["changed"]}
        save(record)
    record["state"] = "staged"
    save(record)
    print(json.dumps({"state": "staged", "commit": commit, "images": record["images"]}))


if __name__ == "__main__":
    os.umask(0o077)
    stage(pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3])
