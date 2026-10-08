"""Defer new turns briefly while existing calls finish; restore the previous admission control with CAS."""
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
MARKER = "operator-maintainer-repair-68ffc8d1"
PRIVATE = ROOT / "admission-original.json"
PUBLIC = ROOT / "admission-during-cutover.json"
os.umask(0o077)


def sql(query):
    result = subprocess.run(["docker", "exec", "-u", "postgres", "quant-company-postgres-1",
        "psql", "-XqAt", "-v", "ON_ERROR_STOP=1", "-d", "quant_company", "-c", query],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, "Admission command failed"
    return json.loads(result.stdout)


def quote(value):
    return "NULL" if value is None else "'" + value.replace("'", "''") + "'"


assert not PRIVATE.exists(), "Prior admission effect requires reconciliation"
previous = sql("SELECT to_jsonb(r) FROM runtime_control r WHERE id=1")
assert sql("SELECT to_jsonb(paused_until IS NULL OR paused_until<=now()) FROM runtime_control WHERE id=1"), "Preserve an active operator pause"
PRIVATE.write_text(json.dumps({"state": "reserved", "previous": previous}) + "\n")
record = {"state": "reserved", "started_at": datetime.now(UTC).isoformat(),
    "scope": "Temporary admission deferral; existing requests are not cancelled or replayed",
    "previous_control_sha256": hashlib.sha256(json.dumps(previous, sort_keys=True).encode()).hexdigest(),
    "lease_seconds": 120, "database_writes": 0}
PUBLIC.write_text(json.dumps(record, indent=2) + "\n")
process = None
last = None
try:
    last = sql("WITH changed AS (UPDATE runtime_control SET paused_until=now()+interval '120 seconds',reason=" +
        quote(MARKER) + " WHERE id=1 AND paused_until IS NOT DISTINCT FROM " + quote(previous["paused_until"]) +
        "::timestamptz AND reason IS NOT DISTINCT FROM " + quote(previous["reason"]) +
        " RETURNING *) SELECT COALESCE((SELECT to_jsonb(changed) FROM changed),'null'::jsonb)")
    assert last, "Admission changed concurrently"
    record["database_writes"] += 1
    record["state"] = "waiting_for_original_calls"
    PUBLIC.write_text(json.dumps(record, indent=2) + "\n")
    with (ROOT / "cutover-v2.stdout.json").open("w") as output, (ROOT / "cutover-v2.stderr.txt").open("w") as error:
        process = subprocess.Popen(["python3", str(ROOT / "operator-cutover.py"), str(ROOT)], stdout=output, stderr=error)
        renewal = time.monotonic()
        while process.poll() is None:
            if time.monotonic() - renewal >= 30:
                renewed = sql("WITH changed AS (UPDATE runtime_control SET paused_until=now()+interval '120 seconds' " +
                    "WHERE id=1 AND reason=" + quote(MARKER) + " AND paused_until=" + quote(last["paused_until"]) +
                    "::timestamptz RETURNING *) SELECT COALESCE((SELECT to_jsonb(changed) FROM changed),'null'::jsonb)")
                assert renewed, "Another operator changed admission; reconcile without overwriting it"
                last = renewed
                record["database_writes"] += 1
                renewal = time.monotonic()
            time.sleep(1)
    assert process.returncode == 0, "Cutover requires reconciliation; original admission will be restored"
    record["state"] = "applied"
finally:
    # Wait for an already launched operator effect before restoring admission.
    if process is not None and process.poll() is None:
        process.wait(timeout=420)
    if last is not None:
        restored = sql("WITH changed AS (UPDATE runtime_control SET paused_until=" + quote(previous["paused_until"]) +
            ",reason=" + quote(previous["reason"]) + " WHERE id=1 AND reason=" + quote(MARKER) +
            " AND paused_until=" + quote(last["paused_until"]) + "::timestamptz RETURNING *) " +
            "SELECT COALESCE((SELECT to_jsonb(changed) FROM changed),'null'::jsonb)")
        record["previous_admission_restored"] = restored == previous
        record["database_writes"] += int(restored is not None)
        assert restored == previous, "Admission changed independently; do not overwrite or retry it"
    record["completed_at"] = datetime.now(UTC).isoformat()
    PUBLIC.write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps(record))
