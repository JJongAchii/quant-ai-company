"""Read public container identities and storage/backup lifecycle only; never print credentials or model payloads."""

import json
import shutil
import subprocess
from datetime import UTC, datetime

ids = subprocess.check_output(['docker', 'ps', '-aq', '--filter', 'name=quant-company-'], text=True).split()
containers = json.loads(subprocess.check_output(['docker', 'inspect', *ids]))
states = {}
for container in containers:
    state = container['State']
    env = dict(line.split('=', 1) for line in container['Config']['Env'] if '=' in line)
    states[container['Name'].removeprefix('/')] = {
        'id': container['Id'], 'image_id': container['Image'], 'image': container['Config']['Image'],
        'state': state['Status'], 'health': state.get('Health', {}).get('Status'),
        'oom_killed': state['OOMKilled'], 'storage_start_failure': 'no space left on device' in state['Error'],
        'started_at': state['StartedAt'], 'finished_at': state['FinishedAt'],
        'company_commit': env.get('COMPANY_CODE_COMMIT'), 'qdata_commit': env.get('QDATA_CODE_COMMIT')}
disk = shutil.disk_usage('/')
backup = subprocess.check_output(['systemctl', 'show', 'quant-company-backup.service', '-p', 'ActiveState',
    '-p', 'SubState', '-p', 'Result', '-p', 'ExecMainStatus', '-p', 'ExecMainStartTimestamp',
    '-p', 'ExecMainExitTimestamp'], text=True)
print(json.dumps({'schema_version': 1, 'observed_at': datetime.now(UTC).isoformat(),
    'containers': states, 'disk': {'total_bytes': disk.total, 'used_bytes': disk.used, 'free_bytes': disk.free},
    'last_scheduled_backup': dict(line.split('=', 1) for line in backup.splitlines()),
    'production_mutated': False, 'secrets_printed': False, 'scientific_trials_invoked': 0}))
