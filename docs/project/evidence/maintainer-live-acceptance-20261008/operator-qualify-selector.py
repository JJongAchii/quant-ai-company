"""Qualify the full review path in the exact deployed image; read-only PostgreSQL."""
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
M = json.loads((ROOT / "manifest.json").read_text())
os.umask(0o077)

def run(args, timeout=90):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # Probe traceback is useful; no environment dump or database URL is emitted.
        print(result.stderr[-3200:], file=sys.stderr)
        raise RuntimeError("Qualification failed: " + args[0])
    return result.stdout.strip()

PROBE = r"""
import hashlib,json,os,time,types
from datetime import UTC,datetime
from pathlib import Path
from urllib.parse import quote
import psycopg
from psycopg.rows import dict_row
from quant_company.company import Company
from quant_company.config import Settings
from quant_company.maintenance import observation
settings=Settings()
company=Company(settings)
old=types.ModuleType("quant_company.maintenance.frozen_observation")
old.__package__="quant_company.maintenance"
source=Path("/opt/maintainer-baseline-observation.py").read_bytes()
exec(compile(source,"/opt/maintainer-baseline-observation.py","exec"),old.__dict__)
password=Path(os.environ["DATABASE_PASSWORD_FILE"]).read_text().strip()
url="postgresql://"+quote(os.environ.get("DATABASE_USER","company"),safe="")+":"+quote(password,safe="")+"@postgres:5432/"+quote(os.environ.get("DATABASE_NAME","quant_company"),safe="")
at=datetime.now(UTC)
with psycopg.connect(url,row_factory=dict_row,connect_timeout=5) as conn:
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    jobs=conn.execute("SELECT id,state,payload,md5(to_jsonb(j)::text) digest FROM maintenance_jobs j WHERE id=ANY(%s::uuid[]) ORDER BY id",(["672a94ca-b8e9-51b7-8fb0-354b96c7f355","c7d07401-ef8a-5d16-96f5-b6048b16dd1b"],)).fetchall()
    case2=next(j for j in jobs if str(j["id"])=="c7d07401-ef8a-5d16-96f5-b6048b16dd1b")
    owners=case2["payload"]["owners"]
    calls=conn.execute("SELECT id,job_id,md5(to_jsonb(c)::text) digest FROM maintenance_calls c WHERE job_id=ANY(%s::uuid[]) ORDER BY id",([str(j["id"]) for j in jobs],)).fetchall()
    conn.execute("SAVEPOINT baseline_limit")
    conn.execute("SET LOCAL statement_timeout='5s'")
    start=time.perf_counter()
    limited=False
    try:
        old.review_snapshot(conn,company,owners,at)
    except psycopg.errors.QueryCanceled:
        limited=True
        conn.execute("ROLLBACK TO SAVEPOINT baseline_limit")
    limit_seconds=time.perf_counter()-start
    assert limited,"The exact installed module must reproduce the five-second failure"
    conn.execute("SET LOCAL statement_timeout='60s'")
    start=time.perf_counter()
    baseline=old.review_snapshot(conn,company,owners,at)
    baseline_seconds=time.perf_counter()-start
    conn.execute("SET LOCAL statement_timeout='5s'")
    start=time.perf_counter()
    candidate=observation.review_snapshot(conn,company,owners,at)
    candidate_seconds=time.perf_counter()-start
    assert candidate==baseline,"Full history, replay bodies, evidence, roster and digest must match"
    assert observation.MAX_REPLAY_REQUEST_BYTES==old.MAX_REPLAY_REQUEST_BYTES==600000
    after=conn.execute("SELECT id,md5(to_jsonb(j)::text) digest FROM maintenance_jobs j WHERE id=ANY(%s::uuid[]) ORDER BY id",([str(j["id"]) for j in jobs],)).fetchall()
    after_calls=conn.execute("SELECT id,job_id,md5(to_jsonb(c)::text) digest FROM maintenance_calls c WHERE job_id=ANY(%s::uuid[]) ORDER BY id",([str(j["id"]) for j in jobs],)).fetchall()
    assert after==[{"id":j["id"],"digest":j["digest"]} for j in jobs] and after_calls==calls
    print(json.dumps({"captured_at":at.isoformat(),"baseline_timed_out_at_five_seconds":limited,
        "baseline_limit_seconds":limit_seconds,"baseline_seconds":baseline_seconds,
        "candidate_seconds":candidate_seconds,"full_review_exactly_equal":True,
        "sampling":candidate[0]["sampling"],"replay_inputs":len(candidate[1]),
        "review_digest":candidate[2],"request_max_bytes_unchanged":True,
        "source_sha256":{"baseline":hashlib.sha256(source).hexdigest(),
            "candidate":hashlib.sha256(Path(observation.__file__).read_bytes()).hexdigest()},
        "jobs":[{"id":str(j["id"]),"state":j["state"],"digest":j["digest"]} for j in jobs],
        "original_calls":len(calls),"jobs_and_calls_unchanged_in_read_only_snapshot":True,
        "model_calls":0,"database_writes":0}))
    conn.rollback()
"""
target=M["target"]
container=json.loads(run(["docker","inspect",target["name"]]))[0]
assert container["Id"]==target["id"] and container["Image"]==target["base_image_id"]
base=json.loads(run(["docker","image","inspect",target["image_tag"]]))[0]
assert base["Id"]==container["Image"]
module="/app/.venv/lib/python3.12/site-packages/quant_company/maintenance/observation.py"
runner=module.replace("observation.py","runner.py")
read="from pathlib import Path;print(Path("+repr(module)+").read_text(),end='')"
frozen=run(["docker","run","--rm","--pull","never","--network","none","--read-only","--entrypoint","python",base["Id"],"-c",read])+"\n"
assert hashlib.sha256(frozen.encode()).hexdigest()==M["base_observation_sha256"]
(ROOT/"payload"/"baseline_observation.py").write_text(frozen)
assert hashlib.sha256((ROOT/"payload"/"observation.py").read_bytes()).hexdigest()==M["candidate_observation_sha256"]
check="import hashlib;from pathlib import Path;print(hashlib.sha256(Path("+repr(runner)+").read_bytes()).hexdigest())"
assert run(["docker","run","--rm","--pull","never","--network","none","--read-only","--entrypoint","python",base["Id"],"-c",check])==M["preserved_runner_sha256"]
(ROOT/"Dockerfile").write_text("FROM "+target["image_tag"]+"\nCOPY --chmod=0644 payload/observation.py "+module+"\nCOPY --chmod=0644 payload/baseline_observation.py /opt/maintainer-baseline-observation.py\n")
run(["docker","build","--network","none","-f",str(ROOT/"Dockerfile"),"-t",M["target_image"],str(ROOT)],timeout=180)
candidate=json.loads(run(["docker","image","inspect",M["target_image"]]))[0]
for field in ("User","Entrypoint","Cmd","WorkingDir","Env"):
    assert candidate["Config"][field]==base["Config"][field]
