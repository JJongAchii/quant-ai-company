"""Qualify a runner-only overlay with real stored case evidence; no model call or DB write."""
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
        raise RuntimeError("Qualification failed: " + args[0])
    return result.stdout.strip()


PROBE = r"""
import hashlib,json,os
from pathlib import Path
from urllib.parse import quote
import psycopg
from psycopg.rows import dict_row
from quant_company.maintenance.investigation import inspection_context
from quant_company.maintenance.policy import Triage
from quant_company.maintenance.runner import proposal_material
password=Path(os.environ["DATABASE_PASSWORD_FILE"]).read_text().strip()
url="postgresql://"+quote(os.environ.get("DATABASE_USER","company"),safe="")+":"+quote(password,safe="")+"@postgres:5432/"+quote(os.environ.get("DATABASE_NAME","quant_company"),safe="")
with psycopg.connect(url,row_factory=dict_row,connect_timeout=5) as conn:
    conn.execute("SET TRANSACTION READ ONLY")
    job=conn.execute("SELECT payload,md5(to_jsonb(j)::text) digest FROM maintenance_jobs j WHERE id=%s",("672a94ca-b8e9-51b7-8fb0-354b96c7f355",)).fetchone()
    calls=conn.execute("SELECT id,request,md5(to_jsonb(c)::text) digest FROM maintenance_calls c WHERE job_id=%s ORDER BY created_at,id",("672a94ca-b8e9-51b7-8fb0-354b96c7f355",)).fetchall()
    assert len(calls)==8
    latest=calls[-1]
    material=json.loads(latest["request"]["prompt"].split("EVIDENCE JSON:\n",1)[1])
    original_request=material["requested_diagnosis"]
    original_observations=material["observations"]
    # Restore the already stored exact code to the real last request. The reducer is then
    # tested on identical case material in both actual images, without another inference.
    material.update(inspection_context(job["payload"]))
    def lines(rows):
        result=set()
        for row in rows:
            for line in row.get("content","").splitlines():
                if ": " in line:
                    number=int(line.split(": ",1)[0])
                    result.add((row["path"],number,line))
        return result
    raw=lines(material["investigated_code"])
    shown,prompt=proposal_material(material,Triage)
    visible=lines(shown["investigated_code"])
    ranges=[]
    for path,start,count in [
        ("src/quant_company/maintenance/requests.py",88,75),
        ("src/quant_company/maintenance/requests.py",219,34),
        ("src/quant_company/maintenance/cases.py",60,75),
        ("tests/test_maintenance_repair_path.py",59,143),
    ]:
        expected={item for item in raw if item[0]==path and start<=item[1]<start+count}
        assert expected,"Real already-read target range must exist"
        ranges.append({"path":path,"start_line":start,"stored_lines":len(expected),
                       "visible_lines":len(expected&visible),"complete":expected<=visible})
    assert len(prompt)<=88000
    assert shown["requested_diagnosis"]==original_request
    assert shown["observations"]==original_observations
    after=conn.execute("SELECT md5(to_jsonb(j)::text) digest FROM maintenance_jobs j WHERE id=%s",("672a94ca-b8e9-51b7-8fb0-354b96c7f355",)).fetchone()
    preserved=conn.execute("SELECT id,md5(to_jsonb(c)::text) digest FROM maintenance_calls c WHERE job_id=%s ORDER BY created_at,id",("672a94ca-b8e9-51b7-8fb0-354b96c7f355",)).fetchall()
    assert after["digest"]==job["digest"]
    assert preserved==[{"id":c["id"],"digest":c["digest"]} for c in calls]
    print(json.dumps({"original_call_id":latest["id"],"original_job_unchanged":True,
        "original_calls_unchanged":True,"original_request_unchanged":True,"observations_unchanged":True,
        "prompt_characters":len(prompt),"prompt_sha256":hashlib.sha256(prompt.encode()).hexdigest(),
        "prompt_sections":{k:len(json.dumps(v,ensure_ascii=False)) for k,v in shown.items()},
        "diagnostic_sections":{k:len(json.dumps(v,ensure_ascii=False)) for k,v in shown.get("current_implementation",{}).items()},
        "history_sections":{k:len(json.dumps(v,ensure_ascii=False)) for k,v in shown.get("history",{}).items()},
        "stored_code_characters":sum(len(r.get("content","")) for r in material["investigated_code"]),
        "all_ranges_visible":all(r["complete"] for r in ranges),"ranges":ranges,
        "model_calls":0,"database_writes":0}))
    conn.rollback()
"""


