"""Read public lifecycle states without model payloads, credentials or replay."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

query = """SELECT json_build_object(
 'running_turns',(SELECT count(*) FROM turns WHERE status='running'),
 'active_jobs',(SELECT count(*) FROM research_jobs WHERE state IN ('queued','claimed','running','uncertain','cancel_requested')),
 'sending_outbox',(SELECT count(*) FROM outbox WHERE status='sending'),
 'model_calls',(SELECT json_agg(c) FROM (SELECT m.request_id,m.input_digest,m.profile,m.revision,m.state,m.updated_at,
   q.state AS quant_call_state,q.error AS quant_call_error,q.completed_at AS quant_completed_at
   FROM model_account_calls m LEFT JOIN quant_feed_calls q ON q.id=m.request_id
   WHERE m.state IN ('dispatching','uncertain') ORDER BY m.updated_at) c),
 'runtime_pause',(SELECT row_to_json(r) FROM runtime_control r WHERE id=1))"""
result = json.loads(subprocess.check_output(['docker', 'exec', 'quant-company-postgres-1', 'psql',
    '-U', 'postgres', '-d', 'quant_company', '-qAt', '-v', 'ON_ERROR_STOP=1', '-c', query], timeout=30))
for call in result['model_calls'] or []:
    path = Path('/var/lib/quant-company/codex/jobs') / (call['request_id'] + '.json')
    if path.is_file():
        receipt = json.loads(path.read_bytes())
        call['runtime_receipt'] = {key: receipt.get(key) for key in
            ('version', 'request_id', 'input_digest', 'state', 'account', 'started_at', 'completed_at')}
        call['runtime_fault_code'] = receipt.get('fault', {}).get('code')
    else:
        call['runtime_receipt'] = None
print(json.dumps({'observed_at': datetime.now(UTC).isoformat(), **result,
    'production_mutated': False, 'model_requests_replayed': 0}))
