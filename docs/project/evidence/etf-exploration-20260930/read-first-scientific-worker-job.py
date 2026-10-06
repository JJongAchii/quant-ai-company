"""Read physical execution metadata for one job; never return credentials or metrics."""

import hashlib
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

job_id = str(UUID(sys.argv[1]))
configuration = Path('/home/achii/.config/quant-company/research-worker.json')
config = json.loads(configuration.read_bytes())
directory = Path(config['state_dir']) / 'jobs' / job_id
state = json.loads((directory / 'state.json').read_bytes())
assignment = state['assignment']
assert assignment['job_id'] == job_id
manifest = assignment['manifest']
assert manifest['mission_id'] == '2ce40574-6368-5cef-b706-b4e67441b3de'
assert manifest['spec']['execution_profile'] == 'kr-etf-retrospective-v1'

fields = {
    'process.json': ('launch_id', 'started_at'),
    'launch-intent.json': ('launch_id', 'phase'),
    'runtime.json': ('schema_version', 'worker_id', 'hostname', 'gpu', 'code_commit', 'company_commit'),
    'sandbox-qualification.json': ('action', 'exit_code', 'timed_out', 'fixture_only', 'wall_seconds'),
    'sandbox-evaluation.json': ('action', 'exit_code', 'timed_out', 'fixture_only', 'wall_seconds'),
    'receipt.json': ('worker_id', 'hostname', 'gpu', 'company_commit', 'code_commit', 'started_at',
                     'completed_at', 'execution_count', 'qualification_passed', 'sealed_read', 'artifact_sha256'),
    'result.json': ('state', 'reason', 'artifact_sha256'),
}
records = {}
for name, allowed in fields.items():
    path = directory / name
    if not path.is_file():
        records[name] = {'exists': False}
        continue
    data = path.read_bytes()
    value = json.loads(data)
    records[name] = {
        'exists': True, 'sha256': hashlib.sha256(data).hexdigest(),
        'mtime_utc': datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
        'metadata': {key: value[key] for key in allowed if key in value},
    }

print(json.dumps({
    'schema_version': 1, 'observed_at': datetime.now(UTC).isoformat(),
    'job_id': job_id, 'mission_id': manifest['mission_id'], 'trial_id': manifest['trial_id'],
    'hostname': platform.node(), 'company_commit': manifest['company_commit'],
    'execution_profile': manifest['spec']['execution_profile'], 'records': records,
    'uploaded': bool(state.get('uploaded')), 'uncertain_reason': state.get('uncertain_reason'),
    'performance_metrics_read_or_returned': False,
}, indent=2))
