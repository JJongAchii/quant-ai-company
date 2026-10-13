"""Read exact public cutover identities and preserve ambiguous receipts without replay."""

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

STATE = Path('/var/lib/quant-company')
PROGRAM = 'f7deaf96-e677-5afe-93d4-18ac387043bb'
STAGE = 'ce867fc3-a194-5327-b312-864b93538d34'


def run(args):
    return subprocess.check_output(args, timeout=60)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


names = run(['docker', 'ps', '-a', '--filter', 'label=com.docker.compose.project=quant-company',
             '--format', '{{.Names}}']).decode().splitlines()
rows = json.loads(run(['docker', 'inspect', *names]))
containers = {}
for row in rows:
    env = dict(item.split('=', 1) for item in row['Config']['Env'] if '=' in item)
    containers[row['Name'].lstrip('/')] = {
        'id': row['Id'], 'image': row['Config']['Image'], 'image_id': row['Image'],
        'state': row['State']['Status'], 'health': row['State'].get('Health', {}).get('Status'),
        'config_files': row['Config']['Labels'].get('com.docker.compose.project.config_files'),
        'pins': {key: env[key] for key in ('COMPANY_CODE_COMMIT', 'QDATA_CODE_COMMIT',
                   'RESEARCH_COMPANY_CODE_COMMIT') if key in env},
    }
query = f"""SELECT json_build_object(
 'running_turns',(SELECT count(*) FROM turns WHERE status='running'),
 'active_jobs',(SELECT count(*) FROM research_jobs WHERE state IN ('queued','claimed','running','uncertain','cancel_requested')),
 'sending_outbox',(SELECT count(*) FROM outbox WHERE status='sending'),
 'model_calls',(SELECT coalesce(json_agg(c),'[]'::json) FROM
    (SELECT request_id,input_digest,profile,revision,state,updated_at FROM model_account_calls
     WHERE state IN ('dispatching','uncertain') ORDER BY request_id)c),
 'program',(SELECT row_to_json(p) FROM
    (SELECT id,project_id,revision,state,manifest_digest,approval_event_id,approved_at
     FROM research_programs WHERE id='{PROGRAM}')p),
 'data_stage',(SELECT row_to_json(s) FROM
    (SELECT id,state,attempt,task_id,error,retry_at,context->'_program_hold' AS hold,
     context->'_data_output_failures' AS format_failures FROM research_mission_stages WHERE id='{STAGE}')s),
 'task',(SELECT row_to_json(t) FROM
    (SELECT id,digest,state,proposal,data_assessment,decision,mission_id FROM research_program_tasks
     WHERE program_id='{PROGRAM}' ORDER BY created_at,id LIMIT 1)t),
 'scientific_origins',(SELECT coalesce(json_agg(o),'[]'::json) FROM
    (SELECT task_id,lineage_id,task_digest FROM research_scientific_lineage_origins
     WHERE lineage_id='b6a31ea2-8da9-5f67-97dd-2e304cde30c3' ORDER BY task_id)o),
 'scientific_trial_count',(SELECT count(*) FROM research_program_reservations WHERE scientific_trial),
 'lineage_trial_limit',(SELECT trial_limit FROM research_scientific_lineages
     WHERE id='b6a31ea2-8da9-5f67-97dd-2e304cde30c3'),
 'account_policy',(SELECT row_to_json(p) FROM (SELECT profile,revision FROM model_account_policy WHERE id=1)p),
 'runtime_pause',(SELECT row_to_json(p) FROM (SELECT paused_until,reason FROM runtime_control WHERE id=1)p))"""
database = json.loads(run(['docker', 'exec', 'quant-company-postgres-1', 'psql', '-U', 'postgres',
                          '-d', 'quant_company', '-qAt', '-v', 'ON_ERROR_STOP=1', '-c', query]))
database['task_sha256'] = hashlib.sha256(json.dumps(database.pop('task'), sort_keys=True).encode()).hexdigest()
jobs = {}
for directory in ('codex/jobs', 'claude/jobs'):
    active = []
    counts = {}
    for path in (STATE / directory).glob('*.json'):
        receipt = json.loads(path.read_bytes())
        state = receipt.get('state')
        counts[state] = counts.get(state, 0) + 1
        if state == 'running':
            active.append({'request_id': receipt.get('request_id'), 'sha256': sha(path)})
    jobs[directory] = {'active': active, 'counts': counts}
uncertain_receipts = []
for call in database['model_calls']:
    if call['state'] != 'uncertain':
        continue
    path = STATE / 'codex/jobs' / (call['request_id'] + '.json')
    receipt = json.loads(path.read_bytes())
    uncertain_receipts.append({'request_id': call['request_id'], 'receipt_sha256': sha(path),
        'state': receipt['state'], 'fault_code': receipt.get('fault', {}).get('code'),
        'input_digest': receipt['input_digest'], 'account': receipt.get('account')})
managed = STATE / 'research/provisioned/data-evidence/conditional-20261001'
print(json.dumps({'schema_version': 1, 'observed_at': datetime.now(UTC).isoformat(),
    'app_release': str(Path('/opt/quant-company/current').resolve()), 'containers': containers,
    'config_hashes': {name: sha(STATE / 'config' / name) for name in
        ('runtime.env', 'roles.json', 'research-profiles.json', 'research-qlab.json')},
    'registry_sha256': sha(STATE / 'research/provisioned/data-evidence/registry.json'),
    'managed_file_hashes': {path.name: sha(path) for path in managed.iterdir() if path.is_file()},
    'database': database, 'runtime_jobs': jobs, 'ambiguous_receipts': uncertain_receipts,
    'production_mutated': False, 'model_calls_replayed': 0, 'performance_computed': False}))
