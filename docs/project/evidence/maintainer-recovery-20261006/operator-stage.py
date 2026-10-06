import hashlib
import json
import os
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

COMMIT = "fadc0e54be0c470068bb18011cc6a1a9ec8ee971"
BASE = "quant-company-maintenance:837ac72a76cf94625afa2b4ccbee5eb103ae6e03"
BASE_ID = "sha256:50124cabdc3db809bfc799e14aa9af1c155d86b6a577f910d18b49d6e5f226f5"
IMAGE = "quant-company-maintenance:" + COMMIT
ARCHIVE = Path("/tmp/maintainer-recovery-20261006-source.tar.gz")
ROOT = Path("/opt/quant-company/operator-releases") / ("maintainer-" + COMMIT)
JOURNAL = Path("/var/lib/quant-company/releases") / ("maintainer-recovery-" + COMMIT + "-stage.json")


def run(args):
    r = subprocess.run(args, capture_output=True, timeout=600)
    if r.returncode:
        raise RuntimeError("operator stage failed: " + args[0] + " " + r.stderr.decode()[-1200:])
    return r.stdout.decode()


def sha(b):
    return hashlib.sha256(b).hexdigest()


os.umask(0o077)
if JOURNAL.exists():
    print(JOURNAL.read_text())
    raise SystemExit(0)
assert sha(ARCHIVE.read_bytes()) == "4930e9ab3069bb662b2225e9e510641c146d3032364dc151efa2256ab052992f", (
    "archive mismatch"
)
current = json.loads(run(["docker", "inspect", "quant-company-maintenance-1"]))[0]
assert current["Config"]["Image"] == BASE and current["Image"] == BASE_ID, "maintainer changed"
assert json.loads(run(["docker", "image", "inspect", BASE]))[0]["Id"] == BASE_ID, "base changed"
ROOT.mkdir(parents=True, exist_ok=True)
with tarfile.open(ARCHIVE, "r:gz") as tar:
    for item in tar.getmembers():
        name = PurePosixPath(item.name)
        assert not name.is_absolute() and ".." not in name.parts and (item.isfile() or item.isdir()), (
            "unsafe archive"
        )
        path = ROOT.joinpath(*name.parts)
        if item.isdir():
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            content = tar.extractfile(item).read()
            if path.exists():
                assert path.read_bytes() == content, "staged source changed: " + item.name
            else:
                path.write_bytes(content)
            path.chmod(0o644)
base_source = Path("/opt/quant-company/releases/837ac72a76cf94625afa2b4ccbee5eb103ae6e03")
fixed = {}
for name in ("pyproject.toml", "uv.lock", "deploy/entrypoint.py", "deploy/qdata-source.json"):
    a = (base_source / name).read_bytes()
    b = (ROOT / name).read_bytes()
    assert a == b, "fixed build input changed: " + name
    fixed[name] = sha(b)
for name in ("pyproject.toml", "uv.lock", "entrypoint.py"):
    installed = run(["docker", "exec", "quant-company-maintenance-1", "cat", "/opt/company/" + name]).encode()
    expected = (ROOT / ("deploy/" + name if name == "entrypoint.py" else name)).read_bytes()
    assert installed == expected, "installed build input mismatch: " + name
labels = current["Config"]["Labels"]
files = labels["com.docker.compose.project.config_files"].split(",")
envfile = labels["com.docker.compose.project.environment_file"]
project = labels["com.docker.compose.project"]
command = ["docker", "compose", "--project-name", project, "--profile", "maintenance", "--env-file", envfile]
for name in files:
    command += ["-f", name]