def probe(image, container):
    env = dict(pair.split("=", 1) for pair in container["Config"]["Env"])
    command = ["docker", "run", "--rm", "--pull", "never", "--read-only", "--memory", "512m",
               "--cpus", "0.5", "--pids-limit", "128", "--cap-drop", "ALL", "--security-opt",
               "no-new-privileges:true", "--user", container["Config"]["User"], "--tmpfs",
               "/tmp:size=64m,mode=1777", "--entrypoint", "python", "--network",
               next(n for n in container["NetworkSettings"]["Networks"] if n.endswith("_core"))]
    for pair in container["Config"]["Env"]:
        command += ["--env", pair]
    for mount in container["Mounts"]:
        if mount["Destination"] in {env["ROLES_FILE"], env["DATABASE_PASSWORD_FILE"]} or mount["Destination"].startswith("/etc/quant-company/"):
            assert mount["Type"] == "bind"
            command += ["--mount", "type=bind,src=" + mount["Source"] + ",dst=" + mount["Destination"] + ",readonly"]
    return json.loads(run(command + [image, "-c", PROBE], timeout=120))


target = MANIFEST["target"]
container = json.loads(run(["docker", "inspect", target["name"]]))[0]
assert container["Id"] == target["id"] and container["Image"] == target["base_image_id"]
assert container["State"]["Running"]
module = "/app/.venv/lib/python3.12/site-packages/quant_company/maintenance/runner.py"
check = "import hashlib;from pathlib import Path;print(hashlib.sha256(Path(" + repr(module) + ").read_bytes()).hexdigest())"
assert run(["docker", "exec", target["name"], "python", "-c", check]) == MANIFEST["base_runner_sha256"]
assert hashlib.sha256((ROOT / "payload" / "runner.py").read_bytes()).hexdigest() == MANIFEST["candidate_runner_sha256"]
base = json.loads(run(["docker", "image", "inspect", target["image_tag"]]))[0]
assert base["Id"] == container["Image"]
baseline = probe(base["Id"], container)
assert not baseline["all_ranges_visible"], "Installed baseline must reproduce the actual observed loss"
dockerfile = ROOT / "Dockerfile"
dockerfile.write_text("FROM " + target["image_tag"] + "\nCOPY --chmod=0644 payload/runner.py " + module + "\n")
run(["docker", "build", "--network", "none", "-f", str(dockerfile), "-t", MANIFEST["target_image"], str(ROOT)], timeout=180)
image = json.loads(run(["docker", "image", "inspect", MANIFEST["target_image"]]))[0]
for field in ("User", "Entrypoint", "Cmd", "WorkingDir", "Env"):
    assert image["Config"][field] == base["Config"][field]
candidate = probe(image["Id"], container)
print(json.dumps({"baseline":baseline,"candidate":candidate}))
assert candidate["all_ranges_visible"]
receipt = {"captured_at": datetime.now(UTC).isoformat(), "patch_commit": MANIFEST["patch_commit"],
           "target": target, "target_image": MANIFEST["target_image"], "target_image_id": image["Id"],
           "base_runner_sha256": MANIFEST["base_runner_sha256"],
           "candidate_runner_sha256": MANIFEST["candidate_runner_sha256"],
           "baseline": baseline, "candidate": candidate, "image_execution_configuration_unchanged": True,
           "model_calls": 0, "database_writes": 0}
(ROOT / "qualification.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps(receipt))
