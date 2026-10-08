"""Read service source identities and compose guards without exporting credentials."""

import json
import subprocess
from datetime import UTC, datetime

CODE = r'''
import hashlib,json,pathlib,quant_company
targets=['quant_company/research/controller.py']
def sources(root):
    files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
           for p in sorted((root/'quant_company').rglob('*.py'))}
    other={key:value for key,value in files.items() if key not in targets}
    return {'root':str(root),'source_count':len(files),
            'patch_files':{key:files[key] for key in targets},
            'other_sources_digest':hashlib.sha256(json.dumps(other,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
print(json.dumps({'source':sources(pathlib.Path('/app/src')),
                  'runtime':sources(pathlib.Path(quant_company.__file__).parent.parent)}))
'''


def main():
    names = ["quant-company-api-1", "quant-company-worker-1"]
    rows = json.loads(subprocess.check_output(["docker", "inspect", *names]))
    result = []
    for row in rows:
        sources = json.loads(subprocess.check_output([
            "docker", "exec", row["Id"], "python", "-c", CODE,
        ], text=True))
        result.append({"name": row["Name"].removeprefix("/"), "container_id": row["Id"],
                       "image": row["Config"]["Image"], "image_id": row["Image"],
                       "state": row["State"]["Status"], "memory_limit": row["HostConfig"]["Memory"],
                       "compose_files": row["Config"]["Labels"]["com.docker.compose.project.config_files"].split(","),
                       "compose_working_dir": row["Config"]["Labels"]["com.docker.compose.project.working_dir"],
                       **sources})
    print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), "services": result}, indent=2))


if __name__ == "__main__":
    main()
