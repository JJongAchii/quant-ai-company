"""Read identities/state of the two observed malformed priority requests."""

import json
import subprocess
from datetime import UTC, datetime

QUERY = """
SELECT coalesce(json_agg(r),'[]'::json) FROM (
 SELECT t.id AS turn_id,t.status AS turn_status,t.request IS NULL AS request_absent,
   length(t.request->>'prompt') AS prompt_characters,t.request->>'model' AS model,
   t.workflow_started,t.due_at,k.id AS task_id,k.agent,k.kind,k.status AS task_status,
   k.priority,k.revision AS task_revision,p.id AS project_id,p.revision AS project_revision,
   p.status AS project_status,p.owner_user,p.channel,p.thread_ts,
   p.owner_user=(SELECT owner_user FROM projects WHERE id='9aac0de4-2b97-5195-a720-287d324234f3') AS same_owner
 FROM turns t JOIN tasks k ON k.id=t.task_id JOIN projects p ON p.id=k.project_id
 WHERE t.id IN ('ffc34b0d-5504-5463-b6dc-2e954cbb408b','2e4de4d4-00b8-50d6-8dc4-1765a28e1807')
 ORDER BY t.created_at,t.id
)r
"""
value = json.loads(subprocess.check_output([
    'docker', 'exec', 'quant-company-postgres-1', 'psql', '-U', 'postgres', '-d', 'quant_company',
    '-qAt', '-v', 'ON_ERROR_STOP=1', '-c', QUERY,
], text=True))
print(json.dumps({'observed_at': datetime.now(UTC).isoformat(), 'requests': value}, indent=2))
