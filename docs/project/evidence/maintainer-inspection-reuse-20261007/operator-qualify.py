"""Compare actual frozen diagnosis input and exact source queries without inference."""
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
MANIFEST = json.loads((ROOT / "manifest.json").read_text())
WORKER = "quant-company-maintenance-1"
os.umask(0o077)

def run(args, timeout=60):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Read-only qualification failed: " + args[0])
    return result.stdout.strip()

PROBE = r'''
import copy,hashlib,json,os
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote
import psycopg
from psycopg.rows import dict_row
from quant_company.company import Company
from quant_company.config import Settings
from quant_company.db import Database
from quant_company.maintenance import investigation
from quant_company.maintenance.policy import Triage,digest
from quant_company.maintenance.runner import proposal_material

password=Path(os.environ["DATABASE_PASSWORD_FILE"]).read_text().strip()
os.environ["DATABASE_URL"]="postgresql://"+quote(os.environ.get("DATABASE_USER","company"),safe="")+":"+quote(password,safe="")+"@postgres:5432/"+quote(os.environ.get("DATABASE_NAME","quant_company"),safe="")
class ReadOnlyDatabase(Database):
    @contextmanager
    def transaction(self):
        with psycopg.connect(self.url,row_factory=dict_row,connect_timeout=5) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            try: yield conn
            finally: conn.rollback()
company=Company(Settings())
company.db=ReadOnlyDatabase(company.settings.database_url)
with company.db.transaction() as conn:
    job=conn.execute("SELECT payload,md5(to_jsonb(j)::text) AS row_md5 FROM maintenance_jobs j WHERE id=%s",("49144860-7e42-5a72-8311-d47871ccf315",)).fetchone()
    calls=conn.execute("SELECT id,request,md5(to_jsonb(c)::text) AS row_md5 FROM maintenance_calls c WHERE job_id=%s ORDER BY created_at,id",("49144860-7e42-5a72-8311-d47871ccf315",)).fetchall()
assert len(calls)==5
payload=job["payload"];mode=os.environ["QUALIFICATION_VARIANT"]
simulation={"investigation_evidence":[],"investigation_requests":[]}
rows=[];query_results=[];repository_loads=0
class Tracing:
    def __init__(self,conn):self.conn=conn
    def execute(self,query,*args):
        global repository_loads
        if query.startswith("SELECT files"):repository_loads+=1
        return self.conn.execute(query,*args)
with company.db.transaction() as conn:
    for number,call in enumerate(calls):
        original=call["request"]["prompt"]
        material=json.loads(original.split("EVIDENCE JSON:\n",1)[1])
        original_history=copy.deepcopy(material["history"])
        original_observations=copy.deepcopy(material["observations"])
        if mode=="candidate":
            material["current_implementation"]=copy.deepcopy(payload["diagnosis"])
            context=investigation.inspection_context(simulation)
            material.update(context)
            material["evidence_references"]=[payload["diagnosis"]["key"]]+[row["key"] for row in payload["diagnosis"]["source_files"]+context["investigated_code"]+material.get("external_research",[])]
            material["instructions"]=material["instructions"].replace(
                "Reuse already supplied exact-code excerpts; request only missing paths or ranges. ",
                "Use inspection_catalog to locate saved ranges. Only shown code is present in this stateless call; request omitted indexed ranges when needed. Prefer a precise path/range to repeating broad searches. Body previews may end within a line. Use shown_line_count and an overlapping smaller range to read the missing tail rather than the same broad first-hit search. ")
        shown,prompt=proposal_material(material,Triage)
        if mode=="baseline":assert prompt==original,"Baseline must exactly reproduce the reserved prompt"
        assert shown["history"]==original_history and shown["observations"]==original_observations,"Original evidence changed"
        assert len(prompt)<=88000
        rows.append({"id":call["id"],"model":call["request"]["model"],"original_prompt_characters":len(original),
                     "prepared_characters":len(prompt),"prepared_sha256":hashlib.sha256(prompt.encode()).hexdigest(),
                     "indexed_excerpts":len(shown.get("inspection_catalog",[])),
                     "shown_paths":[row["path"] for row in shown.get("investigated_code",[])],
                     "original_call_row_md5":call["row_md5"]})
        if number<len(calls)-1:
            receipt=payload["investigation_requests"][number]
            before=repository_loads
            if mode=="candidate":
                result=investigation.inspect_code(Tracing(conn),payload["snapshot"],receipt["requests"],previous=simulation["investigation_requests"])
                investigation.append_inspections(simulation,result)
            else:
                result=investigation.inspect_code(Tracing(conn),payload["snapshot"],receipt["requests"])
                simulation["investigation_evidence"].extend(result)
            simulation["investigation_requests"].append({**receipt,"evidence":result})
            query_results.append({"round":number+1,"returned_excerpts":len(result),
                                  "reused_excerpts":sum(bool(row.get("cache_hit")) for row in result),
                                  "new_repository_loads":repository_loads-before})
    after_job=conn.execute("SELECT md5(to_jsonb(j)::text) AS row_md5 FROM maintenance_jobs j WHERE id=%s",("49144860-7e42-5a72-8311-d47871ccf315",)).fetchone()
    after_calls=conn.execute("SELECT id,md5(to_jsonb(c)::text) AS row_md5 FROM maintenance_calls c WHERE job_id=%s ORDER BY created_at,id",("49144860-7e42-5a72-8311-d47871ccf315",)).fetchall()
assert after_job["row_md5"]==job["row_md5"]
assert after_calls==[{"id":r["id"],"row_md5":r["row_md5"]} for r in calls]
print(json.dumps({"variant":mode,"model_calls":0,"database_writes":0,"original_job_row_md5":job["row_md5"],
                  "original_calls_unchanged":True,"snapshot_commit":payload["snapshot"]["commit"],
                  "prompts":rows,"inspection_rounds":query_results,
                  "stored_excerpts":len(simulation["investigation_evidence"]),"repository_loads":repository_loads}))
'''

