"""Read service source identities and compose guards without exporting credentials."""

import argparse
import json
import os
import pwd
import subprocess
from datetime import UTC, datetime
from pathlib import Path

CODE = r'''
import hashlib,json,pathlib,quant_company
targets=['quant_company/research/controller.py','quant_company/research/builds.py','quant_company/research/mission_backend.py']
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", type=Path)
    args = parser.parse_args()
    names = ["quant-company-api-1", "quant-company-worker-1"]
    rows = json.loads(subprocess.check_output(["docker", "inspect", *names]))
    result = []
    for row in rows:
        sources = json.loads(subprocess.check_output([
            "docker", "exec", row["Id"], "python", "-c", CODE,
        ], text=True))
        if args.export:
            assert args.export.is_absolute() and args.export.parent == Path("/tmp")
            user = pwd.getpwnam("ubuntu")
            args.export.mkdir(exist_ok=True, mode=0o700)
            os.chown(args.export, user.pw_uid, user.pw_gid)
            service = row["Name"].removeprefix("/quant-company-").removesuffix("-1")
            for mode, inventory in sources.items():
                for name in inventory["patch_files"]:
                    target = args.export / (service + "-" + mode + "-" + Path(name).name)
                    subprocess.run(["docker", "cp", row["Id"] + ":" + inventory["root"] + "/" + name,
                                    str(target)], check=True, capture_output=True)
                    target.chmod(0o600)
                    os.chown(target, user.pw_uid, user.pw_gid)
        result.append({"name": row["Name"].removeprefix("/"), "container_id": row["Id"],
                       "image": row["Config"]["Image"], "image_id": row["Image"],
                       "state": row["State"]["Status"], "memory_limit": row["HostConfig"]["Memory"],
                       "compose_files": row["Config"]["Labels"]["com.docker.compose.project.config_files"].split(","),
                       "compose_working_dir": row["Config"]["Labels"]["com.docker.compose.project.working_dir"],
                       **sources})
    print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), "services": result}, indent=2))


if __name__ == "__main__":
    main()
