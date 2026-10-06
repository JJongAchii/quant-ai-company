"""Official model/list from the staged runtime images. Does not start model turns."""

import json
import pathlib
import subprocess


def run(args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError("catalog_probe_failed:" + args[0])
    return result.stdout


stage = json.loads(pathlib.Path("/var/lib/quant-company/releases/model-assignments-20261006-stage.json").read_text())
if stage["state"] != "staged":
    raise RuntimeError("runtime_images_not_staged")
profile = run(["docker", "exec", "-u", "postgres", "quant-company-postgres-1", "psql", "-XAt",
               "-d", "quant_company", "-c", "SELECT profile FROM model_account_policy WHERE id=1"]).strip()
if profile not in {"primary", "backup"}:
    raise RuntimeError("account_selection_invalid")
for name in ("codex-runtime", "quant-codex-runtime"):
    before = next(r for r in stage["targets"] if r["Name"] == "/quant-company-" + name + "-1")
    image = stage["images"][before["Image"]]
    networks = json.loads(run(["docker", "network", "inspect", *before["NetworkSettings"]["Networks"]]))
    egress = [n["Name"] for n in networks if not n["Internal"]]
    if len(egress) != 1:
        raise RuntimeError("catalog_probe_egress_ambiguous")
    network = egress[0]
    args = ["docker", "run", "--rm", "--read-only", "--memory=384m", "--cpus=.5", "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true", "--network=" + network, "--tmpfs", "/tmp:size=64m,mode=1777"]
    env = dict(item.split("=", 1) for item in before["Config"]["Env"])
    if env.get("PYTHONPATH"):
        args.extend(["--env", "PYTHONPATH=" + env["PYTHONPATH"]])
    for mount in before["Mounts"]:
        if mount["Destination"] in {"/state/auth", "/state/backup-auth"}:
            args.extend(["--mount", "type=bind,source=" + mount["Source"] + ",target=" + mount["Destination"]])
    code = """import asyncio,json,pathlib
from quant_company.providers.codex_runner import CodexRunner,RunnerConfig
from quant_company.providers.model_catalog import read_catalog
runner=CodexRunner(RunnerConfig(codex_home=pathlib.Path('/state/auth'),backup_codex_home=pathlib.Path('/state/backup-auth'),jobs_dir=pathlib.Path('/tmp/catalog-probe-jobs')))
rows=asyncio.run(read_catalog(runner,PROFILE))
print(json.dumps({'profile':PROFILE,'models':rows,'model_turns':0}))""".replace("PROFILE", repr(profile))
    result = json.loads(run([*args, "--entrypoint", "/app/.venv/bin/python", image["id"], "-c", code]))
    print(json.dumps({"runtime": name, "image": image["id"], "scope": "real official CLI and selected ChatGPT account",
                      **result}), flush=True)
