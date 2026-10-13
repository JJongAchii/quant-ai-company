"""Read fixed first-mission audit delivery metadata; omit evidence and performance prose."""

import hashlib
import json
import pathlib
import re
import subprocess
from datetime import UTC, datetime

QUERY = """
SELECT json_build_object(
 'mission',(SELECT row_to_json(x) FROM (SELECT id,state,cycle,updated_at
   FROM research_missions WHERE id='2ce40574-6368-5cef-b706-b4e67441b3de')x),
 'stage',(SELECT row_to_json(x) FROM (SELECT id,stage,actor,state,attempt,error,retry_at,task_id,
   context->'_audit_hold' AS hold,context->'_audit'->>'delivery_version' AS delivery_version,
   context->'_audit'->'required_reads' AS required_reads,created_at,updated_at
   FROM research_mission_stages WHERE id='5e5e2013-373c-5868-9fc9-242985e085b3')x),
 'turns',(SELECT coalesce(json_agg(x),'[]'::json) FROM (SELECT id,sequence,status,error,
   request->>'request_id' AS request_id,request->'session' AS session,
   request->>'output_contract' AS output_contract,response IS NOT NULL AS response_present,
   created_at,updated_at FROM turns WHERE task_id='a88948c2-58fc-5aa6-897a-76a691dd51dc'
   ORDER BY sequence)x),
 'packets',(SELECT coalesce(json_agg(x),'[]'::json) FROM (SELECT turn_id,attempt,digest,
   jsonb_array_length(chunks) AS chunk_count,notes IS NOT NULL AS notes_present,reviewed_at
   FROM research_audit_packets WHERE stage_id='5e5e2013-373c-5868-9fc9-242985e085b3')x),
 'stages',(SELECT json_agg(x) FROM (SELECT id,stage,actor,state,error,created_at,updated_at
   FROM research_mission_stages WHERE mission_id='2ce40574-6368-5cef-b706-b4e67441b3de'
   ORDER BY created_at DESC LIMIT 4)x))
"""

raw = subprocess.check_output([
    "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company",
    "-qAt", "-v", "ON_ERROR_STOP=1", "-c", QUERY,
], text=True)
value = json.loads(raw)
for turn in value["turns"]:
    identity = turn["request_id"]
    if not identity or not re.fullmatch(r"[0-9a-f-]{36}", identity):
        continue
    path = pathlib.Path("/var/lib/quant-company/codex/jobs") / (identity + ".json")
    if not path.is_file():
        continue
    record = json.loads(path.read_bytes())
    turn["runtime_receipt_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    turn["runtime"] = {key: record.get(key) for key in
        ["state", "started_at", "completed_at", "cli_version"]}
    turn["runtime_keys"] = sorted(record)
    turn["runtime"]["fault_code"] = record.get("fault", {}).get("code")
    if isinstance(record.get("result"), dict):
        response = record["result"]
        artifacts = response.get("decision", {}).get("artifacts", [])
        turn["runtime_response"] = {
            "provider": response.get("provider"), "thread_id": response.get("thread_id"),
            "request_id": response.get("request_id"), "artifact_count": len(artifacts),
            "input_digest": record.get("input_digest"),
        }
        parsed = []
        for artifact in artifacts:
            content = artifact.get("content", "")
            try:
                body = json.loads(content)
            except (ValueError, TypeError):
                parsed.append({"json_valid": False})
                continue
            parsed.append({"json_valid": True, "body_type": type(body).__name__,
                "keys": sorted(body) if isinstance(body, dict) else None,
                "packet_digest": body.get("packet_digest") if isinstance(body, dict) else None,
                "notes_characters": len(body.get("notes", "")) if isinstance(body, dict)
                    and isinstance(body.get("notes", ""), str) else None,
                "notes_stripped_characters": len(body.get("notes", "").strip()) if isinstance(body, dict)
                    and isinstance(body.get("notes", ""), str) else None})
        turn["runtime_response"]["artifact_shapes"] = parsed
print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), **value}, indent=2))
