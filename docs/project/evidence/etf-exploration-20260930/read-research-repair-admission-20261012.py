"""Read account waits, queued turn metadata and failed-evidence read receipts."""

import json
import subprocess
from datetime import UTC, datetime

QUERY = """
BEGIN READ ONLY;
SET LOCAL statement_timeout='10s';
SELECT json_build_object(
 'account_policy',(SELECT json_build_object('profile',profile,'revision',revision)
   FROM model_account_policy WHERE id=1),
 'accounts',(SELECT json_agg(x) FROM (SELECT profile,authentication,paused_until,last_fault,last_success_at
   FROM model_accounts ORDER BY profile)x),
 'stage',(SELECT json_build_object('id',s.id,'state',s.state,'attempt',s.attempt,'task_id',s.task_id,
   'task_status',t.status,'retry_at',s.retry_at,'error',s.error,
   'recovery',s.context->'_repair_recovery','hold_present',s.context ? '_repair_hold',
   'hold_required_evidence_count',jsonb_array_length(s.context->'_repair_hold'->'required_evidence'))
   FROM research_mission_stages s LEFT JOIN tasks t ON t.id=s.task_id
   WHERE s.id='2d0ec857-8601-5cca-927d-f7f51877696d'),
 'recent_turns',(SELECT coalesce(json_agg(x),'[]'::json) FROM (
   SELECT u.id,u.sequence,u.status,u.due_at,u.workflow_started,u.attempts,
     u.request IS NOT NULL AS request_frozen,u.error,u.updated_at,c.profile,c.state AS account_call_state
   FROM turns u JOIN research_mission_stages s ON u.task_id=s.task_id
   LEFT JOIN model_account_calls c ON c.request_id=u.id::text
   WHERE s.id='2d0ec857-8601-5cca-927d-f7f51877696d'
   ORDER BY u.sequence DESC LIMIT 4)x),
 'repair_evidence_reads',(SELECT coalesce(json_agg(x),'[]'::json) FROM (
   SELECT r.attempt,r.path,r.character_offset,length(r.content) AS characters,
     r.next_offset,r.sha256,r.created_at
   FROM research_stage_reads r JOIN research_mission_stages s ON r.stage_id=s.id
   WHERE s.id='2d0ec857-8601-5cca-927d-f7f51877696d' AND r.attempt=s.attempt
     AND (r.path LIKE 'failed-code/%' OR r.path='repair/failed-build.json'
       OR r.path=(SELECT e->>'file' FROM jsonb_array_elements(s.context->'evidence_sources') e
                  WHERE e->>'source_id'='research-failure:69a52b87-3b37-56c1-9ac5-97c5819c4746'))
   ORDER BY r.created_at LIMIT 80)x));
COMMIT;
"""


def main():
    result = json.loads(subprocess.check_output([
        "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres",
        "-d", "quant_company", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", QUERY,
    ], text=True, timeout=30))
    result.update(observed_at=datetime.now(UTC).isoformat(), database_mutated=False,
                  credentials_prompts_or_performance_exported=False)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
