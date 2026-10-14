"""Qualify small overlays against exact worker and maintenance source in separate PostgreSQL."""
import ast
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT=Path(sys.argv[1]).resolve()
INPUT=json.loads((ROOT/"input.json").read_text())
os.umask(0o077)
def run(args,timeout=90):
    result=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if result.returncode:
        print(result.stderr[-3200:],file=sys.stderr)
        raise RuntimeError("Qualification command failed: "+args[0])
    return result.stdout.strip()

targets={}
base_sources={}
candidate_sources={}
module_root="/app/.venv/lib/python3.12/site-packages/quant_company"
for key,name,path in [("worker","quant-company-worker-1","company.py"),
                      ("maintenance","quant-company-maintenance-1","maintenance/store.py")]:
    c=json.loads(run(["docker","inspect",name]))[0]
    assert c["State"]["Running"]
    labels=c["Config"]["Labels"]
    targets[key]={"name":name,"id":c["Id"],"base_image_id":c["Image"],"image_tag":c["Config"]["Image"],
        "project":labels["com.docker.compose.project"],"service":labels["com.docker.compose.service"],
        "compose_files":labels["com.docker.compose.project.config_files"],
        "envfile":"/var/lib/quant-company/config/runtime.env"}
    source=run(["docker","exec",name,"python","-c",
                "from pathlib import Path;print(Path("+repr(module_root+"/"+path)+").read_text(),end='')"])+"\n"
    patch=next(p for p in INPUT["patches"] if p["path"]==path)
    assert source.count(patch["old"])==1,"Unexpected actual source; stop and reconcile"
    candidate=source.replace(patch["old"],patch["new"])
    ast.parse(candidate)
    base_sources[key]={"path":path,"sha256":hashlib.sha256(source.encode()).hexdigest()}
    candidate_sources[key]={"path":path,"sha256":hashlib.sha256(candidate.encode()).hexdigest()}
    (ROOT/"payload"/("baseline-"+key+".py")).write_text(source)
    (ROOT/"payload"/(key+".py")).write_text(candidate)

helper=ROOT/"payload"/"admission.py"
ast.parse(helper.read_text())
images={}
short=INPUT["patch_commit"][:7]
for key,target in targets.items():
    module=module_root+"/"+base_sources[key]["path"]
    tag="quant-company-maintainer-admission-"+key+":"+short
    dockerfile=ROOT/("Dockerfile."+key)
    dockerfile.write_text("FROM "+target["image_tag"]+"\nCOPY --chmod=0644 payload/"+key+".py "+module+"\nCOPY --chmod=0644 payload/admission.py "+module_root+"/maintenance/admission.py\n")
    base=json.loads(run(["docker","image","inspect",target["image_tag"]]))[0]
    assert base["Id"]==target["base_image_id"]
    run(["docker","build","--network","none","-f",str(dockerfile),"-t",tag,str(ROOT)],timeout=180)
    image=json.loads(run(["docker","image","inspect",tag]))[0]
    for field in ("User","Entrypoint","Cmd","WorkingDir","Env"):
        assert image["Config"][field]==base["Config"][field]
    images[key]={"tag":tag,"id":image["Id"]}

qual_image="quant-company-maintainer-admission-qualification:"+short
(ROOT/"Dockerfile.qual").write_text("FROM "+images["worker"]["tag"]+"\nCOPY --chmod=0644 payload/maintenance.py "+module_root+"/maintenance/store.py\nCOPY --chmod=0644 payload/baseline-worker.py /opt/baseline-company.py\nCOPY --chmod=0644 payload/baseline-maintenance.py /opt/baseline-store.py\nCOPY --chmod=0644 admission-probe.py /opt/admission-probe.py\n")
run(["docker","build","--network","none","-f",str(ROOT/"Dockerfile.qual"),"-t",qual_image,str(ROOT)],timeout=180)
pg_image=json.loads(run(["docker","inspect","quant-company-postgres-1"]))[0]["Image"]
network="maintainer-admission-qual-"+short
pg_name=network+"-pg"
assert not run(["docker","network","ls","--filter","name=^"+network+"$","--format","{{.ID}}"])
assert not run(["docker","ps","-a","--filter","name=^/"+pg_name+"$","--format","{{.ID}}"])
network_created=False
pg_created=False
try:
    run(["docker","network","create","--internal",network])
    network_created=True
    run(["docker","run","-d","--name",pg_name,"--pull","never","--network",network,
         "--memory","512m","--cpus","0.5","--pids-limit","128",
         "--tmpfs","/var/lib/postgresql/data:size=256m,mode=0700",
         "--env","POSTGRES_HOST_AUTH_METHOD=trust",pg_image])
    pg_created=True
    for _ in range(30):
        ready=subprocess.run(["docker","exec",pg_name,"pg_isready","-U","postgres"],capture_output=True)
        if ready.returncode==0:
            break
        time.sleep(1)
    else:
        raise RuntimeError("Disposable PostgreSQL not ready")
    output=run(["docker","run","--rm","--pull","never","--network",network,"--read-only",
                "--memory","512m","--cpus","0.5","--pids-limit","128","--cap-drop","ALL",
                "--security-opt","no-new-privileges:true","--tmpfs","/tmp:size=64m,mode=1777",
                "--env","QUALIFICATION_PG="+pg_name,"--entrypoint","python",qual_image,
                "/opt/admission-probe.py"],timeout=180)
    comparison=json.loads(output)
finally:
    if pg_created:
        run(["docker","rm","-f",pg_name])
    if network_created:
        run(["docker","network","rm",network])
receipt={"captured_at":datetime.now(UTC).isoformat(),"patch_commit":INPUT["patch_commit"],"scope":INPUT["scope"],
    "targets":targets,"base_sources":base_sources,"candidate_sources":candidate_sources,
    "helper_sha256":hashlib.sha256(helper.read_bytes()).hexdigest(),"images":images,
    "comparison":comparison,"image_execution_configuration_unchanged":True,
    "existing_independent_source_changes_preserved":True,"temporary_database_and_network_removed":True}
(ROOT/"qualification.json").write_text(json.dumps(receipt,indent=2)+"\n")
print(json.dumps(receipt))

