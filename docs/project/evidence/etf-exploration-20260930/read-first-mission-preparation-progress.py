"""Read bounded preparation metadata for the already approved first mission."""

import json
import subprocess
from datetime import UTC, datetime

QUERY = """
WITH latest AS (
 SELECT * FROM research_mission_stages
 WHERE mission_id='2ce40574-6368-5cef-b706-b4e67441b3de'
 ORDER BY created_at DESC,id DESC LIMIT 1
)
SELECT json_build_object(
 'stage',(SELECT row_to_json(s) FROM (SELECT id,stage,actor,state,attempt,error,retry_at,
   task_id,created_at,updated_at FROM latest)s),
 'task',(SELECT row_to_json(t) FROM (SELECT t.id,t.agent,t.status,t.priority,t.revision
   FROM tasks t JOIN latest s ON s.task_id=t.id)t),
 'files',(SELECT context->'available_files' FROM latest),
 'required_lineage_reads',(SELECT context->'required_lineage_reads' FROM latest),
 'read_progress',(SELECT coalesce(json_agg(p),'[]'::json) FROM (SELECT r.path,count(*) AS chunks,
   max(r.character_offset) AS latest_offset,bool_or(r.next_offset IS NULL) AS complete,
   min(r.created_at) AS first_read,max(r.created_at) AS last_read
   FROM research_stage_reads r JOIN latest s ON s.id=r.stage_id AND s.attempt=r.attempt
   GROUP BY r.path ORDER BY max(r.created_at))p),
 'turn_counts',(SELECT coalesce(json_agg(t),'[]'::json) FROM (SELECT t.status,count(*) AS count
   FROM turns t JOIN latest s ON s.task_id=t.task_id GROUP BY t.status)t),
 'last_turn',(SELECT row_to_json(t) FROM (SELECT t.id,t.status,t.created_at,t.updated_at,
   t.request->>'request_id' AS request_id FROM turns t JOIN latest s ON s.task_id=t.task_id
   ORDER BY t.created_at DESC,t.id DESC LIMIT 1)t),
 'urgency_receipt',(SELECT json_build_object('event_id',id,'detail',detail) FROM events
   WHERE project_id='9aac0de4-2b97-5195-a720-287d324234f3'
   AND kind='research_preparation_priority_reconciled'
   AND detail->>'operation_id'='research-current-mission-preparation-urgency:c3050fd0:20261006'
   ORDER BY id LIMIT 1)
)
"""

value = json.loads(subprocess.check_output([
    "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company",
    "-qAt", "-v", "ON_ERROR_STOP=1", "-c", QUERY,
], text=True))
print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), **value}, indent=2))
