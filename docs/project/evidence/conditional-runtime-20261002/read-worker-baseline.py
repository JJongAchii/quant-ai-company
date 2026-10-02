"""Read worker pins and public service status without tokens or candidate execution."""

import hashlib
import json
import platform
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def run(args):
    return subprocess.check_output(args, text=True, timeout=30).strip()


path = Path("/home/achii/.config/quant-company/research-worker.json")
raw = path.read_bytes()
config = json.loads(raw)
repo = Path(config["company_repo"])
units = {}
for name in ("research-worker.service", "research-worker-tunnel.service"):
    values = run(["systemctl", "--user", "show", name, "--property=ActiveState,SubState,MainPID,WorkingDirectory,ExecMainStartTimestamp"])
    units[name] = dict(row.split("=", 1) for row in values.splitlines())
    if name == 'research-worker.service':
        command = run(['systemctl', '--user', 'show', name, '-p', 'ExecStart', '--value'])
        executable = re.search(r'path=([^ ;]+)', command).group(1)
        units[name]['exec_path'] = executable
        units[name]['exec_command_sha256'] = hashlib.sha256(command.encode()).hexdigest()
        units[name]['exec_python_version'] = run([executable, '--version'])
print(json.dumps({
    "schema_version": 1, "observed_at": datetime.now(UTC).isoformat(),
    "hostname": platform.node(), "gpu": run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"]),
    "config_sha256": hashlib.sha256(raw).hexdigest(), "company_commit": config["company_commit"],
    "company_repo": str(repo), "checkout_commit": run(["git", "-C", str(repo), "rev-parse", "HEAD"]),
    "checkout_changes": run(["git", "-C", str(repo), "status", "--porcelain"]),
    "adaptive_profiles": config["adaptive_profiles"], "state_dir": config["state_dir"],
    "release_registry_file": config.get("release_registry_file"), "units": units,
    "python": run(["/home/achii/quant-company-qualification/autonomous-20260921/company/.venv/bin/python", "--version"]),
    "bubblewrap": run(["/usr/bin/bwrap", "--version"]),
    "configuration_mutated": False, "model_or_performance_execution": False,
}, sort_keys=True, indent=2))
