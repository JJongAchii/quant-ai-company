"""Read-only post-cutover check for repeated program proposals."""

import json
import subprocess
from datetime import UTC, datetime

PROGRAM_ID = "e06537d3-fac3-5c8c-bf25-ddabb3c7e282"
sql = f"""BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SELECT json_build_object(
 'program',(SELECT json_build_object('id',id,'state',state,'manifest_digest',manifest_digest,
   'approval_event_id',approval_event_id) FROM research_programs WHERE id='{PROGRAM_ID}'),
 'tasks',(SELECT coalesce(json_agg(json_build_object('id',id,'state',state,'created_at',created_at)
   ORDER BY created_at,id),'[]'::json) FROM research_program_tasks WHERE program_id='{PROGRAM_ID}'),
 'stages',(SELECT coalesce(json_agg(json_build_object('id',id,'stage',stage,'state',state,
   'attempt',attempt,'created_at',created_at,'updated_at',updated_at,
   'evidence_version',context->>'evidence_version',
   'packet_index',context->'data_evidence_packets',
   'source_files',(SELECT coalesce(json_agg(item ORDER BY item->>'name'),'[]'::json)
     FROM jsonb_array_elements(context->'available_files') item
     WHERE item->>'name' LIKE 'sources/%')) ORDER BY created_at,id),'[]'::json)
   FROM research_mission_stages WHERE program_id='{PROGRAM_ID}'),
 'mission_count',(SELECT count(*) FROM research_missions WHERE program_id='{PROGRAM_ID}'),
 'reservation_count',(SELECT count(*) FROM research_program_reservations WHERE program_id='{PROGRAM_ID}'),
 'scientific_trials',(SELECT count(*) FROM research_program_reservations
   WHERE program_id='{PROGRAM_ID}' AND scientific_trial),
 'worker_last_seen',(SELECT last_seen FROM research_workers WHERE id='worker'));
ROLLBACK;"""
result = subprocess.run(
    ["docker", "exec", "-i", "-u", "postgres", "quant-company-postgres-1",
     "psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-d", "quant_company"],
    input=sql, text=True, capture_output=True, check=True)
database = next(json.loads(line) for line in result.stdout.splitlines() if line.startswith("{"))
print(json.dumps({"schema_version": 1, "observed_at": datetime.now(UTC).isoformat(),
                  "database": database}, sort_keys=True))
