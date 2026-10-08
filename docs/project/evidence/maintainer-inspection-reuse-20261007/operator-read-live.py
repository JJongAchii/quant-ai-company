"""Read deployed module hashes, old receipts and actual post-cutover usage; no writes."""
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT=Path(sys.argv[1]).resolve()
manifest=json.loads((ROOT/"manifest.json").read_text())
cutover=json.loads((ROOT/"cutover.json").read_text())
qualified=json.loads((ROOT/"qualification.json").read_text())

def run(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=45)
    if result.returncode:
        raise RuntimeError("Read-only postcheck failed: "+args[0])
    return result.stdout

def sql(query):
    raw=run(["docker","exec","-u","postgres","quant-company-postgres-1","psql","-XqAt","-v","ON_ERROR_STOP=1",
             "-d","quant_company","-c","BEGIN READ ONLY; "+query+"; COMMIT;"])
    return json.loads(raw)

service=json.loads(run(["docker","inspect","quant-company-maintenance-1"]))[0]
probe="import hashlib,json;from pathlib import Path;r=Path("+repr(manifest["module_root"])+");print(json.dumps({p:hashlib.sha256((r/p).read_bytes()).hexdigest() for p in "+repr(list(manifest["base_module_sha256"]))+"}))"
installed=json.loads(run(["docker","exec","quant-company-maintenance-1","python","-c",probe]))
assert all(installed[p]==manifest["candidate_module_sha256"][Path(p).name] for p in installed)
assert service["Image"]==qualified["target_image_id"] and service["State"]["Running"]
ids=list(cutover["historical_maintenance_call_sha256"])
safe_ids=",".join("'"+i+"'" for i in ids)
old_rows=sql("SELECT COALESCE(jsonb_agg(to_jsonb(c)),'[]'::jsonb) FROM maintenance_calls c WHERE id IN ("+safe_ids+")")
old_sha={row["id"]:hashlib.sha256(json.dumps(row,sort_keys=True,separators=(",",":")).encode()).hexdigest() for row in old_rows}
assert old_sha==cutover["historical_maintenance_call_sha256"]
old_reviews=sql("SELECT COALESCE(jsonb_agg(to_jsonb(s)),'[]'::jsonb) FROM staff_independent_reviews s WHERE state='blocked' AND error='uncertain'")
review_sha={row["id"]:hashlib.sha256(json.dumps(row,sort_keys=True,separators=(",",":")).encode()).hexdigest() for row in old_reviews}
assert all(review_sha.get(k)==v for k,v in cutover["uncertain_review_sha256"].items())
since=cutover["completed_at"]
assert all(c in "0123456789-:.TZ+" for c in since)
facts=sql("""SELECT jsonb_build_object(
'heartbeat',(SELECT jsonb_build_object('enabled',runtime->'enabled','at',runtime->'heartbeat_at','poll_seconds',runtime->'poll_seconds') FROM maintenance_control WHERE id=1),
'eligible_jobs',(SELECT count(*) FROM maintenance_jobs WHERE state IN ('review','triage','patch','design','evaluate','publish','ci','pr')),
'original_case',(SELECT jsonb_build_object('id',id,'state',state,'error',error,'updated_at',updated_at) FROM maintenance_jobs WHERE id='49144860-7e42-5a72-8311-d47871ccf315'),
'new_calls_since_cutover',(SELECT COALESCE(jsonb_agg(jsonb_build_object('id',id,'job_id',job_id,'model',request->'model','has_response',response IS NOT NULL,'usage',response->'usage')),'[]'::jsonb) FROM maintenance_calls WHERE created_at>'"""+since+"""'::timestamptz))""")
record={"captured_at":datetime.now(UTC).isoformat(),"scope":"Actual installed maintenance container, old receipts and PostgreSQL; read only",
        "patch_commit":manifest["patch_commit"],"installed_module_sha256":installed,
        "container":{"id":service["Id"],"image_id":service["Image"],"running":service["State"]["Running"],
                     "oom_killed":service["State"]["OOMKilled"],"restart_count":service["RestartCount"],
                     "memory_limit_bytes":service["HostConfig"]["Memory"]},
        "historical_calls_unchanged":True,"historical_call_count":len(old_sha),"uncertain_reviews_unchanged":True,
        "uncertain_review_count":len(cutover["uncertain_review_sha256"]),
        **facts,"model_calls_by_operator":0,"database_writes":0,
        "usage_boundary":"Prepared character reduction and code-query reuse are not observed model-token/cache savings"}
(ROOT/"live-after.json").write_text(json.dumps(record,indent=2)+"\n")
print(json.dumps(record))

