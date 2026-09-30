"""Read-only, secret-free host observation; no model calls or service mutations."""

import json
import os
import runpy
import sys
from datetime import UTC, datetime
from pathlib import Path

helper = runpy.run_path(sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).with_name("preview_overlay.py")))
inspect, env = helper["inspect"], helper["env"]
worker = inspect("quant-company-quant-feed-worker-1")
settings = env(worker)
disk = os.statvfs("/")
memory = {line.split(":", 1)[0]: int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines()
          if line.startswith(("MemTotal:", "MemAvailable:"))}
services = helper["inventory"]()
names = helper["run"](["docker", "ps", "-a", "--format", "{{.Names}}"])
protocol = json.loads(helper["run"]([
    "docker", "exec", "quant-company-codex-runtime-1", "python", "-c",
    "import json; from quant_company.contracts import ProviderRequest; "
    "print(json.dumps(ProviderRequest.model_json_schema()['properties'].get('output_contract', {}).get('enum', "
    "['agent_decision'])))",
]))
print(json.dumps({
    "checked_at": datetime.now(UTC).isoformat(),
    "current_release": helper["CURRENT"].resolve().name,
    "quant_collection_enabled": settings.get("QUANT_FEED_ENABLED") == "true",
    "quant_publication_enabled": settings.get("QUANT_FEED_PUBLISH_ENABLED") == "true",
    "quant_worker_revision": worker["Config"].get("Labels", {}).get("org.opencontainers.image.revision"),
    "runtime_output_contracts": protocol,
    "activity": helper["activity"](),
    "disk_available_bytes": disk.f_bavail * disk.f_frsize,
    "memory_kib": memory,
    "preview_containers_present": [name for name in names.splitlines() if name.startswith("quant-structural-preview-")],
    "services": services,
}, indent=2))
