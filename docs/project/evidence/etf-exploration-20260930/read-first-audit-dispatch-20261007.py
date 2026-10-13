"""Read queue, pause and container metadata for the fixed resumed audit turn."""

import json
import subprocess
from datetime import UTC, datetime

QUERY = """
SELECT json_build_object(
 'next_turn',(SELECT row_to_json(x) FROM (SELECT t.id,t.status,t.workflow_started,t.due_at,t.error,
   t.created_at,t.updated_at,k.priority,k.status AS task_state,k.agent
   FROM turns t JOIN tasks k ON k.id=t.task_id WHERE t.id='8520ba60-39ff-52e3-8af4-bb3a02ffcad0')x),
 'runtime_control',(SELECT row_to_json(x) FROM (SELECT paused_until,reason FROM runtime_control WHERE id=1)x),
 'pending_starts',(SELECT count(*) FROM turns WHERE NOT workflow_started AND status='queued'),
 'queue',(SELECT coalesce(json_agg(x),'[]'::json) FROM (SELECT t.id,t.status,t.workflow_started,t.error,
   k.agent,k.priority,k.kind,t.created_at,t.updated_at FROM turns t JOIN tasks k ON k.id=t.task_id
   WHERE t.status IN ('queued','running','waiting') ORDER BY k.priority,t.created_at LIMIT 10)x))
"""
value = json.loads(subprocess.check_output([
    "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company",
    "-qAt", "-v", "ON_ERROR_STOP=1", "-c", QUERY,
], text=True))
containers = json.loads(subprocess.check_output(["docker", "inspect", "quant-company-api-1",
    "quant-company-worker-1", "quant-company-dispatch-1", "quant-company-codex-runtime-1",
    "quant-company-account-gateway-1"], text=True))
value["containers"] = [{"name": row["Name"], "image": row["Config"]["Image"],
    "state": row["State"]["Status"], "health": row["State"].get("Health", {}).get("Status")}
    for row in containers]
print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), **value}, indent=2))
