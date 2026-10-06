"""Inspect only public live/desired mount identities; never print Compose environments or credentials."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

base = Path(__file__).resolve().parent
source = '5c44ad07273adc780a56a47d7d35114fd0dc32c8'
override = Path('/var/lib/quant-company/config') / ('native-output-' + source + '-01.compose.json')
record = json.loads((Path('/var/lib/quant-company/releases') / ('native-output-' + source + '-01.json')).read_bytes())
assert record['phase'] == 'held_before_activation' and record['selected_services_attempted'] == []
results = {}
for service in record['selected_services']:
    name = 'quant-company-' + service + '-1'
    row = json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]
    assert row['Id'] == record['before'][name]['id'] and row['Image'] == record['before'][name]['image_id']
    files = row['Config']['Labels']['com.docker.compose.project.config_files'].split(',')
    command = ['docker', 'compose', '--project-name', 'quant-company', '--profile', '*',
        '--env-file', '/var/lib/quant-company/config/runtime.env',
        *[arg for path in files for arg in ('-f', path)], '-f', str(override), 'config', '--format', 'json']
    config = json.loads(subprocess.check_output(command, timeout=60))
    desired = config['services'][service]
    expected = {(m['Source'], m['Destination'], m['RW']) for m in row['Mounts'] if m['Type'] == 'bind'}
    actual = {(m['source'], m['target'], not m.get('read_only', False))
        for m in desired.get('volumes', []) if m['type'] == 'bind'}
    for secret in desired.get('secrets', []):
        target = secret.get('target', secret['source'])
        if not target.startswith('/'):
            target = '/run/secrets/' + target
        actual.add((config['secrets'][secret['source']]['file'], target, False))
    results[service] = {'compose_files': files, 'expected_count': len(expected), 'desired_count': len(actual),
        'match': expected == actual, 'missing_live_mounts': sorted(expected - actual),
        'additional_desired_mounts': sorted(actual - expected)}
print(json.dumps({'schema_version': 1, 'observed_at': datetime.now(UTC).isoformat(),
    'phase': 'read_only_composition_diagnosis', 'services': results,
    'credentials_read': False, 'production_mutated': False}))
