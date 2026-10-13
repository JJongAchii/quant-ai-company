"""Install the qualified observation selector at the maintenance advisory-lock boundary."""
import fcntl
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
Q = json.loads((ROOT / "qualification.json").read_text())
CI = json.loads((ROOT / "source-ci.json").read_text())
JOURNAL = ROOT / "cutover.json"
os.umask(0o077)


def run(args, timeout=90):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Operator command failed: " + args[0])
    return result.stdout.strip()


def save(record):
    temporary = JOURNAL.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(record, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, JOURNAL)


def inventory():
    names = run(["docker", "ps", "-a", "--format", "{{.Names}}"]).splitlines()
    containers = json.loads(run(["docker", "inspect", *[n for n in names if n.startswith("quant-company-")]]))
    return {c["Name"].lstrip("/"): c for c in containers}


def public_inventory(containers):
    return {name: {"id":c["Id"], "image":c["Image"], "running":c["State"]["Running"]}
            for name,c in containers.items()}


def boundaries(old, new):
    for field in ("User", "Entrypoint", "Cmd", "WorkingDir"):
        assert new["Config"][field] == old["Config"][field]
    assert dict(p.split("=",1) for p in new["Config"]["Env"]) == dict(p.split("=",1) for p in old["Config"]["Env"])
    for field in ("ReadonlyRootfs", "CapDrop", "CapAdd", "SecurityOpt", "Memory", "NanoCpus", "PidsLimit", "Tmpfs", "RestartPolicy"):
        assert new["HostConfig"][field] == old["HostConfig"][field]
    assert sorted((m["Type"],m["Source"],m["Destination"],m["RW"]) for m in new["Mounts"]) == sorted(
        (m["Type"],m["Source"],m["Destination"],m["RW"]) for m in old["Mounts"])
    assert set(new["NetworkSettings"]["Networks"]) == set(old["NetworkSettings"]["Networks"])


assert CI["headRefOid"] == Q["patch_commit"]
assert all(c["status"]=="COMPLETED" and c["conclusion"]=="SUCCESS" for c in CI["statusCheckRollup"])
assert {c["name"] for c in CI["statusCheckRollup"]} == {"service","codex-protocol"}
assert Q["comparison"]["full_review_exactly_equal"] and Q["comparison"]["baseline_timed_out_at_five_seconds"]
with Path("/var/lib/quant-company/.backup.lock").open("a") as backup:
    fcntl.flock(backup, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert not JOURNAL.exists(), "Reconcile the saved prior effect receipt; do not replay"
    locker = subprocess.Popen(["docker","exec","-i","-u","postgres","quant-company-postgres-1",
                               "psql","-XqAt","-v","ON_ERROR_STOP=1","-d","quant_company"],
                              stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)

    def query(statement):
        locker.stdin.write(statement+";\n")
        locker.stdin.flush()
        return json.loads(locker.stdout.readline().strip())

    record = None
    try:
        assert query("SELECT jsonb_build_object('acquired',pg_try_advisory_lock(71350221))")["acquired"], "Maintenance is executing; wait for its normal boundary"
        assert query("SELECT to_jsonb(count(*)) FROM maintenance_calls c JOIN maintenance_jobs j ON j.id=c.job_id WHERE c.response IS NULL AND j.state IN ('review','triage','patch','design','evaluate','publish')") == 0
        before = inventory()
        target = Q["target"]
        old = before[target["name"]]
        assert old["Id"] == target["id"] and old["Image"] == target["base_image_id"]
        image = json.loads(run(["docker","image","inspect",Q["target_image"]]))[0]
        assert image["Id"] == Q["target_image_id"]
        env = dict(pair.split("=",1) for pair in old["Config"]["Env"])
        override = Path("/var/lib/quant-company/config")/("maintainer-selector-"+Q["patch_commit"]+".compose.json")
        override.write_text(json.dumps({"services":{"maintenance":{
            "image":Q["target_image"],"environment":env,"mem_limit":old["HostConfig"]["Memory"],
            "cpus":old["HostConfig"]["NanoCpus"]/1e9}}}))
        command = ["docker","compose","--project-name",target["project"],"--profile","*","--env-file",target["envfile"]]
        for file in target["compose_files"].split(",")+[str(override)]:
            command += ["-f",file]
        resolved = json.loads(run(command+["config","--format","json"]))["services"]["maintenance"]
        assert resolved["image"] == Q["target_image"]
        assert {k:str(v) for k,v in resolved["environment"].items()} == env
        original_query = "SELECT jsonb_build_object('jobs',(SELECT jsonb_agg(jsonb_build_object('id',id,'digest',md5(to_jsonb(j)::text)) ORDER BY id) FROM maintenance_jobs j WHERE id IN ('672a94ca-b8e9-51b7-8fb0-354b96c7f355','c7d07401-ef8a-5d16-96f5-b6048b16dd1b')),'calls',(SELECT jsonb_agg(jsonb_build_object('id',id,'digest',md5(to_jsonb(c)::text)) ORDER BY id) FROM maintenance_calls c WHERE job_id IN ('672a94ca-b8e9-51b7-8fb0-354b96c7f355','c7d07401-ef8a-5d16-96f5-b6048b16dd1b')))"
        preserved = query(original_query)
        release = str(Path("/opt/quant-company/current").resolve())
        record = {"state":"cutover_started","started_at":datetime.now(UTC).isoformat(),
                  "patch_commit":Q["patch_commit"],"ci":CI,"before":public_inventory(before),
                  "global_release":release,"override":str(override),"original_case":preserved,
                  "scope":"maintenance observation selector only; maintenance session lock held; other workers continue",
                  "rollback_image":target["image_tag"],"rollback_image_id":target["base_image_id"]}
        save(record)
        if old["State"]["Running"]:
            run(["docker","stop","--time","60",target["name"]],timeout=90)
        run(command+["up","-d","--no-deps","--no-build","--pull","never","maintenance"],timeout=180)
        after = inventory()
        new = after[target["name"]]
        assert new["Image"] == Q["target_image_id"] and new["State"]["Running"] and not new["State"]["OOMKilled"]
        boundaries(old,new)
        module="/app/.venv/lib/python3.12/site-packages/quant_company/maintenance/observation.py"
        check="import hashlib;from pathlib import Path;print(hashlib.sha256(Path("+repr(module)+").read_bytes()).hexdigest())"
        assert run(["docker","exec",target["name"],"python","-c",check]) == Q["candidate_observation_sha256"]
        runner=module.replace("observation.py","runner.py")
        preserved_check="import hashlib;from pathlib import Path;print(hashlib.sha256(Path("+repr(runner)+").read_bytes()).hexdigest())"
        assert run(["docker","exec",target["name"],"python","-c",preserved_check]) == Q["preserved_runner_sha256"]
        assert query(original_query) == preserved
        assert str(Path("/opt/quant-company/current").resolve()) == release
        before_public,after_public=public_inventory(before),public_inventory(after)
        assert all(after_public[name]==value for name,value in before_public.items() if name!=target["name"])
        record.update(state="active",completed_at=datetime.now(UTC).isoformat(),after=after_public,
                      original_case_unchanged=True,other_containers_unchanged=True,
                      environment_and_boundaries_unchanged=True,model_calls_by_operator=0,
                      global_admission_pause=False)
        save(record)
        print(json.dumps(record))
    except BaseException as error:
        if record is not None:
            record.update(state="requires_operator_reconciliation",error_type=type(error).__name__)
            save(record)
        raise
    finally:
        locker.stdin.close()
        locker.wait(timeout=10)
