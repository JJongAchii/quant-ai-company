"""Read the current signed program's assessment, selection and usage without writes."""

import json
import subprocess
from datetime import UTC, datetime

QUERY = """
SELECT json_build_object(
 'program',(SELECT json_build_object('id',id,'state',state,'revision',revision,'manifest_digest',manifest_digest,
   'approval_event_id',approval_event_id,'approved_at',approved_at)
   FROM research_programs WHERE id='f7deaf96-e677-5afe-93d4-18ac387043bb'),
 'tasks',(SELECT coalesce(json_agg(t),'[]'::json) FROM (SELECT id,state,proposal->>'title' AS title,
   data_assessment,decision,mission_id,created_at FROM research_program_tasks
   WHERE program_id='f7deaf96-e677-5afe-93d4-18ac387043bb' ORDER BY created_at,id)t),
 'stages',(SELECT coalesce(json_agg(s),'[]'::json) FROM (SELECT id,stage,state,attempt,error,retry_at,task_id,
   context->'_data_output_failures' AS data_output_failures FROM research_mission_stages
   WHERE program_id='f7deaf96-e677-5afe-93d4-18ac387043bb' ORDER BY created_at,id)s),
 'usage',(SELECT json_build_object('reservations',count(*),'scientific_trials',count(*) FILTER(WHERE scientific_trial),
   'compute_seconds',coalesce(sum(actual_seconds),0)) FROM research_program_reservations
   WHERE program_id='f7deaf96-e677-5afe-93d4-18ac387043bb'),
 'mission_count',(SELECT count(*) FROM research_missions WHERE program_id='f7deaf96-e677-5afe-93d4-18ac387043bb'),
 'missions',(SELECT coalesce(json_agg(m),'[]'::json) FROM (SELECT id,state,cycle,cycle_trials,cumulative_trials,
   approval_event_id,approved_at,created_at,updated_at FROM research_missions
   WHERE program_id='f7deaf96-e677-5afe-93d4-18ac387043bb' ORDER BY created_at,id)m),
 'mission_stages',(SELECT coalesce(json_agg(s),'[]'::json) FROM (SELECT s.id,s.mission_id,s.stage,s.state,
   s.attempt,s.error,s.retry_at,s.task_id,s.updated_at FROM research_mission_stages s JOIN research_missions m
   ON m.id=s.mission_id WHERE m.program_id='f7deaf96-e677-5afe-93d4-18ac387043bb' ORDER BY s.created_at,s.id)s),
 'trials',(SELECT coalesce(json_agg(t),'[]'::json) FROM (SELECT t.id,t.mission_id,t.cycle,t.state,t.job_id,
   t.created_at,t.updated_at FROM research_mission_trials t JOIN research_missions m ON m.id=t.mission_id
   WHERE m.program_id='f7deaf96-e677-5afe-93d4-18ac387043bb' ORDER BY t.created_at,t.id)t),
 'jobs',(SELECT coalesce(json_agg(j),'[]'::json) FROM (SELECT j.id,j.mission_id,j.task_id,j.recipe_id,
   j.company_commit,j.state,j.approval_event_id,j.approved_at,j.worker_id,j.claimed_at,j.heartbeat_at,
   j.sequence,j.update_digest,j.artifact_sha256,j.error,j.created_at,j.updated_at
   FROM research_jobs j JOIN research_missions m ON m.id=j.mission_id
   WHERE m.program_id='f7deaf96-e677-5afe-93d4-18ac387043bb' ORDER BY j.created_at,j.id)j)
)
"""

value = json.loads(subprocess.check_output([
    "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company",
    "-qAt", "-v", "ON_ERROR_STOP=1", "-c", QUERY
], text=True))
print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), **value}, ensure_ascii=False, indent=2))
