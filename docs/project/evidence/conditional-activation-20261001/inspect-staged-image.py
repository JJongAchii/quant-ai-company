"""Public image import identities only; no credentials, network or host data mounts."""

import json
import subprocess

IMAGE = "quant-company:19807db8d1817a3a495ac10eee34b1bac2d287fe"
config = json.loads(subprocess.check_output(["sudo", "docker", "image", "inspect", IMAGE]))[0]
env = dict(item.split("=", 1) for item in config["Config"].get("Env", []) if "=" in item)
probe = """import hashlib,importlib.util,json,pathlib,sys
spec=importlib.util.find_spec('quant_company');root=pathlib.Path(spec.origin).parent
paths=[root/'research/program_contracts.py',pathlib.Path('/opt/company/src/quant_company/research/program_contracts.py'),pathlib.Path('/opt/quant-code/src/quant_company/research/program_contracts.py')]
print(json.dumps({'import_root':str(root),'program_contract_files':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()},'sys_path':sys.path}))
"""
result = json.loads(subprocess.check_output([
    "sudo", "docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp",
    "--entrypoint", "/app/.venv/bin/python", IMAGE, "-B", "-c", probe,
]))
print(json.dumps({"image": IMAGE, "image_id": config["Id"], "pythonpath": env.get("PYTHONPATH"),
                  "working_directory": config["Config"]["WorkingDir"], "probe": result,
                  "operating_services_changed": False}, indent=2))
