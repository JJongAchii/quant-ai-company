"""Read current queued research and higher-priority turn metadata, without effects."""

import json
import subprocess

query = """
SELECT json_build_object(
 'current',(SELECT json_build_object('id',t.id,'status',t.status,'workflow_started',t.workflow_started,
 'due_at',t.due_at,'attempts',t.attempts,'task_status',k.status,'priority',k.priority,
 'task_revision',k.revision,'project_revision',p.revision,'project_status',p.status)
 FROM turns t JOIN tasks k ON k.id=t.task_id JOIN projects p ON p.id=k.project_id
 WHERE t.id='55aa649b-75b0-5c2c-8763-86990be2c3aa'),
 'runtime_control',(SELECT row_to_json(c) FROM runtime_control c WHERE id=1),
 'routing',(SELECT coalesce(json_agg(x),'[]'::json) FROM (SELECT id,status,revision,created_at FROM tasks
 WHERE project_id='9aac0de4-2b97-5195-a720-287d324234f3' AND kind='routing'
 AND status NOT IN ('completed','superseded') ORDER BY created_at)x),
 'running_turns',(SELECT coalesce(json_agg(x),'[]'::json) FROM (SELECT t.id,t.task_id,t.status,t.created_at,t.updated_at,
 k.agent,k.kind,k.project_id,k.priority,t.request->>'model' AS model FROM turns t JOIN tasks k ON k.id=t.task_id
 WHERE t.status='running' ORDER BY t.updated_at DESC LIMIT 8)x),
 'higher_priority',(SELECT coalesce(json_agg(x),'[]'::json) FROM (
 SELECT t.id,t.status,t.due_at,t.workflow_started,t.error,k.id AS task_id,k.agent,k.kind,k.priority,k.project_id,
 k.status AS task_status,t.request->>'model' AS model
 FROM turns t JOIN tasks k ON k.id=t.task_id JOIN projects p ON p.id=k.project_id
 WHERE t.status IN ('queued','waiting') AND t.due_at<=now() AND k.priority<100 AND k.revision=p.revision
 AND (p.status='active' OR k.kind IN ('answer','routing'))
 AND (k.kind='routing' OR NOT EXISTS(SELECT 1 FROM tasks r WHERE r.project_id=p.id AND r.kind='routing'
 AND r.status NOT IN ('completed','superseded'))) ORDER BY k.priority,t.created_at LIMIT 8)x))
"""
value = json.loads(subprocess.check_output([
    "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company",
    "-qAt", "-v", "ON_ERROR_STOP=1", "-c", query
], text=True))
print(json.dumps(value, indent=2))
