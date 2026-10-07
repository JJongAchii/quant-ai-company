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
import quant_company,quant_company.research.audit_delivery as audit,quant_company.research.mission_backend as backend
installed=pathlib.Path(quant_company.__file__).parent.parent
runtime_files={str(p.relative_to(installed)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((installed/'quant_company').rglob('*.py'))}
runtime_others={k:v for k,v in runtime_files.items() if k not in targets}
runtime={'root':str(installed),'source_count':len(runtime_files),'notes_bound':audit.MAX_NOTES,
 'patch_files':{name:runtime_files[name] for name in targets},
 'other_sources_digest':hashlib.sha256(json.dumps(runtime_others,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
print(json.dumps({'source_count':len(files),'patch_files':{name:files[name] for name in targets},'other_sources_digest':digest,'runtime':runtime}))
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
