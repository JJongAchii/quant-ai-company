"""Read-only host verification. Run as root; never emits credential/environment files."""

import hashlib
import importlib.util
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

OPERATION = Path("/var/lib/quant-company/operations/quant-feed-resume-20260930")
COMMIT = "5defb8cb9dd7c655214663b05670cd2896006b30"
SELECTED = {"quant-company-" + name + "-1" for name in ("api", "dispatch", "quant-feed-worker")}


def main():
    operator_path = OPERATION / "quant_feed_quality_release.py"
    spec = importlib.util.spec_from_file_location("quant_resume_operator", operator_path)
    operator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(operator)
    raw = (Path("/var/lib/quant-company/releases") / ("quant-feed-resume-" + COMMIT + ".json")).read_bytes()
    journal = json.loads(raw)
    before, after = journal["service_inventory_before"], operator.inventory()
    independent = {name: after.get(name) == row for name, row in before.items() if name not in SELECTED}
    timer = "quant-feed-v20-observe.timer"
    timer_state = {arg: subprocess.run(["systemctl", arg, timer], capture_output=True, text=True,
                                       check=False).stdout.strip() for arg in ("is-active", "is-enabled")}
    memory = {line.split(":", 1)[0]: int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines()
              if line.startswith(("MemTotal:", "MemAvailable:"))}
    disk = os.statvfs("/")
    probe = operator.publication_probe()
    current = Path("/opt/quant-company/current").resolve().name
    checks = {
        "current_release_unchanged": current == COMMIT,
        "publication_gate_active": probe["publish_enabled"] and probe["authorized"],
        "selected_services_healthy": all(
            after[name]["running"] and not after[name]["oom"] and after[name]["restarts"] == 0
            and after[name]["health"] in (None, "healthy") and after[name]["image_revision"] == COMMIT
            and after[name]["quant_publish"] == "true" for name in SELECTED),
        "no_service_oom": not any(row["oom"] for row in after.values()),
        "memory_above_activation_floor": memory["MemAvailable"] >= 256 * 1024,
        "waived_observation_timer_disabled": timer_state == {"is-active": "inactive", "is-enabled": "disabled"},
    }
    print(json.dumps({"checked_at": datetime.now(UTC).isoformat(), "checks": checks,
                      "current_release": current, "publication_probe": probe,
                      "activation_journal_sha256": hashlib.sha256(raw).hexdigest(),
                      "operator_sha256": hashlib.sha256(operator_path.read_bytes()).hexdigest(),
                      "activation_phase": journal["phase"], "activation_started_at": journal["started_at"],
                      "activation_completed_at": journal["completed_at"],
                      "independent_services_preserved_at_activation": journal["independent_services_preserved"],
                      "independent_inventory_still_unchanged": independent,
                      "services": after, "disk_available_bytes": disk.f_bavail * disk.f_frsize,
                      "memory_kib": memory, "observation_timer": timer_state,
                      "model_calls": 0, "slack_writes": 0, "service_mutations": 0}, indent=2))
    if not all(checks.values()):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
