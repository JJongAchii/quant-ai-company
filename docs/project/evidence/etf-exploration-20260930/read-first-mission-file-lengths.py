"""Read frozen evidence byte/character counts without returning file contents."""

import json
import subprocess

CODE = r'''
import hashlib
import json
from pathlib import Path
from quant_company.company import Company
from quant_company.config import Settings
company=Company(Settings())
with company.db.transaction() as conn:
    row=conn.execute("SELECT context FROM research_mission_stages WHERE mission_id=%s "
        "ORDER BY created_at DESC,id DESC LIMIT 1",('2ce40574-6368-5cef-b706-b4e67441b3de',)).fetchone()
values=[]
for name,entry in sorted(row['context']['_private_files'].items()):
    assert name.startswith(('mission/','sources/','code/labs/company-domestic-research/'))
    path=Path(entry['path'])
    assert path.is_file() and not path.is_symlink() and path.stat().st_size<2500000
    data=path.read_bytes()
    assert hashlib.sha256(data).hexdigest()==entry['sha256']
    values.append({'name':name,'bytes':len(data),'characters':len(data.decode('utf-8')),
                   'sha256':entry['sha256']})
print(json.dumps({'files':values}))
'''

value = subprocess.run([
    "docker", "exec", "-i", "quant-company-api-1", "python", "/app/entrypoint.py", "python", "-",
], input=CODE, capture_output=True, text=True, timeout=60)
if value.returncode:
    raise RuntimeError("frozen_file_length_read_failed")
print(json.dumps(json.loads(value.stdout), indent=2))
