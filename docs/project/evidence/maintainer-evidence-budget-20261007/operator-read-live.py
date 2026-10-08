"""Read actual post-cutover state and usage; no writes, model calls or private prompts."""

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
cutover = json.loads((ROOT / "cutover.json").read_text())
manifest = json.loads((ROOT / "manifest.json").read_text())

def run(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise RuntimeError("post_cutover_read_failed_" + args[0])
    return result.stdout

service = json.loads(run(["docker", "inspect", "quant-company-maintenance-1"]))[0]
probe = "import hashlib,json;from pathlib import Path;root=Path(" + repr(manifest["module_root"]) + ");print(json.dumps({p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in " + repr(list(manifest["base_module_sha256"])) + "}))"
installed = json.loads(run(["docker", "exec", "quant-company-maintenance-1", "python", "-c", probe]))
assert all(installed[path] == manifest["candidate_module_sha256"][Path(path).name] for path in installed)
identities = ",".join("'" + identity + "'::uuid" for identity in manifest["case_ids"])
since = cutover["completed_at"]
assert all(character in "0123456789-:.TZ+" for character in since)
sql = """BEGIN READ ONLY;
SELECT jsonb_build_object(
'heartbeat',(SELECT jsonb_build_object('enabled',runtime->'enabled','heartbeat_at',runtime->'heartbeat_at',
'poll_seconds',runtime->'poll_seconds') FROM maintenance_control WHERE id=1),
'repository',(SELECT jsonb_build_object('commit',commit,'checked_at',checked_at,
'read_order_version',metadata#>'{coverage,read_order_version}',
'read_bytes',metadata#>'{coverage,read_bytes}',
'review_code_available',files ? 'src/quant_company/staff/independent_review.py',
'claude_code_available',files ? 'src/quant_company/providers/claude_runner.py')
FROM repository_evidence ORDER BY checked_at DESC LIMIT 1),
'original_cases',(SELECT jsonb_agg(jsonb_build_object('id',id,'kind',kind,'state',state,'error',error,
'revision',payload->'diagnostic_revision')) FROM maintenance_jobs WHERE id IN (""" + identities + """)),
'new_calls_since_cutover',(SELECT COALESCE(jsonb_agg(jsonb_build_object('id',id,'job_id',job_id,
'model',request->'model','has_response',response IS NOT NULL,'usage',response->'usage')),'[]'::jsonb)
FROM maintenance_calls WHERE created_at>'""" + since + """'::timestamptz),
'eligible_jobs',(SELECT count(*) FROM maintenance_jobs WHERE state IN
('review','triage','patch','design','evaluate','publish','ci','pr')));
COMMIT;"""
raw = run(["docker", "exec", "-u", "postgres", "quant-company-postgres-1", "psql", "-XAt",
           "-v", "ON_ERROR_STOP=1", "-d", "quant_company", "-c", sql])
facts = json.loads(next(line for line in raw.splitlines() if line.startswith("{")))
receipt = {"captured_at": datetime.now(UTC).isoformat(), "scope": "Actual deployed container and read-only PostgreSQL",
    "container": {"id": service["Id"], "image_id": service["Image"], "running": service["State"]["Running"],
                  "oom_killed": service["State"]["OOMKilled"], "restart_count": service["RestartCount"],
                  "memory_limit_bytes": service["HostConfig"]["Memory"]},
    "installed_module_sha256": installed, **facts, "model_calls_by_this_probe": 0, "database_writes": 0,
    "usage_interpretation": "Character preparation comparison is not token savings. Zero new calls provide no post-optimization token estimate."}
(ROOT / "live-after.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps(receipt))
