"""Read only repair contract shapes and worker failure identities, never metrics."""

import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime

QUERY = """
BEGIN READ ONLY;
SET LOCAL statement_timeout='15s';
SELECT json_build_object(
 'stage',(SELECT row_to_json(x) FROM (
   SELECT id,stage,actor,state,attempt,error,retry_at,task_id,result,context,updated_at
   FROM research_mission_stages WHERE id='2d0ec857-8601-5cca-927d-f7f51877696d')x),
 'attempts',(SELECT coalesce(json_agg(x),'[]'::json) FROM (
   SELECT attempt,error,response,created_at,completed_at
   FROM research_stage_attempts WHERE stage_id='2d0ec857-8601-5cca-927d-f7f51877696d'
   ORDER BY attempt DESC LIMIT 5)x),
 'request',(SELECT row_to_json(x) FROM (
   SELECT t.id,t.request->>'prompt' AS prompt
   FROM turns t JOIN research_mission_stages s ON s.task_id=t.task_id
   WHERE s.id='2d0ec857-8601-5cca-927d-f7f51877696d'
   ORDER BY t.created_at DESC LIMIT 1)x));
COMMIT;
"""


def shape(value):
    if not isinstance(value, dict):
        return {"type": type(value).__name__}
    patches = value.get("patches")
    content = json.dumps(value, ensure_ascii=False)
    dispositions = {key: item for key in ("repair_disposition", "repair_status", "ready_for_retry")
                    if isinstance((item := value.get(key)), bool)
                    or (isinstance(item, str) and re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", item))}
    return {
        "keys": sorted(value),
        "dispositions": dispositions,
        "requests_failed_code": any(item in content for item in ("failed candidate", "failed code", "실패 코드", "실패한 코드")),
        "requests_failure_logs": any(item in content for item in ("stderr", "오류 로그", "실패 로그")),
        "rationale_nonempty": isinstance(value.get("rationale"), str)
        and bool(value["rationale"].strip()),
        "patches_type": type(patches).__name__,
        "patch_count": len(patches) if isinstance(patches, list) else None,
        "patches": [{"keys": sorted(patch), "path": patch.get("path"),
                     "expected_text_type": type(patch.get("expected_text")).__name__,
                     "expected_text_characters": len(patch.get("expected_text") or ""),
                     "replacement_text_characters": len(patch.get("replacement_text") or "")}
                    for patch in patches if isinstance(patch, dict)]
        if isinstance(patches, list) else [],
        "repair_source_ids": value.get("repair_source_ids"),
        "sha256": hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest(),
    }


def main():
    data = json.loads(subprocess.check_output([
        "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres",
        "-d", "quant_company", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", QUERY,
    ], text=True))
    stage = data["stage"]
    stage["result_shape"] = shape(stage.pop("result"))
    context = stage.pop("context")
    stage["context_keys"] = sorted(context)
    stage["available_files"] = [{"name": item.get("name"), "sha256": item.get("sha256")}
                                for item in context.get("available_files", [])]
    for attempt in data["attempts"]:
        response = attempt.pop("response") or {}
        decision = response.get("decision", {})
        attempt["decision_status"] = decision.get("status")
        attempt["artifact_shapes"] = []
        for artifact in decision.get("artifacts", []):
            content = artifact.get("content", "")
            try:
                attempt["artifact_shapes"].append(shape(json.loads(content, strict=False)))
            except (TypeError, ValueError):
                attempt["artifact_shapes"].append({"parse_error": True})
    request = data["request"]
    prompt = request.pop("prompt")
    marker = "\nMISSION DATA JSON:\n"
    instruction, payload = prompt.split(marker, 1)
    payload = json.loads(payload)
    request["stage_instruction"] = instruction.split("\nSTAGE: repair\n", 1)[-1]
    request["output_schema"] = payload.get("output_schema")
    request["last_error"] = payload.get("last_error")
    request["file_progress"] = payload.get("file_progress")
    print(json.dumps({"observed_at": datetime.now(UTC).isoformat(),
                      "database_mutated": False, "performance_metrics_read_or_returned": False,
                      **data}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
