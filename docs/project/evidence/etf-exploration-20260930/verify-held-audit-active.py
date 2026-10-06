"""Read active source bytes, core health and connection counts without secrets."""

import hashlib
import json
import pathlib
import subprocess
from datetime import UTC, datetime

SOURCE = "defd4833adea6a0191412121a5a5c6e8265c58ba"
ROOT = pathlib.Path("/opt/quant-company/releases") / SOURCE
assert pathlib.Path("/opt/quant-company/current").resolve() == ROOT
expected = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
    for p in (ROOT / "src").rglob("*") if p.is_file()}
assert len(expected) == 205
probe = "import hashlib,json,pathlib; print(json.dumps({str(p.relative_to('/app')):hashlib.sha256(p.read_bytes()).hexdigest() for p in pathlib.Path('/app/src').rglob('*') if p.is_file() and '__pycache__' not in p.parts}))"
names = subprocess.check_output(["docker", "ps", "--filter", "label=com.docker.compose.project=quant-company",
    "--format", "{{.Names}}"], text=True).splitlines()
rows = json.loads(subprocess.check_output(["docker", "inspect", *names], text=True))
by_name = {r["Name"].removeprefix("/"): r for r in rows}
services = {}
for service in ["api", "dispatch", "slack-socket", "worker", "account-gateway", "codex-runtime"]:
    name = "quant-company-" + service + "-1"
    row = by_name[name]
    assert "COMPANY_CODE_COMMIT=" + SOURCE in row["Config"]["Env"]
    assert row["State"]["Running"] and SOURCE in row["Config"]["Image"]
    actual = json.loads(subprocess.check_output(["docker", "exec", name, "python", "-c", probe], text=True))
    assert actual == expected, "active_source_bytes_differ"
    health = row["State"].get("Health", {}).get("Status")
    if service in {"api", "account-gateway", "codex-runtime"}:
        assert health == "healthy", "core_health_not_ready"
    services[name] = {"image": row["Config"]["Image"], "image_id": row["Image"],
        "health": health, "source_files_verified": len(expected)}
tcp_probe = "import json,pathlib; print(json.dumps({'established_tcp_443':sum(row.split()[3]=='01' and int(row.split()[2].split(':')[1],16)==443 for row in pathlib.Path('/proc/net/tcp').read_text().splitlines()[1:])}))"
tcp = json.loads(subprocess.check_output(["docker", "exec", "quant-company-slack-socket-1", "python", "-c", tcp_probe], text=True))
receipt = {"schema_version": 1, "observed_at": datetime.now(UTC).isoformat(),
    "source_commit": SOURCE, "state": "six_active_services_source_verified",
    "services": services, "running_service_count": len(names),
    "socket_established_tcp_443": tcp["established_tcp_443"],
    "config_sha256": {name: hashlib.sha256((pathlib.Path('/var/lib/quant-company/config') / name).read_bytes()).hexdigest()
        for name in ["roles.json", "research-profiles.json", "research-qlab.json", "runtime.env"]},
    "scientific_trials_added_by_operator": 0}
print(json.dumps(receipt, indent=2))