def probe(image,worker,variant):
    env=dict(pair.split("=",1) for pair in worker["Config"]["Env"])
    command=["docker","run","--rm","--pull","never","--read-only","--memory","512m","--cpus","0.5",
             "--pids-limit","128","--cap-drop","ALL","--security-opt","no-new-privileges:true",
             "--user",worker["Config"]["User"],"--tmpfs","/tmp:size=64m,mode=1777","--entrypoint","python",
             "--network",next(n for n in worker["NetworkSettings"]["Networks"] if n.endswith("_core"))]
    for pair in worker["Config"]["Env"]:
        command+=["--env",pair]
    command+=["--env","QUALIFICATION_VARIANT="+variant]
    for mount in worker["Mounts"]:
        if mount["Destination"] in {env["ROLES_FILE"],env["DATABASE_PASSWORD_FILE"]} or mount["Destination"].startswith("/etc/quant-company/"):
            assert mount["Type"]=="bind"
            command+=["--mount","type=bind,src="+mount["Source"]+",dst="+mount["Destination"]+",readonly"]
    return json.loads(run(command+[image,"-c",PROBE],timeout=120))

worker=json.loads(run(["docker","inspect",WORKER]))[0]
assert worker["Image"]==MANIFEST["base_image_id"],"Installed maintenance image changed"
module_probe="import hashlib,json;from pathlib import Path;root=Path("+repr(MANIFEST["module_root"])+");print(json.dumps({p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in "+repr(list(MANIFEST["base_module_sha256"]))+"}))"
installed=json.loads(run(["docker","exec",WORKER,"python","-c",module_probe]))
assert installed==MANIFEST["base_module_sha256"],"Installed base source changed"
for name,expected in MANIFEST["candidate_module_sha256"].items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected
baseline=probe(worker["Image"],worker,"baseline")
base_tag=worker["Config"]["Image"]
assert json.loads(run(["docker","image","inspect",base_tag]))[0]["Id"]==worker["Image"]
run(["docker","build","--network","none","--build-arg","BASE_IMAGE="+base_tag,
     "--build-arg","MODULE_ROOT="+MANIFEST["module_root"],"--build-arg","PATCH_COMMIT="+MANIFEST["patch_commit"],
     "-t",MANIFEST["target_image"],str(ROOT)],timeout=120)
image=json.loads(run(["docker","image","inspect",MANIFEST["target_image"]]))[0]
assert json.loads(run(["docker","image","inspect",base_tag]))[0]["Id"]==worker["Image"]
candidate=probe(image["Id"],worker,"candidate")
before=sum(r["prepared_characters"] for r in baseline["prompts"])
after=sum(r["prepared_characters"] for r in candidate["prompts"])
assert all(a["prepared_characters"] < b["prepared_characters"] for b,a in zip(baseline["prompts"],candidate["prompts"],strict=True)), "Every original input must decrease before activation"
receipt={"captured_at":datetime.now(UTC).isoformat(),"scope":"Actual frozen production requests and real PostgreSQL; exact source query comparison; no inference",
         "patch_commit":MANIFEST["patch_commit"],"base_image_id":worker["Image"],"previous_container_id":worker["Id"],
         "target_image":MANIFEST["target_image"],"target_image_id":image["Id"],
         "baseline":baseline,"candidate":candidate,
         "aggregate_characters":{"before":before,"after":after,"reduction_percent":(1-after/before)*100},
         "candidate_module_sha256":MANIFEST["candidate_module_sha256"],
         "compose_files":worker["Config"]["Labels"]["com.docker.compose.project.config_files"].split(","),
         "project":worker["Config"]["Labels"]["com.docker.compose.project"],
         "usage_boundary":"This is preparation/query reuse, not measured new model token usage or cache savings"}
(ROOT/"qualification.json").write_text(json.dumps(receipt,indent=2)+"\n")
print(json.dumps(receipt))
