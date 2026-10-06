"""Read only timing and output shapes; no proposal content or measured results."""

import json
import subprocess

QUERY = """
SELECT coalesce(json_agg(t),'[]'::json) FROM (
 SELECT t.id,t.status,t.error,t.created_at,t.updated_at,
   jsonb_array_length(coalesce(t.response->'decision'->'artifacts','[]'::jsonb)) AS artifact_count,
   jsonb_array_length(coalesce(t.response->'decision'->'tools','[]'::jsonb)) AS tool_count,
   t.response->'decision'->>'status' AS response_status,
   (SELECT json_build_object('path',r.path,'offset',r.character_offset,'next_offset',r.next_offset)
    FROM research_stage_reads r WHERE r.stage_id='c3050fd0-0139-53ec-8669-56dff53ab630'
    AND r.created_at=t.updated_at LIMIT 1) AS read
 FROM turns t WHERE t.task_id='d7df0261-af8f-5c89-b8ef-a59e3bb9b6f1'
 ORDER BY t.created_at DESC,t.id DESC LIMIT 16
)t
"""

value = json.loads(subprocess.check_output([
    "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company",
    "-qAt", "-v", "ON_ERROR_STOP=1", "-c", QUERY,
], text=True))
print(json.dumps(value, indent=2))
