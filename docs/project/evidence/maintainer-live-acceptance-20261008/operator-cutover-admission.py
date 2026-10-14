"""Install qualified admission overlays between completed model calls; no DB pause or replay."""
import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT=Path(sys.argv[1]).resolve()
Q=json.loads((ROOT/"qualification.json").read_text())
CI=json.loads((ROOT/"source-ci.json").read_text())
JOURNAL=ROOT/"cutover.json"
os.umask(0o077)

def run(args,timeout=90):
    result=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if result.returncode:
        raise RuntimeError("Operator command failed: "+args[0])
    return result.stdout.strip()

def save(record):
    tmp=JOURNAL.with_suffix(".tmp")
    with tmp.open("w") as stream:
        json.dump(record,stream,indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp,JOURNAL)

def inventory():
    names=[n for n in run(["docker","ps","-a","--format","{{.Names}}"]).splitlines() if n.startswith("quant-company-")]
    return {c["Name"].lstrip("/"):c for c in json.loads(run(["docker","inspect",*names]))}

def public(containers):
    return {name:{"id":c["Id"],"image":c["Image"],"running":c["State"]["Running"]} for name,c in containers.items()}

def boundaries(old,new):
    for field in ("User","Entrypoint","Cmd","WorkingDir"):
        assert old["Config"][field]==new["Config"][field]
    assert dict(p.split("=",1) for p in old["Config"]["Env"])==dict(p.split("=",1) for p in new["Config"]["Env"])
    for field in ("ReadonlyRootfs","CapDrop","CapAdd","SecurityOpt","Memory","NanoCpus","PidsLimit","Tmpfs","RestartPolicy"):
        assert old["HostConfig"][field]==new["HostConfig"][field]
    assert sorted((m["Type"],m["Source"],m["Destination"],m["RW"]) for m in old["Mounts"])==sorted(
        (m["Type"],m["Source"],m["Destination"],m["RW"]) for m in new["Mounts"])
    assert set(old["NetworkSettings"]["Networks"])==set(new["NetworkSettings"]["Networks"])

def source_hash(name,path):
    code="import hashlib;from pathlib import Path;print(hashlib.sha256(Path("+repr("/app/.venv/lib/python3.12/site-packages/quant_company/"+path)+").read_bytes()).hexdigest())"
    return run(["docker","exec",name,"python","-c",code])

