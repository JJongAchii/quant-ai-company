"""Read only public data-stage request identities and fixed runtime fault labels."""

import json
import pathlib
import re
import subprocess
from datetime import UTC, datetime

query = """
SELECT coalesce(json_agg(t),'[]'::json) FROM (
 SELECT t.id,t.task_id,t.status,t.error,t.created_at,t.updated_at,
 t.request->>'request_id' AS request_id,t.request->>'model' AS model,
 t.request->>'reasoning_effort' AS reasoning_effort,
 length(t.request->>'prompt') AS prompt_characters
 FROM turns t JOIN tasks k ON k.id=t.task_id
 WHERE k.project_id='9aac0de4-2b97-5195-a720-287d324234f3' AND k.agent='data'
 AND k.created_at>'2026-10-05T23:13:50Z' ORDER BY t.created_at DESC LIMIT 5)t
"""
rows = json.loads(subprocess.check_output([
    "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", query
], text=True))
for row in rows:
    identity = row["request_id"]
    if not identity or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", identity):
        continue
    path = pathlib.Path("/var/lib/quant-company/codex/jobs") / (identity + ".json")
    if not path.is_file():
        row["runtime_receipt_present"] = False
        continue
    receipt = json.loads(path.read_text())
    row["runtime_receipt_present"] = True
    row["runtime"] = {k: receipt.get(k) for k in ["state", "started_at", "completed_at", "cli_version", "requested_execution"]}
    fault = receipt.get("fault", {})
    row["runtime"]["fault_code"] = fault.get("code")
    message = fault.get("message", "")
    row["runtime"]["parse_phase"] = re.findall(r"\(([a-z_]+):([a-z_]+)\)", message)
stage_query = """
WITH latest_stage AS (
 SELECT * FROM research_mission_stages
 WHERE program_id='f7deaf96-e677-5afe-93d4-18ac387043bb' AND stage='program_data'
 ORDER BY created_at DESC,id DESC LIMIT 1
)
SELECT json_build_object(
 'stage',(SELECT row_to_json(s) FROM (SELECT id,state,attempt,error,retry_at,task_id,
   context->'required_data_reads' AS required_data_reads
   FROM latest_stage)s),
 'recent_reads',(SELECT json_agg(r) FROM (SELECT r.path,r.character_offset,r.next_offset,r.created_at
   FROM research_stage_reads r JOIN latest_stage s ON s.id=r.stage_id AND r.attempt=s.attempt
   ORDER BY r.created_at DESC LIMIT 8)r),
 'progress',(SELECT json_agg(r) FROM (SELECT DISTINCT ON(r.path) r.path,r.next_offset
   FROM research_stage_reads r JOIN latest_stage s ON s.id=r.stage_id AND r.attempt=s.attempt
   ORDER BY r.path,r.character_offset DESC)r))
"""
stage = json.loads(subprocess.check_output([
    "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", stage_query
], text=True))
print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), "requests": rows, "stage_progress": stage}, indent=2))
