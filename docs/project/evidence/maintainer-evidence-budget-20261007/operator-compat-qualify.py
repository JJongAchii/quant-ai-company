"""Qualify the main role compatibility guard on the pinned worker; no model calls."""
import ast
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
WORKER = "quant-company-worker-1"
TURN = "e2988906-53c9-532b-acad-4afa31c0ca98"
EXPECTED = "57b2ce59bd146c60ad4bc6ff33d0e91672bc7593a71d24c232f7ddec3ede9899"
PATCH = "main-role-compat-20261007"
os.umask(0o077)

def run(args, timeout=60):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Compatibility qualification command failed: " + args[0])
    return result.stdout.strip()

worker = json.loads(run(["docker", "inspect", WORKER]))[0]
module_path = run(["docker", "exec", WORKER, "python", "-c",
                  "import quant_company.company as c;print(c.__file__)"])
original = run(["docker", "exec", WORKER, "python", "-c",
                "from pathlib import Path;print(Path(" + repr(module_path) + ").read_text(),end='')"]) + "\n"
# run() strips trailing newlines; the production Python file has one final newline.
assert hashlib.sha256(original.encode()).hexdigest() == EXPECTED, "Installed source changed"
replacements = [
    ("from .staff.packs import pack\n", "from .staff.packs import STAFF, pack\n"),
    ('if name != TECH_FEED_AGENT else role', 'if name not in {TECH_FEED_AGENT, "trend_scout"} else role'),
    ('"specialist_pack_version": pack(role.id)["version"],',
     '"specialist_pack_version": pack(role.id)["version"] if role.id in (*STAFF, "reporter") else None,'),
    ('"specialist_pack_digest": pack(role.id)["digest"]}',
     '"specialist_pack_digest": pack(role.id)["digest"] if role.id in (*STAFF, "reporter") else None}'),
    ('if role.id not in {TECH_FEED_AGENT, "quant_scout"}',
     'if role.id not in {TECH_FEED_AGENT, "quant_scout", "trend_scout"}'),
]
candidate = original
for before, after in replacements:
    assert candidate.count(before) == 1, "Expected main compatibility hunk changed"
    candidate = candidate.replace(before, after)
ast.parse(candidate)
(ROOT / "company.py").write_text(candidate)
(ROOT / "company.py").chmod(0o644)
sha = hashlib.sha256(candidate.encode()).hexdigest()
tag = "quant-company-worker-role-compat:" + sha[:16]
(ROOT / "Dockerfile").write_text(
    "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\nARG MODULE_ROOT\nCOPY company.py ${MODULE_ROOT}/company.py\n"
    'LABEL quant-company.turn-context-patch="' + PATCH + '"\n')
base_tag = worker["Config"]["Image"]
assert json.loads(run(["docker", "image", "inspect", base_tag]))[0]["Id"] == worker["Image"]
run(["docker", "build", "--network", "none", "--build-arg", "BASE_IMAGE=" + base_tag,
     "--build-arg", "MODULE_ROOT=" + str(Path(module_path).parent), "-t", tag, str(ROOT)], timeout=120)
image = json.loads(run(["docker", "image", "inspect", tag]))[0]
assert json.loads(run(["docker", "image", "inspect", base_tag]))[0]["Id"] == worker["Image"]
PROBE = r'''
import hashlib,json,os
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote
import psycopg
from psycopg.rows import dict_row
from quant_company.company import Company
from quant_company.config import Settings
from quant_company.db import Database

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
ident=os.environ["COMPAT_TURN_ID"]
with company.db.transaction() as conn:
    row=conn.execute("SELECT t.status,t.attempts,t.request,t.response,k.instruction,k.agent FROM turns t JOIN tasks k ON k.id=t.task_id WHERE t.id=%s",(ident,)).fetchone()
assert row and row["status"]=="queued" and row["attempts"]==0 and row["request"] is None and row["response"] is None
try:
    result=company.prepare_turn(ident)
    request=result.get("request") or {}
    prepared={"state":result["state"],"prompt_characters":len(request.get("prompt","")),"model":request.get("model"),"reasoning_effort":request.get("reasoning_effort")}
except Exception as exc:
    if hasattr(exc,"errors"):
        error=[{"loc":e["loc"],"type":e["type"],"msg":e["msg"]} for e in exc.errors(include_input=False,include_url=False)]
    else:
        error=str(exc)[:240]
    prepared={"state":"preparation_error","error_type":type(exc).__name__,"error":error}
with company.db.transaction() as conn:
    after=conn.execute("SELECT t.status,t.attempts,t.request,t.response,k.instruction,k.agent FROM turns t JOIN tasks k ON k.id=t.task_id WHERE t.id=%s",(ident,)).fetchone()
assert row==after,"Qualification committed changes"
print(json.dumps({"sql_writes":"rolled_back","model_calls":0,"turn_id":ident,"instruction_sha256":hashlib.sha256(row["instruction"].encode()).hexdigest(),"agent":row["agent"],**prepared}))
'''

def probe(image_id):
    env = dict(pair.split("=",1) for pair in worker["Config"]["Env"])
    args=["docker","run","--rm","--pull","never","--read-only","--memory","512m","--cpus","0.5",
          "--pids-limit","128","--cap-drop","ALL","--security-opt","no-new-privileges:true",
          "--user",worker["Config"]["User"],"--tmpfs","/tmp:size=64m,mode=1777",
          "--entrypoint","python","--network",next(n for n in worker["NetworkSettings"]["Networks"] if n.endswith("_core"))]
    for pair in worker["Config"]["Env"]:
        args += ["--env",pair]
    args += ["--env","COMPAT_TURN_ID="+TURN]
    for mount in worker["Mounts"]:
        if mount["Destination"] in {env["ROLES_FILE"],env["DATABASE_PASSWORD_FILE"]} or mount["Destination"].startswith("/etc/quant-company/"):
            assert mount["Type"]=="bind"
            args += ["--mount","type=bind,src="+mount["Source"]+",dst="+mount["Destination"]+",readonly"]
    return json.loads(run(args+[image_id,"-c",PROBE],timeout=90))

baseline=probe(worker["Image"])
candidate_result=probe(image["Id"])
print(json.dumps({"baseline":baseline,"candidate":candidate_result}),flush=True)
assert baseline["state"]=="preparation_error"
assert candidate_result["state"]=="ready" and 0<candidate_result["prompt_characters"]<=90000
assert baseline["instruction_sha256"]==candidate_result["instruction_sha256"]
receipt={"captured_at":datetime.now(UTC).isoformat(),"patch_commit":PATCH,
         "scope":"Main role compatibility guard on exact installed pinned worker; real PostgreSQL rollback; zero model calls",
         "source_main_commit":"b981ad7e282fb1ead9d6663172f83883478aa225",
         "previous_container_id":worker["Id"],"base_image_id":worker["Image"],"target_image":tag,"target_image_id":image["Id"],
         "base_company_sha256":EXPECTED,"candidate_company_sha256":sha,
         "baseline":baseline,"candidate":candidate_result,
         "compose_files":worker["Config"]["Labels"]["com.docker.compose.project.config_files"].split(","),
         "project":worker["Config"]["Labels"]["com.docker.compose.project"]}
(ROOT/"qualification.json").write_text(json.dumps(receipt,indent=2)+"\n")
print(json.dumps(receipt))
