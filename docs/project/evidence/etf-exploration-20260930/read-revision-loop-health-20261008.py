"""Read service health and socket TCP metadata; never print logs or credentials."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def main():
    names = ["quant-company-api-1", "quant-company-worker-1", "quant-company-slack-socket-1",
             "quant-company-postgres-1", "quant-company-codex-runtime-1", "quant-company-quant-codex-runtime-1"]
    rows = json.loads(subprocess.check_output(["docker", "inspect", *names], timeout=15))
    socket = next(row for row in rows if row["Name"] == "/quant-company-slack-socket-1")
    connections = 0
    for table in ("tcp", "tcp6"):
        for line in (Path("/proc") / str(socket["State"]["Pid"]) / "net" / table).read_text().splitlines()[1:]:
            fields = line.split()
            if fields[3] == "01" and int(fields[2].rsplit(":", 1)[1], 16) == 443:
                connections += 1
    native = subprocess.check_output([
        "docker", "top", "quant-company-codex-runtime-1", "-eo", "pid,comm,args",
    ], text=True, timeout=15)
    astra_exec = sum("gpt-6-astra" in line and "exec" in line for line in native.splitlines()[1:])
    inode = (Path("/var/lib/quant-company/codex/jobs") / ".runtime.lock").stat().st_ino
    lane_locked = any("FLOCK" in line and any(field.endswith(":" + str(inode)) for field in line.split())
                      for line in Path("/proc/locks").read_text().splitlines())
    print(json.dumps({"observed_at": datetime.now(UTC).isoformat(), "services": [
        {"name": row["Name"].removeprefix("/"), "running": row["State"]["Running"],
         "health": row["State"].get("Health", {}).get("Status"), "oom_killed": row["State"]["OOMKilled"],
         "restart_count": row["RestartCount"]} for row in rows
    ], "socket_namespace_established_tcp443_connections": connections,
        "native_astra_exec_process_count": astra_exec, "native_company_lane_flock_held": lane_locked,
        "signed_slack_round_trip_claimed": False, "database_mutated": False,
        "credentials_or_logs_exported": False}, indent=2))


if __name__ == "__main__":
    main()
