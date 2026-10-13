"""Read only scoped data failure identities and service-owned runtime fault labels."""

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

QUERY = """
SELECT coalesce(json_agg(t),'[]'::json) FROM (
 SELECT t.id,t.task_id,t.status,t.error,t.created_at,t.updated_at,
 t.request->>'request_id' AS request_id,
 t.request->>'output_contract' AS output_contract,
 length(t.request->>'prompt') AS prompt_characters,
 a.stage_id,a.attempt,s.state AS stage_state,s.error AS stage_error
 FROM turns t JOIN research_stage_attempts a ON a.task_id=t.task_id
 JOIN research_mission_stages s ON s.id=a.stage_id
 WHERE s.program_id='f7deaf96-e677-5afe-93d4-18ac387043bb'
 AND s.stage='program_data' AND t.error IS NOT NULL
 ORDER BY t.created_at DESC LIMIT 8)t
"""
rows = json.loads(subprocess.check_output([
    "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company",
    "-qAt", "-v", "ON_ERROR_STOP=1", "-c", QUERY
], text=True))
for row in rows:
    identity = row["request_id"]
    if not identity or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", identity):
        continue
    path = Path("/var/lib/quant-company/codex/jobs") / (identity + ".json")
    if not path.is_file():
        row["runtime_receipt_present"] = False
        continue
    receipt = json.loads(path.read_bytes())
    row["runtime_receipt_present"] = True
    row["runtime"] = {key: receipt.get(key) for key in (
        "state", "started_at", "completed_at", "cli_version", "requested_execution")}
    fault = receipt.get("fault", {})
    row["runtime"]["fault_code"] = fault.get("code")
    row["runtime"]["parse_phase"] = re.findall(r"\(([a-z_]+):([a-z_]+)\)", fault.get("message", ""))
print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), "data_faults": rows}, indent=2))