env=dict(pair.split("=",1) for pair in container["Config"]["Env"])
cmd=["docker","run","--rm","--pull","never","--read-only","--memory","512m","--cpus","0.5","--pids-limit","128","--cap-drop","ALL","--security-opt","no-new-privileges:true","--user",container["Config"]["User"],"--tmpfs","/tmp:size=64m,mode=1777","--entrypoint","python","--network",next(n for n in container["NetworkSettings"]["Networks"] if n.endswith("_core"))]
for pair in container["Config"]["Env"]:
    cmd+=["--env",pair]
for mount in container["Mounts"]:
    if mount["Destination"] in {env["ROLES_FILE"],env["DATABASE_PASSWORD_FILE"]} or mount["Destination"].startswith("/etc/quant-company/"):
        assert mount["Type"]=="bind"
        cmd+=["--mount","type=bind,src="+mount["Source"]+",dst="+mount["Destination"]+",readonly"]
comparison=json.loads(run(cmd+[candidate["Id"],"-c",PROBE],timeout=180))
assert comparison["source_sha256"]["baseline"]==M["base_observation_sha256"] and comparison["source_sha256"]["candidate"]==M["candidate_observation_sha256"]
receipt={"captured_at":datetime.now(UTC).isoformat(),"patch_commit":M["patch_commit"],"target":target,
    "target_image":M["target_image"],"target_image_id":candidate["Id"],
    "base_observation_sha256":M["base_observation_sha256"],"candidate_observation_sha256":M["candidate_observation_sha256"],
    "preserved_runner_sha256":M["preserved_runner_sha256"],"comparison":comparison,
    "image_execution_configuration_unchanged":True,"model_calls":0,"database_writes":0}
(ROOT/"qualification.json").write_text(json.dumps(receipt,indent=2)+"\n")
print(json.dumps(receipt))

