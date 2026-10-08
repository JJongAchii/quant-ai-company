"""Qualify exact installed-image overlays against frozen production evidence, without inference."""
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
MANIFEST = json.loads((ROOT / "manifest.json").read_text())
os.umask(0o077)


def run(args, timeout=90):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Qualification command failed: " + args[0])
    return result.stdout.strip()


PROBE = r"""
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
from quant_company.maintenance.policy import Triage
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
job_id="c44f74be-7d4d-4aa1-a789-a4edd1ee5374"
with company.db.transaction() as conn:
    job=conn.execute("SELECT payload,md5(to_jsonb(j)::text) AS row_md5 FROM maintenance_jobs j WHERE id=%s",(job_id,)).fetchone()
    calls=conn.execute("SELECT id,request,response,md5(to_jsonb(c)::text) AS row_md5 FROM maintenance_calls c WHERE job_id=%s ORDER BY created_at,id",(job_id,)).fetchall()
assert len(calls)==5
payload=job["payload"];mode=os.environ["QUALIFICATION_VARIANT"]
simulation={"investigation_evidence":[],"investigation_requests":[]}
rows=[]
def numbered(shown):
    return {(r["path"],line) for r in shown.get("investigated_code",[]) for line in r.get("content","").splitlines()}
with company.db.transaction() as conn:
    for number,call in enumerate(calls):
        original=call["request"]["prompt"]
        material=json.loads(original.split("EVIDENCE JSON:\n",1)[1])
        history=copy.deepcopy(material["history"])
        observations=copy.deepcopy(material["observations"])
        if mode=="candidate":
            material["current_implementation"]=copy.deepcopy(payload["diagnosis"])
            material.update(investigation.inspection_context(simulation))
            material["evidence_references"]=[payload["diagnosis"]["key"]]+[r["key"] for r in payload["diagnosis"]["source_files"]+material["investigated_code"]+material.get("external_research",[])]
        shown,prompt=proposal_material(material,Triage)
        if mode=="baseline":
            assert prompt==original,"Installed baseline must exactly reproduce the reserved prompt"
        assert shown["history"]==history and shown["observations"]==observations
        assert len(prompt)<=88000
        visible=numbered(shown)
        raw={(r["path"],line) for r in simulation["investigation_evidence"] for line in r.get("content","").splitlines()}
        row={"id":call["id"],"original_characters":len(original),"prepared_characters":len(prompt),
             "prepared_sha256":hashlib.sha256(prompt.encode()).hexdigest(),"stored_numbered_lines":len(raw),
             "visible_numbered_lines":len(visible),"retained_original_lines":len(raw&visible),
             "shown_bodies":len(shown.get("investigated_code",[])),"original_row_md5":call["row_md5"]}
        if number==len(calls)-1 and mode=="candidate":
            proposals=call["response"]["decision"]["artifacts"]
            last=json.loads(proposals[0]["content"])
            recovered=[]
            for query in last["inspect"]:
                if query.get("query"):continue
                start=query["start_line"];end=start+query["line_count"]
                expected={(path,line) for path,line in raw if path==query["path"] and start<=int(line.split(":",1)[0])<end}
                # The final diagnosis asked for previously read ranges because their bodies were dropped.
                assert expected and expected<=visible,"An already read final requested range is still absent"
                recovered.append({"path":query["path"],"start_line":start,"previously_read_lines":len(expected)})
            assert len(recovered)==4
            row["final_repeated_ranges_present"]=recovered
        rows.append(row)
        if number<len(calls)-1:
            receipt=payload["investigation_requests"][number]
            if mode=="candidate":
                result=investigation.inspect_code(conn,payload["snapshot"],receipt["requests"],previous=simulation["investigation_requests"])
                investigation.append_inspections(simulation,result)
            else:
                result=investigation.inspect_code(conn,payload["snapshot"],receipt["requests"])
                simulation["investigation_evidence"].extend(result)
            simulation["investigation_requests"].append({**receipt,"evidence":result})
    after_job=conn.execute("SELECT md5(to_jsonb(j)::text) AS row_md5 FROM maintenance_jobs j WHERE id=%s",(job_id,)).fetchone()
    after_calls=conn.execute("SELECT id,md5(to_jsonb(c)::text) AS row_md5 FROM maintenance_calls c WHERE job_id=%s ORDER BY created_at,id",(job_id,)).fetchall()
assert after_job["row_md5"]==job["row_md5"]
assert after_calls==[{"id":r["id"],"row_md5":r["row_md5"]} for r in calls]
print(json.dumps({"variant":mode,"model_calls":0,"database_writes":0,"original_job_unchanged":True,
                  "original_calls_unchanged":True,"snapshot_commit":payload["snapshot"]["commit"],"prompts":rows}))
"""


