"""Read actual app/worker image identities and Python source hashes; no credentials."""

import json
import subprocess
from datetime import UTC, datetime

NAMES = ["quant-company-api-1", "quant-company-worker-1"]
code = r'''
import hashlib,json,pathlib
root = pathlib.Path('/app/src')
files = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*.py'))}
targets = ['quant_company/research/audit_delivery.py','quant_company/research/mission_backend.py']
others = {name: digest for name, digest in files.items() if name not in targets}
digest = hashlib.sha256(json.dumps(others,sort_keys=True,separators=(',',':')).encode()).hexdigest()
print(json.dumps({'source_count':len(files),'patch_files':{name:files[name] for name in targets},'other_sources_digest':digest}))
'''
rows = json.loads(subprocess.check_output(["docker", "inspect", *NAMES], text=True))
result = []
for row in rows:
    assert row["State"]["Running"]
    files = json.loads(subprocess.check_output(["docker", "exec", row["Id"], "python", "-c", code], text=True))
    result.append({"name": row["Name"].removeprefix("/"), "container_id": row["Id"],
        "image": row["Config"]["Image"], "image_id": row["Image"],
        "compose_files": row["Config"]["Labels"]["com.docker.compose.project.config_files"].split(","),
        "state": row["State"]["Status"], **files})
print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), "services": result}, indent=2))
