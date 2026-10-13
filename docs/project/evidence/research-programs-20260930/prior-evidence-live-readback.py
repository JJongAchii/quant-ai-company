import hashlib
import json
import subprocess
from pathlib import Path

sql = """BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SELECT json_build_object('observed_at',now(),
 'stage',(SELECT json_build_object('id',id,'stage',stage,'state',state,'attempt',attempt,'task_id',task_id,'context',context) FROM research_mission_stages WHERE id='e2b78eb8-6d5e-592f-ad67-bd7baa45f207'),
 'stalled_turn',(SELECT json_build_object('id',id,'status',status,'updated_at',updated_at,'provider',response->>'provider','request',request) FROM turns WHERE id='74b8234f-74f6-54de-befb-3d15e24833b4'),
 'turns',(SELECT json_agg(json_build_object('id',id,'status',status,'created_at',created_at,'updated_at',updated_at) ORDER BY created_at,id) FROM turns WHERE task_id='f173f628-29f6-5ad7-b94c-f673676e812f'),
 'model_call',(SELECT json_build_object('profile',profile,'state',state,'updated_at',updated_at) FROM model_account_calls WHERE request_id='74b8234f-74f6-54de-befb-3d15e24833b4'),
 'mission_count',(SELECT count(*) FROM research_missions WHERE program_id='e06537d3-fac3-5c8c-bf25-ddabb3c7e282'),
 'reservation_count',(SELECT count(*) FROM research_program_reservations WHERE program_id='e06537d3-fac3-5c8c-bf25-ddabb3c7e282'));
ROLLBACK;"""
r = subprocess.run(
    [
        "docker",
        "exec",
        "-i",
        "-u",
        "postgres",
        "quant-company-postgres-1",
        "psql",
        "-X",
        "-A",
        "-t",
        "-v",
        "ON_ERROR_STOP=1",
        "-d",
        "quant_company",
    ],
    input=sql,
    text=True,
    capture_output=True,
    check=True,
)
x = next(json.loads(line) for line in r.stdout.splitlines() if line.startswith("{"))
s = x["stage"]
ctx = s.pop("context")
t = x["stalled_turn"]
req = t.pop("request")
prompt = (req or {}).get("prompt", "")
files = []
for item in ctx["prior_tasks"]:
    name = "prior-tasks/" + str(item["id"]) + ".json"
    entry = ctx["_private_files"].get(name)
    if not entry:
        continue
    data = (Path("/var/lib/quant-company") / Path(entry["path"]).relative_to("/state")).read_bytes()
    assert hashlib.sha256(data).hexdigest() == entry["sha256"] and json.loads(data) == item
    files.append({"name": name, "sha256": entry["sha256"], "size": len(data), "exact_original_record": True})
rendered = json.loads(prompt.split("MISSION DATA JSON:\n", 1)[1]) if prompt else {}
x.update(
    prior_task_count=len(ctx["prior_tasks"]),
    prior_evidence_files=files,
    prompt_chars=len(prompt),
    prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest() if prompt else None,
    rendered_prior_task_count=len(rendered.get("prior_tasks", [])),
    read_chunk_count=len(rendered.get("read_chunks", [])),
    actual_model_request_present=bool(prompt),
)
print(json.dumps(x, sort_keys=True))
