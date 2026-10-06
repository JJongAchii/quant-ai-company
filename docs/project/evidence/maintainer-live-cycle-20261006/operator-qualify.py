"""One-off trusted operator qualification; original SQL writes always roll back."""

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
MANIFEST = json.loads((ROOT / "manifest.json").read_text())
WORKER = "quant-company-worker-1"


def run(args, timeout=60):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Operator command failed: " + args[0])
    return result.stdout.strip()


REPLAY = r'''
import hashlib,json,os
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote
import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError
from quant_company.company import Company
from quant_company.config import Settings
from quant_company.db import Database
import quant_company.company as module

password=Path(os.environ["DATABASE_PASSWORD_FILE"]).read_text().strip()
os.environ["DATABASE_URL"]="postgresql://"+quote(os.environ.get("DATABASE_USER","company"),safe="")+":"+quote(password,safe="")+"@postgres:5432/"+quote(os.environ.get("DATABASE_NAME","quant_company"),safe="")
class RollbackDatabase(Database):
    @contextmanager
    def transaction(self):
        with psycopg.connect(self.url,row_factory=dict_row,connect_timeout=5) as conn:
            try:
                yield conn
            finally:
                conn.rollback()

company=Company(Settings())
company.db=RollbackDatabase(company.settings.database_url)
original=module.ProviderRequest
captured={}
class CapturingRequest:
    @staticmethod
    def model_validate(value):
        return original.model_validate(value)
    def __new__(cls,**kwargs):
        captured.update(kwargs)
        return original(**kwargs)
module.ProviderRequest=CapturingRequest
rows=[]
for ident in json.loads(os.environ["RECOVERY_TURN_IDS"]):
    captured.clear()
    with company.db.transaction() as conn:
        before=conn.execute("SELECT status,attempts,request,response FROM turns WHERE id=%s",(ident,)).fetchone()
        if not before or before["status"]!="queued" or before["attempts"] or before["request"] or before["response"]:
            raise ValueError("Original unreserved turn changed before qualification")
    try:
        prepared=company.prepare_turn(ident)
        state=prepared["state"]
        error=None
    except ValidationError as exc:
        state="validation_error"
        error=[entry["msg"] for entry in exc.errors(include_input=False,include_url=False)]
    prompt=captured.get("prompt","")
    context=json.loads(prompt.split("TASK DATA JSON:\n",1)[1]) if "TASK DATA JSON:\n" in prompt else {}
    rows.append({"turn_id":ident,"state":state,"error":error,"prompt_characters":len(prompt),"context_characters":len(json.dumps(context,ensure_ascii=False)),"header_characters":len(prompt)-len(json.dumps(context,ensure_ascii=False)),"context_truncated":context.get("context_truncated"),"instruction_sha256":hashlib.sha256(context.get("task",{}).get("instruction","").encode()).hexdigest(),"protected_requested_sources":[s["id"] for s in context.get("approved_sources",[]) if s["id"] in context.get("task",{}).get("instruction","")],"original_output_contract":captured.get("output_contract"),"model":captured.get("model"),"reasoning_effort":captured.get("reasoning_effort")})
    with company.db.transaction() as conn:
        after=conn.execute("SELECT status,attempts,request,response FROM turns WHERE id=%s",(ident,)).fetchone()
        assert before==after,"Preparation qualification committed a write"
print(json.dumps({"sql_writes":"rolled_back","model_calls":0,"turns":rows}))
'''


def replay(image, worker):
    env = dict(pair.split("=", 1) for pair in worker["Config"]["Env"])
    command = ["docker", "run", "--rm", "--pull", "never", "--read-only", "--memory", "512m",
               "--cpus", "0.5", "--pids-limit", "128", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges:true", "--user", worker["Config"]["User"],
               "--tmpfs", "/tmp:size=64m,mode=1777", "--entrypoint", "python"]
    network = next(name for name in worker["NetworkSettings"]["Networks"] if name.endswith("_core"))
    command += ["--network", network]
    for pair in worker["Config"]["Env"]:
        command += ["--env", pair]
    command += ["--env", "RECOVERY_TURN_IDS=" + json.dumps(MANIFEST["turn_ids"])]
    targets = {env["ROLES_FILE"], env["DATABASE_PASSWORD_FILE"]}
    for mount in worker["Mounts"]:
        if mount["Destination"] in targets or mount["Destination"].startswith("/etc/quant-company/"):
            assert mount["Type"] == "bind"
            command += ["--mount", "type=bind,src=" + mount["Source"] + ",dst=" + mount["Destination"] + ",readonly"]
    return json.loads(run(command + [image, "-c", REPLAY], timeout=90))


os.umask(0o077)
worker = json.loads(run(["docker", "inspect", WORKER]))[0]
assert worker["Image"] == MANIFEST["base_image_id"], "Production worker image changed"
baseline = replay(worker["Image"], worker)
assert all(row["state"] == "validation_error" and row["prompt_characters"] > 90000 for row in baseline["turns"])
for name, expected in MANIFEST["candidate_module_sha256"].items():
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
run(["docker", "build", "--network", "none", "--build-arg", "BASE_IMAGE=" + worker["Image"],
     "--build-arg", "MODULE_ROOT=" + MANIFEST["module_root"], "--build-arg", "PATCH_COMMIT=" + MANIFEST["patch_commit"],
     "-t", MANIFEST["target_image"], str(ROOT)], timeout=120)
image = json.loads(run(["docker", "image", "inspect", MANIFEST["target_image"]]))[0]
candidate = replay(image["Id"], worker)
assert all(row["state"] == "ready" and row["prompt_characters"] <= 90000 for row in candidate["turns"])
for before, after in zip(baseline["turns"], candidate["turns"], strict=True):
    for key in ("turn_id", "instruction_sha256", "protected_requested_sources", "original_output_contract", "model", "reasoning_effort"):
        assert before[key] == after[key], "Frozen task authority changed: " + key
receipt = {
    "captured_at": datetime.now(UTC).isoformat(), "scope": "Actual production configuration and PostgreSQL; rolled-back preparation; no model calls",
    "patch_commit": MANIFEST["patch_commit"], "base_image_id": worker["Image"], "previous_container_id": worker["Id"],
    "target_image": MANIFEST["target_image"], "target_image_id": image["Id"], "baseline": baseline, "candidate": candidate,
    "candidate_module_sha256": MANIFEST["candidate_module_sha256"],
    "compose_files": worker["Config"]["Labels"]["com.docker.compose.project.config_files"].split(","),
    "working_dir": worker["Config"]["Labels"]["com.docker.compose.project.working_dir"],
    "project": worker["Config"]["Labels"]["com.docker.compose.project"],
}
(ROOT / "qualification.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps(receipt, indent=2))
