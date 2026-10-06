"""Report only lock owner process metadata and Python script paths; never print arguments."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

locks = json.loads(subprocess.check_output(['lslocks', '--json', '--output', 'PID,COMMAND,PATH']))['locks']
rows = []
for lock in locks:
    if lock['path'] != '/var/lib/quant-company/.backup.lock':
        continue
    process = Path('/proc') / str(lock['pid'])
    paths = [value.decode() for value in (process / 'cmdline').read_bytes().split(b'\0')
        if value.startswith(b'/') and value.endswith(b'.py')]
    children = (process / 'task' / str(lock['pid']) / 'children').read_text().split()
    rows.append({**lock, 'script_paths': paths, 'children': [{
        'pid': int(pid), 'comm': (Path('/proc') / pid / 'comm').read_text().strip()} for pid in children]})
print(json.dumps({'observed_at': datetime.now(UTC).isoformat(), 'locks': rows,
    'arguments_or_credentials_printed': False}))
