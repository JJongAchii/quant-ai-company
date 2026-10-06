"""Read operational attempt failures; never return scientific response prose."""

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
 'stage',(SELECT row_to_json(s) FROM (SELECT id,stage,actor,state,attempt,error,retry_at,task_id FROM latest)s),
 'attempts',(SELECT coalesce(json_agg(a),'[]'::json) FROM (
   SELECT a.attempt,a.task_id,a.error,a.completed_at,t.status AS task_status,t.priority
   FROM research_stage_attempts a JOIN latest s ON s.id=a.stage_id JOIN tasks t ON t.id=a.task_id
   ORDER BY a.attempt DESC LIMIT 3)a),
 'context_navigation_present',(SELECT context ? 'source_completion_navigation' FROM latest),
 'source_contract_hint',(SELECT context->'_source_completion_hint' FROM latest),
 'challenges',(SELECT coalesce(json_agg(c),'[]'::json) FROM (
   SELECT id,proposal_id,payload->>'reviewer' AS reviewer,created_at
   FROM research_mission_challenges WHERE mission_id='2ce40574-6368-5cef-b706-b4e67441b3de'
   ORDER BY created_at DESC LIMIT 3)c),
 'decision_shapes',(SELECT coalesce(json_agg(a),'[]'::json) FROM (
   SELECT a.attempt,a.response->'artifact'->>'decision' AS decision,
     (SELECT coalesce(json_agg(r->>'challenge_id'),'[]'::json)
      FROM jsonb_array_elements(coalesce(a.response->'artifact'->'responses','[]'::jsonb))r)
      AS response_challenge_ids
   FROM research_stage_attempts a JOIN latest s ON s.id=a.stage_id
   ORDER BY a.attempt DESC LIMIT 3)a)
)
"""
value = json.loads(subprocess.check_output([
    'docker', 'exec', 'quant-company-postgres-1', 'psql', '-U', 'postgres', '-d', 'quant_company',
    '-qAt', '-v', 'ON_ERROR_STOP=1', '-c', QUERY,
], text=True))
print(json.dumps({'observed_at': datetime.now(UTC).isoformat(), **value}, indent=2))
