"""Build an isolated help image from the exact active API source cohort."""

import datetime
import hashlib
import json
import pathlib
import subprocess

ROOT = pathlib.Path("/opt/quant-company/operator-releases/command-help-20261006")
CONTEXT = ROOT / "build"


def run(args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=300)
    if result.returncode:
        raise RuntimeError("staging_command_failed:" + args[0])
    return result.stdout


plan = json.loads((CONTEXT / "plan.json").read_text())
old = json.loads((ROOT / "initial-inspect.json").read_text())
if any(r["Image"] != plan["base_image"] or r["Config"]["User"] != "10001:10001" for r in old):
    raise RuntimeError("unexpected_source_cohort")
for name in plan["changed"]:
    if hashlib.sha256((CONTEXT / name).read_bytes()).hexdigest() != plan["after"][name]:
        raise RuntimeError("artifact_digest_mismatch")
dockerfile = CONTEXT / "Dockerfile"
dockerfile.write_text(dockerfile.read_text().replace("USER 10001\n", "USER 10001:10001\n"))
# Dockerfile FROM requires a reference, whereas docker inspect reports an image ID.
base_tag = "quant-company-command-help-base:" + plan["base_image"].split(":", 1)[1]
run(["docker", "tag", plan["base_image"], base_tag])
if json.loads(run(["docker", "image", "inspect", base_tag]))[0]["Id"] != plan["base_image"]:
    raise RuntimeError("base_tag_identity_mismatch")
tag = "quant-company-command-help:" + plan["commit"]
build = subprocess.run(["docker", "build", "--network", "none", "--build-arg", "BASE_IMAGE=" + base_tag,
                        "--build-arg", "HELP_COMMIT=" + plan["commit"], "-t", tag, str(CONTEXT)],
                       text=True, capture_output=True, timeout=300)
(ROOT / "build.log").write_text(build.stdout + build.stderr)
if build.returncode:
    raise RuntimeError("image_build_failed_private_log_retained")
image = json.loads(run(["docker", "image", "inspect", tag]))[0]
code = """import hashlib,importlib.util,json,pathlib
from quant_company.accounts import help_text
from quant_company.command_help import HELP_TEXT
from quant_company.model_policy import HELP_TEXT as MODEL_HELP
assert help_text('도움말')==HELP_TEXT==MODEL_HELP
assert help_text('명령어')==help_text('help')==HELP_TEXT
assert '계정 명령을 인식하지 못했습니다' in help_text('unknown')
root=pathlib.Path(importlib.util.find_spec('quant_company').origin).parent
files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
print(json.dumps({'root':str(root),'files':files,'guide_chars':len(HELP_TEXT)}))"""
probe = json.loads(run(["docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=32m",
                        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--entrypoint", "/app/.venv/bin/python",
                        image["Id"], "-c", code]))
if probe["root"] != plan["package_root"] or probe["files"] != plan["after"]:
    raise RuntimeError("built_source_inventory_mismatch")
record = {"state": "staged", "commit": plan["commit"], "base_image": plan["base_image"], "image": image["Id"], "tag": tag,
          "targets": plan["targets"], "changed_files": plan["changed"], "source_files_verified": len(probe["files"]),
          "guide_chars": probe["guide_chars"], "staged_at": datetime.datetime.now(datetime.UTC).isoformat()}
path = pathlib.Path("/var/lib/quant-company/releases/command-help-20261006-stage.json")
path.write_text(json.dumps(record, indent=2))
path.chmod(0o600)
print(json.dumps(record, indent=2))
