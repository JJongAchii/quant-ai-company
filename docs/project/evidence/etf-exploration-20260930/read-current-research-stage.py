"""Read the latest programme/child-mission stage and its fixed runtime metadata."""

import json
import pathlib
import re
import subprocess
from datetime import UTC, datetime

query = """
WITH latest AS (
 SELECT s.* FROM research_mission_stages s LEFT JOIN research_missions m ON m.id=s.mission_id
 WHERE s.program_id='f7deaf96-e677-5afe-93d4-18ac387043bb'
 OR m.program_id='f7deaf96-e677-5afe-93d4-18ac387043bb'
 ORDER BY s.created_at DESC,s.id DESC LIMIT 1
)
SELECT json_build_object(
 'stage',(SELECT row_to_json(x) FROM (SELECT id,stage,actor,state,attempt,error,retry_at,task_id,created_at,updated_at FROM latest)x),
 'requests',(SELECT coalesce(json_agg(x),'[]'::json) FROM (SELECT t.id,t.status,t.error,t.created_at,t.updated_at,
 t.request->>'request_id' AS request_id,t.request->>'model' AS model,
 t.request->>'reasoning_effort' AS reasoning_effort FROM turns t JOIN latest s ON s.task_id=t.task_id
 ORDER BY t.created_at DESC LIMIT 3)x),
 'reads',(SELECT coalesce(json_agg(x),'[]'::json) FROM (SELECT r.path,r.character_offset,r.next_offset,r.created_at
 FROM research_stage_reads r JOIN latest s ON s.id=r.stage_id AND s.attempt=r.attempt
 ORDER BY r.created_at DESC LIMIT 3)x))
"""
value = json.loads(subprocess.check_output([
    "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company",
    "-qAt", "-v", "ON_ERROR_STOP=1", "-c", query
], text=True))
for request in value["requests"]:
    identity = request["request_id"]
    if not identity or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", identity):
        continue
    receipt = pathlib.Path("/var/lib/quant-company/codex/jobs") / (identity + ".json")
    if receipt.is_file():
        runtime = json.loads(receipt.read_bytes())
        request["runtime"] = {key: runtime.get(key) for key in ["state", "started_at", "completed_at", "cli_version"]}
        request["runtime"]["fault_code"] = runtime.get("fault", {}).get("code")
        request["runtime"]["parse_phase"] = re.findall(r"\(([a-z_]+):([a-z_]+)\)", runtime.get("fault", {}).get("message", ""))
print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), **value}, indent=2))