definition = json.loads(run(command + ["config", "--format", "json"]))["services"]["maintenance"]
old_env = dict(pair.split("=", 1) for pair in current["Config"]["Env"])
env_changes = sorted(k for k, v in definition.get("environment", {}).items() if old_env.get(k) != str(v))
assert set(env_changes) <= {"QUANT_FEED_PUBLISH_ENABLED", "SLACK_ALLOWED_CHANNELS"}, (
    "unexpected runtime environment drift: " + ",".join(env_changes)
)
override = Path("/var/lib/quant-company/config") / ("maintainer-recovery-" + COMMIT + ".compose.json")
overlay = {
    "services": {
        "maintenance": {
            "image": IMAGE,
            "environment": {"COMPANY_CODE_COMMIT": COMMIT, **{k: old_env[k] for k in env_changes}},
        }
    }
}
if override.exists():
    assert json.loads(override.read_text()) == overlay, "operator overlay changed"
else:
    override.write_text(json.dumps(overlay, indent=2) + "\n")
command += ["-f", str(override)]
definition = json.loads(run(command + ["config", "--format", "json"]))["services"]["maintenance"]
remaining = sorted(
    k
    for k, v in definition.get("environment", {}).items()
    if k != "COMPANY_CODE_COMMIT" and old_env.get(k) != str(v)
)
assert not remaining, "runtime environment drift after preservation: " + ",".join(remaining)
assert definition["user"] == current["Config"]["User"], "runtime uid changed"
run(
    [
        "docker",
        "build",
        "--file",
        str(ROOT / "deploy/Dockerfile.code-update"),
        "--build-arg",
        "BASE_IMAGE=" + BASE,
        "--build-arg",
        "RELEASE_COMMIT=" + COMMIT,
        "--tag",
        IMAGE,
        str(ROOT),
    ]
)
smoke = """import json,os,inspect,hashlib
from quant_company.contracts import ProviderRequest
from quant_company.maintenance.policy import Triage
from quant_company.maintenance.runner import proposal_material
from quant_company.maintenance.problems import failure_observations
paths=["src/quant_company/example_"+str(i)+".py" for i in range(3000)]
evidence={"observations":[{"key":"qualification:no-inference","text":"response contract failed"}],"editable_paths":paths,"repository_paths":paths,"history":{},"current_implementation":{"source_files":[{"path":paths[-1],"content":"pass"}]}}
material,prompt=proposal_material(evidence,Triage)
ProviderRequest(request_id="qualification-no-inference",model="fixture",prompt=prompt)
assert len(prompt)<=88000 and paths[-1] in material["editable_paths"]
assert os.environ["COMPANY_CODE_COMMIT"]=="fadc0e54be0c470068bb18011cc6a1a9ec8ee971"
print(json.dumps({"commit":os.environ["COMPANY_CODE_COMMIT"],"provider_request_validated":True,"prompt_characters":len(prompt),"problems_module_sha256":hashlib.sha256(inspect.getsource(failure_observations).encode()).hexdigest(),"model_calls":0,"network":"none"}))
"""
assert json.loads(run(["docker", "image", "inspect", BASE]))[0]["Id"] == BASE_ID, (
    "base image changed during build"
)
smoke_result = json.loads(
    run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--entrypoint",
            "/app/.venv/bin/python",
            IMAGE,
            "-c",
            smoke,
        ]
    )
)
image = json.loads(run(["docker", "image", "inspect", IMAGE]))[0]
record = {
    "captured_at": datetime.now(UTC).isoformat(),
    "phase": "image_qualified_not_activated",
    "commit": COMMIT,
    "archive_sha256": sha(ARCHIVE.read_bytes()),
    "base_image": BASE,
    "base_image_id": BASE_ID,
    "target_image": IMAGE,
    "target_image_id": image["Id"],
    "fixed_inputs_sha256": fixed,
    "reviewed_build_recipe_sha256": sha((ROOT / "deploy/Dockerfile.code-update").read_bytes()),
    "existing_runtime_environment_equal_after_overlay": True,
    "preserved_environment_keys": env_changes,
    "override": str(override),
    "compose_files": files,
    "envfile": envfile,
    "project": project,
    "previous_container_id": current["Id"],
    "smoke": smoke_result,
    "source": str(ROOT),
}
JOURNAL.parent.mkdir(parents=True, exist_ok=True)
JOURNAL.write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps(record, indent=2))