def frozen_probe(image, container, variant):
    env = dict(pair.split("=", 1) for pair in container["Config"]["Env"])
    command = ["docker", "run", "--rm", "--pull", "never", "--read-only", "--memory", "512m",
               "--cpus", "0.5", "--pids-limit", "128", "--cap-drop", "ALL", "--security-opt",
               "no-new-privileges:true", "--user", container["Config"]["User"], "--tmpfs",
               "/tmp:size=64m,mode=1777", "--entrypoint", "python",
               "--network", next(n for n in container["NetworkSettings"]["Networks"] if n.endswith("_core"))]
    for pair in container["Config"]["Env"]:
        command += ["--env", pair]
    command += ["--env", "QUALIFICATION_VARIANT=" + variant]
    for mount in container["Mounts"]:
        if mount["Destination"] in {env["ROLES_FILE"], env["DATABASE_PASSWORD_FILE"]} or mount["Destination"].startswith("/etc/quant-company/"):
            assert mount["Type"] == "bind"
            command += ["--mount", "type=bind,src=" + mount["Source"] + ",dst=" + mount["Destination"] + ",readonly"]
    return json.loads(run(command + [image, "-c", PROBE], timeout=150))


qualified = []
for target in MANIFEST["targets"]:
    container = json.loads(run(["docker", "inspect", target["name"]]))[0]
    assert container["Id"] == target["id"] and container["Image"] == target["base_image_id"]
    assert container["State"]["Running"]
    root = target["module_root"]
    modules = list(target["base_module_sha256"])
    check = "import json,hashlib,importlib;from pathlib import Path;root=Path(" + repr(root) + ");" + \
        "mods=" + repr(modules) + ";[importlib.import_module('quant_company.'+p[:-3].replace('/','.')) for p in mods];" + \
        "print(json.dumps({p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in mods}))"
    installed = json.loads(run(["docker", "exec", target["name"], target["python"], "-c", check]))
    assert installed == target["base_module_sha256"]
    expected = {p: hashlib.sha256((ROOT / "payload" / p).read_bytes()).hexdigest() for p in modules}
    assert expected == target["candidate_module_sha256"]
    base = json.loads(run(["docker", "image", "inspect", target["image_tag"]]))[0]
    assert base["Id"] == container["Image"]
    dockerfile = ROOT / ("Dockerfile." + target["service"])
    lines = ["FROM " + target["image_tag"], "LABEL quant-company.maintenance-repair-patch=" + MANIFEST["patch_commit"]]
    lines += ["COPY --chmod=0644 payload/" + p + " " + root + "/" + p for p in modules]
    dockerfile.write_text("\n".join(lines) + "\n")
    baseline = frozen_probe(container["Image"], container, "baseline") if target["service"] == "maintenance" else None
    run(["docker", "build", "--network", "none", "-f", str(dockerfile), "-t", target["target_image"], str(ROOT)], timeout=180)
    image = json.loads(run(["docker", "image", "inspect", target["target_image"]]))[0]
    for field in ("User", "Entrypoint", "Cmd", "WorkingDir", "Env"):
        assert image["Config"][field] == base["Config"][field]
    candidate_hashes = json.loads(run(["docker", "run", "--rm", "--pull", "never", "--network", "none",
        "--read-only", "--memory", "256m", "--cpus", "0.5", "--pids-limit", "128", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--user", container["Config"]["User"],
        "--tmpfs", "/tmp:size=32m,mode=1777", "--entrypoint", target["python"], image["Id"], "-c", check]))
    assert candidate_hashes == expected
    candidate = frozen_probe(image["Id"], container, "candidate") if baseline else None
    qualified.append({**target, "target_image_id": image["Id"], "module_imports_passed": True,
                      "image_execution_configuration_unchanged": True, "baseline": baseline, "candidate": candidate})
receipt = {"captured_at": datetime.now(UTC).isoformat(), "patch_commit": MANIFEST["patch_commit"],
           "ci": MANIFEST["ci"], "scope": MANIFEST["scope"], "targets": qualified,
           "model_calls": 0, "database_writes": 0,
           "limitation": "Real frozen production evidence, no new model inference; automatic production repair/PR is not claimed."}
(ROOT / "qualification.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps(receipt))
