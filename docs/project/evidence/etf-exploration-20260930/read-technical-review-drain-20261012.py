"""Read owned cutover receipt and native lock owners without secrets or commands."""

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

STATE = Path("/var/lib/quant-company")
COMMIT = "68a53f1c699837256db5e8a4a6479bbd0fc82876"
receipt = json.loads((STATE / "releases" / ("technical-review-" + COMMIT + "-applied.json")).read_bytes())
locks = []
kernel = Path("/proc/locks").read_text().splitlines()
for name in ("codex/jobs/.runtime.lock", "codex/jobs/.runtime-news.lock",
             "codex/jobs/.runtime-brief.lock", "codex/jobs/.runtime-quant.lock", "claude/jobs/.runtime.lock"):
    path = STATE / name
    if not path.exists():
        continue
    stat = path.stat()
    owners = []
    for line in kernel:
        fields = line.split()
        if "FLOCK" not in fields or "->" in fields:
            continue
        process, device = fields[4:6]
        major, minor, inode = device.split(":")
        if (int(major, 16), int(minor, 16), int(inode)) == (os.major(stat.st_dev), os.minor(stat.st_dev), stat.st_ino):
            comm = Path("/proc") / process / "comm"
            owners.append({"pid": int(process), "process_name": comm.read_text().strip() if comm.is_file() else None})
    locks.append({"relative_lock": name, "owners": owners})
running = []
for path in (STATE / "claude/jobs").glob("*.json"):
    value = json.loads(path.read_bytes())
    if value.get("state") == "running":
        running.append({key: value.get(key) for key in ("request_id", "state", "started_at", "cli_version")})
dump_files = [{"staging": path.parent.name, "bytes": path.stat().st_size,
               "modified_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()}
              for path in (STATE / "backups").glob("staging-*/database.dump")]
dump_state = json.loads(subprocess.check_output([
    "docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company",
    "-qAt", "-v", "ON_ERROR_STOP=1", "-c", "BEGIN READ ONLY; SELECT coalesce(json_agg(x),'[]'::json) FROM "
    "(SELECT pid,state,wait_event_type,wait_event,pg_blocking_pids(pid) AS blockers "
    "FROM pg_stat_activity WHERE application_name='pg_dump')x; COMMIT;",
], text=True, timeout=15))
print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), "state": receipt["state"],
                  "started_at": receipt["started_at"], "native_locks": locks,
                  "running_claude_receipts": running,
                  "incomplete_database_dump_metadata": dump_files,
                  "database_dump_activity": dump_state,
                  "database_or_runtime_mutated": False}, indent=2))
