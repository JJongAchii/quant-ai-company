"""Read public release identities and research authority; no prices or mutation."""

import hashlib
import json
import pathlib
import subprocess
from datetime import UTC, datetime


def run(args):
    return subprocess.check_output(args, timeout=60)


def sql(query):
    return json.loads(run([
        "sudo", "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres",
        "-d", "quant_company", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", query,
    ]))


state = pathlib.Path("/var/lib/quant-company")
names = run(["sudo", "docker", "ps", "-a", "--filter", "label=com.docker.compose.project=quant-company",
             "--format", "{{.Names}}"] ).decode().splitlines()
containers = json.loads(run(["sudo", "docker", "inspect", *names]))
public = {}
for row in containers:
    env = dict(item.split("=", 1) for item in row["Config"].get("Env", []) if "=" in item)
    public[row["Name"].lstrip("/")] = {
        "id": row["Id"], "image": row["Config"]["Image"], "image_id": row["Image"],
        "state": row["State"]["Status"], "health": row["State"].get("Health", {}).get("Status"),
        "config_files": row["Config"].get("Labels", {}).get("com.docker.compose.project.config_files"),
        "pins": {key: env[key] for key in (
            "COMPANY_CODE_COMMIT", "QDATA_CODE_COMMIT", "RESEARCH_COMPANY_CODE_COMMIT",
            "MODEL_RUNTIME_URL", "MODEL_ACCOUNTS_ENABLED", "COMPANY_AUTONOMOUS_RESEARCH_ENABLED",
        ) if key in env},
    }
query = """SELECT json_build_object(
 'project',(SELECT row_to_json(p) FROM (SELECT id,revision,status FROM projects
   WHERE id='9aac0de4-2b97-5195-a720-287d324234f3')p),
 'programs',(SELECT coalesce(json_agg(p),'[]'::json) FROM (SELECT id,project_id,revision,state,
   spec,manifest_digest,approval_event_id,approved_at FROM research_programs
   WHERE project_id='9aac0de4-2b97-5195-a720-287d324234f3' ORDER BY created_at,id)p),
 'tasks',(SELECT coalesce(json_agg(t),'[]'::json) FROM (SELECT t.id,t.program_id,t.digest,t.state,
   t.proposal,t.data_assessment,t.decision,t.mission_id,t.created_at
   FROM research_program_tasks t JOIN research_programs p ON p.id=t.program_id
   WHERE p.project_id='9aac0de4-2b97-5195-a720-287d324234f3' ORDER BY t.created_at,t.id)t),
 'missions',(SELECT coalesce(json_agg(m),'[]'::json) FROM (SELECT id,program_id,state,cumulative_trials,
   cycle_trials,stagnant_trials FROM research_missions WHERE program_id IN
   (SELECT id FROM research_programs WHERE project_id='9aac0de4-2b97-5195-a720-287d324234f3'))m),
 'usage',(SELECT coalesce(json_agg(r),'[]'::json) FROM (SELECT program_id,count(*) AS reservations,
   count(*) FILTER(WHERE scientific_trial) AS scientific_trials,
   count(*) FILTER(WHERE settled_at IS NULL) AS outstanding,
   coalesce(sum(CASE WHEN settled_at IS NULL THEN reserved_seconds ELSE actual_seconds END),0) AS compute_seconds
   FROM research_program_reservations GROUP BY program_id)r),
 'activity',json_build_object(
   'running_turns',(SELECT count(*) FROM turns WHERE status='running'),
   'active_jobs',(SELECT count(*) FROM research_jobs WHERE state IN
     ('queued','claimed','running','cancel_requested','uncertain')),
   'sending_outbox',(SELECT count(*) FROM outbox WHERE status='sending'),
   'runtime_pause',(SELECT row_to_json(c) FROM (SELECT paused_until,reason FROM runtime_control WHERE id=1)c)),
 'lineage_tables_present',to_regclass('public.research_scientific_lineages') IS NOT NULL
);"""
database = sql(query)
config_hashes = {}
for name in ("runtime.env", "roles.json", "research-profiles.json", "research-qlab.json"):
    raw = run(["sudo", "cat", str(state / "config" / name)])
    config_hashes[name] = hashlib.sha256(raw).hexdigest()
registry_raw = run(["sudo", "cat", str(state / "research/provisioned/data-evidence/registry.json")])
registry = json.loads(registry_raw)
print(json.dumps({
    "observed_at": datetime.now(UTC).isoformat(),
    "app_release": run(["readlink", "-f", "/opt/quant-company/current"]).decode().strip(),
    "containers": public, "database": database, "config_hashes": config_hashes,
    "registry_sha256": hashlib.sha256(registry_raw).hexdigest(),
    "registry_packets": [{key: packet.get(key) for key in (
        "program_digest", "envelope", "data_policy_digest", "research_scope", "input_files",
    )} for packet in registry["packets"]],
    "production_mutated": False, "sealed_prices_read": False, "performance_computed": False,
}, ensure_ascii=False, indent=2))
