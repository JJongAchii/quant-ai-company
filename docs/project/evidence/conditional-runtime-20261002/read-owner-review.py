"""Read actual owner notice delivery and signed program state without message contents."""

import json
import subprocess
from datetime import UTC, datetime

query = """SELECT json_build_object(
 'notice',(SELECT row_to_json(n) FROM (SELECT id,status,sent_ts,attempts,agent,channel,thread_ts
   FROM outbox WHERE id='59444618-2248-572f-a162-4225b6202b98') n),
 'programs',(SELECT json_agg(p) FROM (SELECT id,state,manifest_digest,approval_event_id
   FROM research_programs WHERE project_id='9aac0de4-2b97-5195-a720-287d324234f3' ORDER BY created_at) p),
 'actual_jobs',(SELECT count(*) FROM research_jobs WHERE state IN ('queued','claimed','running','uncertain','cancel_requested')),
 'actual_scientific_trials',(SELECT count(*) FROM research_program_reservations WHERE scientific_trial),
 'runtime_pause',(SELECT row_to_json(r) FROM runtime_control r WHERE id=1))"""
result = json.loads(subprocess.check_output(['docker', 'exec', 'quant-company-postgres-1', 'psql',
    '-U', 'postgres', '-d', 'quant_company', '-qAt', '-v', 'ON_ERROR_STOP=1', '-c', query], timeout=30))
print(json.dumps({'schema_version': 1, 'observed_at': datetime.now(UTC).isoformat(), **result,
    'owner_approval_fabricated': False}))