assert CI["headRefOid"]==Q["patch_commit"]
assert {c["name"] for c in CI["statusCheckRollup"]}=={"service","codex-protocol"}
assert all(c["status"]=="COMPLETED" and c["conclusion"]=="SUCCESS" for c in CI["statusCheckRollup"])
assert Q["comparison"]["candidate"]["frozen_request_human_priority_and_release_verified"]
assert all(Q["comparison"]["guards"].values())
with Path("/var/lib/quant-company/.backup.lock").open("a") as backup:
    fcntl.flock(backup,fcntl.LOCK_EX|fcntl.LOCK_NB)
    assert not JOURNAL.exists(),"Reconcile the saved effect receipt; do not replay"
    locker=subprocess.Popen(["docker","exec","-i","-u","postgres","quant-company-postgres-1",
        "psql","-XqAt","-v","ON_ERROR_STOP=1","-d","quant_company"],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
    def query(sql):
        locker.stdin.write(sql+";\n")
        locker.stdin.flush()
        result=locker.stdout.readline().strip()
        if not result:
            raise RuntimeError("Database boundary query failed")
        return json.loads(result)
    held=[]
    record=None
    try:
        assert query("SELECT jsonb_build_object('acquired',pg_try_advisory_lock(71350221))")["acquired"]
        before=inventory()
        for key,target in Q["targets"].items():
            old=before[target["name"]]
            assert old["Id"]==target["id"] and old["Image"]==target["base_image_id"] and old["State"]["Running"]
            assert source_hash(target["name"],Q["base_sources"][key]["path"])==Q["base_sources"][key]["sha256"]
            assert json.loads(run(["docker","image","inspect",Q["images"][key]["tag"]]))[0]["Id"]==Q["images"][key]["id"]
        providers={}
        for name,var in [("quant-company-codex-runtime-1","CODEX_JOBS_DIR"),
                         ("quant-company-claude-runtime-1","CLAUDE_JOBS_DIR")]:
            c=before[name]
            env=dict(p.split("=",1) for p in c["Config"]["Env"])
            dest=env[var]
            m=next(m for m in c["Mounts"] if dest==m["Destination"] or dest.startswith(m["Destination"].rstrip("/")+"/"))
            path=Path(m["Source"]+dest[len(m["Destination"]):]+"/.runtime.lock")
            assert m["Type"]=="bind" and path.exists()
            providers[name]=path
        print(json.dumps({"state":"waiting_for_completed_model_boundary","at":datetime.now(UTC).isoformat()}),flush=True)
        start=time.monotonic()
        while time.monotonic()-start<900:
            try:
                for path in providers.values():
                    stream=path.open("r+")
                    held.append(stream)
                    fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                for stream in held:
                    stream.close()
                held=[]
                time.sleep(1)
        else:
            raise RuntimeError("Existing calls still active; no service replaced")
        # The CLI lock is released after saving its receipt; let its worker commit normally.
        for _ in range(60):
            running=query("SELECT to_jsonb(count(*)) FROM turns t JOIN tasks k ON k.id=t.task_id JOIN projects p ON p.id=k.project_id WHERE t.status='running' AND k.revision=p.revision")
            if running==0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Company outcome still committing; no service replaced")
        assert query("SELECT to_jsonb(count(*)) FROM maintenance_calls c JOIN maintenance_jobs j ON j.id=c.job_id WHERE c.response IS NULL AND j.state IN ('review','triage','patch','design','evaluate','publish')")==0
        current=inventory()
        assert public(current)==public(before),"Another operator changed a container; reconcile first"
        release=str(Path("/opt/quant-company/current").resolve())
        original_sql="SELECT jsonb_build_object('jobs',(SELECT jsonb_agg(jsonb_build_object('id',id,'digest',md5(to_jsonb(j)::text)) ORDER BY id) FROM maintenance_jobs j WHERE id IN ('672a94ca-b8e9-51b7-8fb0-354b96c7f355','c7d07401-ef8a-5d16-96f5-b6048b16dd1b')),'calls',(SELECT jsonb_agg(jsonb_build_object('id',id,'digest',md5(to_jsonb(c)::text)) ORDER BY id) FROM maintenance_calls c WHERE job_id IN ('672a94ca-b8e9-51b7-8fb0-354b96c7f355','c7d07401-ef8a-5d16-96f5-b6048b16dd1b')))"
        originals=query(original_sql)
        frozen=query("SELECT COALESCE(jsonb_agg(jsonb_build_object('id',id,'digest',md5(request::text)) ORDER BY id),'[]'::jsonb) FROM turns WHERE request IS NOT NULL AND status IN ('queued','waiting','running')")
        commands={}
        for key,target in Q["targets"].items():
            old=before[target["name"]]
            env=dict(p.split("=",1) for p in old["Config"]["Env"])
            override=Path("/var/lib/quant-company/config")/("maintainer-admission-"+Q["patch_commit"]+"-"+key+".compose.json")
            override.write_text(json.dumps({"services":{target["service"]:{
                "image":Q["images"][key]["tag"],"environment":env,
                "mem_limit":old["HostConfig"]["Memory"],"cpus":old["HostConfig"]["NanoCpus"]/1e9}}}))
            command=["docker","compose","--project-name",target["project"],"--profile","*","--env-file",target["envfile"]]
            for file in target["compose_files"].split(",")+[str(override)]:
                command+=["-f",file]
            resolved=json.loads(run(command+["config","--format","json"]))["services"][target["service"]]
            assert resolved["image"]==Q["images"][key]["tag"]
            assert {k:str(v) for k,v in resolved["environment"].items()}==env
            commands[key]=command
        record={"state":"cutover_started","started_at":datetime.now(UTC).isoformat(),
            "patch_commit":Q["patch_commit"],"ci":CI,"before":public(before),"global_release":release,
            "original_cases":originals,"frozen_company_requests":frozen,
            "inference_locks_held_after_existing_calls_completed":list(providers),
            "scope":"worker admission and maintenance reservation only"}
        save(record)
        for key in ("worker","maintenance"):
            run(["docker","stop","--time","60",Q["targets"][key]["name"]],timeout=90)
        for key in ("maintenance","worker"):
            target=Q["targets"][key]
            run(commands[key]+["up","-d","--no-deps","--no-build","--pull","never",target["service"]],timeout=180)
        after=inventory()
        changed={t["name"] for t in Q["targets"].values()}
        for key,target in Q["targets"].items():
            new=after[target["name"]]
            assert new["State"]["Running"] and not new["State"]["OOMKilled"]
            assert new["Image"]==Q["images"][key]["id"]
            boundaries(before[target["name"]],new)
            assert source_hash(target["name"],Q["candidate_sources"][key]["path"])==Q["candidate_sources"][key]["sha256"]
            assert source_hash(target["name"],"maintenance/admission.py")==Q["helper_sha256"]
        assert query(original_sql)==originals
        if frozen:
            ids=",".join("'"+r["id"]+"'" for r in frozen)
            saved=query("SELECT jsonb_agg(jsonb_build_object('id',id,'digest',md5(request::text)) ORDER BY id) FROM turns WHERE id IN ("+ids+")")
            assert saved==frozen
        assert all(public(after)[name]==value for name,value in public(before).items() if name not in changed)
        assert str(Path("/opt/quant-company/current").resolve())==release
        record.update(state="active",completed_at=datetime.now(UTC).isoformat(),after=public(after),
            original_cases_and_calls_unchanged=True,frozen_company_requests_unchanged=True,
            environment_and_boundaries_unchanged=True,other_containers_unchanged=True,
            model_calls_by_operator=0,global_admission_pause=False,models_effort_caps_and_slots_unchanged=True)
        save(record)
        print(json.dumps(record),flush=True)
    except BaseException as error:
        if record is not None:
            record.update(state="requires_operator_reconciliation",error_type=type(error).__name__)
            save(record)
        raise
    finally:
        for stream in held:
            stream.close()
        locker.stdin.close()
        locker.wait(timeout=10)
