"""Read company authority, worker, registry and science counts; no mutations."""

import hashlib
import json
import subprocess
from pathlib import Path

PROGRAM = "e06537d3-fac3-5c8c-bf25-ddabb3c7e282"
worker = json.loads(subprocess.check_output(["docker", "inspect", "quant-company-worker-1"]))[0]
path = Path("/var/lib/quant-company/research/provisioned/data-evidence/registry.json")
raw = path.read_bytes()
registry = json.loads(raw)
query = f"""BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SELECT json_build_object('observed_at',now(),
 'program',(SELECT json_build_object('state',state,'digest',manifest_digest,'approval',approval_event_id)
   FROM research_programs WHERE id='{PROGRAM}'),
 'tasks',(SELECT coalesce(json_agg(json_build_object('id',id,'state',state) ORDER BY created_at),'[]'::json)
   FROM research_program_tasks WHERE program_id='{PROGRAM}'),
 'mission_count',(SELECT count(*) FROM research_missions WHERE program_id='{PROGRAM}'),
 'reservation_count',(SELECT count(*) FROM research_program_reservations WHERE program_id='{PROGRAM}'),
 'scientific_trials',(SELECT count(*) FROM research_program_reservations
   WHERE program_id='{PROGRAM}' AND scientific_trial)); ROLLBACK;"""
result = subprocess.check_output([
    "docker", "exec", "-u", "postgres", "quant-company-postgres-1", "psql", "-X", "-A", "-t",
    "-v", "ON_ERROR_STOP=1", "-d", "quant_company", "-c", query,
], text=True)
state = next(json.loads(line) for line in result.splitlines() if line.startswith("{"))
print(json.dumps({
    "active_release_commit": Path("/opt/quant-company/current").resolve().name,
    "worker": {"id": worker["Id"], "image": worker["Config"]["Image"], "state": worker["State"]["Status"]},
    "registry_sha256": hashlib.sha256(raw).hexdigest(),
    "packets": [{"envelope": p["envelope"], "report_names": list(p["reports"]),
                 "blocking_gaps": p["blocking_gaps"]} for p in registry["packets"]],
    "program_state": state,
}, ensure_ascii=False, sort_keys=True))
