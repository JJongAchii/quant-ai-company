"""Read native lane locks and pending receipt identities without prompt contents."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def main():
    roots = [Path("/var/lib/quant-company/codex/jobs"), Path("/var/lib/quant-company/claude/jobs")]
    locks = Path("/proc/locks").read_text().splitlines()
    lanes = []
    pending = []
    for root in roots:
        for name in (".runtime.lock", ".runtime-news.lock", ".runtime-brief.lock", ".runtime-quant.lock"):
            path = root / name
            if path.exists():
                inode = path.stat().st_ino
                held = any("FLOCK" in line and any(field.endswith(":" + str(inode))
                           for field in line.split()) for line in locks)
                lanes.append({"path": str(path), "held": held})
        for path in sorted(root.glob("*.json")):
            if datetime.now(UTC).timestamp() - path.stat().st_mtime > 86400:
                continue
            row = json.loads(path.read_bytes())
            if row.get("state") in {"running", "uncertain"}:
                pending.append({key: row.get(key) for key in (
                    "request_id", "state", "started_at", "completed_at", "execution_lane")})
    native = subprocess.check_output([
        "docker", "top", "quant-company-codex-runtime-1", "-eo", "pid,comm",
    ], text=True, timeout=15).splitlines()
    print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), "lanes": lanes,
                      "recent_pending_receipts": pending, "native_process_names": native,
                      "database_mutated": False, "prompts_or_credentials_exported": False}))


if __name__ == "__main__":
    main()
