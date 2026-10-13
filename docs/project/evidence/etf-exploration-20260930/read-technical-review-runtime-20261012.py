"""Hash actual source/runtime parents and optionally export reviewed code only."""
import argparse
import json
import os
import pwd
import subprocess
from datetime import UTC, datetime
from pathlib import Path

CODE = r'''
import hashlib,json,pathlib,quant_company
targets=['quant_company/research/'+name for name in
         ('controller.py','mission_backend.py','missions.py','scientific_lineages.py','technical_review.py','mission_schema.sql')]
def sources(root):
    files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
           for p in sorted((root/'quant_company').rglob('*')) if p.is_file() and p.suffix in ('.py','.sql')}
    other={key:value for key,value in files.items() if key not in targets}
    return {'root':str(root),'source_count':sum(name.endswith('.py') for name in files),'file_count':len(files),
            'patch_files':{key:files.get(key) for key in targets},
            'other_sources_digest':hashlib.sha256(json.dumps(other,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
print(json.dumps({'source':sources(pathlib.Path('/app/src')),
                  'runtime':sources(pathlib.Path(quant_company.__file__).parent.parent)}))
'''

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--export",type=Path)
    args=parser.parse_args()
    rows=json.loads(subprocess.check_output(["docker","inspect","quant-company-api-1","quant-company-worker-1"]))
    result=[]
    for row in rows:
        inventories=json.loads(subprocess.check_output(["docker","exec",row["Id"],"python","-c",CODE],text=True))
        if args.export:
            assert args.export.is_absolute() and args.export.parent==Path("/tmp")
            owner=pwd.getpwnam("ubuntu")
            args.export.mkdir(exist_ok=True,mode=0o700)
            os.chown(args.export,owner.pw_uid,owner.pw_gid)
            service=row["Name"].removeprefix("/quant-company-").removesuffix("-1")
            for mode,inventory in inventories.items():
                for name,digest in inventory["patch_files"].items():
                    if digest is None:
                        continue
                    target=args.export/(service+"-"+mode+"-"+Path(name).name)
                    subprocess.run(["docker","cp",row["Id"]+":"+inventory["root"]+"/"+name,str(target)],
                                   check=True,capture_output=True)
                    target.chmod(0o600)
                    os.chown(target,owner.pw_uid,owner.pw_gid)
        result.append({"name":row["Name"].removeprefix("/"),"container_id":row["Id"],
                       "image":row["Config"]["Image"],"image_id":row["Image"],
                       "memory_limit":row["HostConfig"]["Memory"],"state":row["State"]["Status"],
                       "compose_files":row["Config"]["Labels"]["com.docker.compose.project.config_files"].split(","),
                       "compose_working_dir":row["Config"]["Labels"]["com.docker.compose.project.working_dir"],
                       **inventories})
    print(json.dumps({"observed_at":datetime.now(UTC).isoformat(),"services":result},indent=2))

if __name__=="__main__":
    main()
