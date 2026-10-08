"""Qualified worker-only image overlay, with a durable receipt and no request replay."""

import fcntl
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
QUALIFIED = json.loads((ROOT / "qualification.json").read_text())
STATE = Path("/var/lib/quant-company")
JOURNAL = ROOT / "cutover.json"
WORKER = "quant-company-worker-1"


def run(args, timeout=60):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Operator command failed: " + args[0])
    return result.stdout.strip()


def sql(query):
    return json.loads(run(["docker", "exec", "-u", "postgres", "quant-company-postgres-1", "psql",
                           "-XAt", "-v", "ON_ERROR_STOP=1", "-d", "quant_company", "-c", query]))


def hashes(query):
    return {row["id"]: hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            for row in sql(query)}


def snapshot():
    names = [name for name in run(["docker", "ps", "--format", "{{.Names}}"]).splitlines()
             if name.startswith("quant-company-")]
    full = json.loads(run(["docker", "inspect", *names]))
    public = {item["Name"].lstrip("/"): {"id": item["Id"], "image_id": item["Image"], "running": item["State"]["Running"]}
              for item in full}
    return public, next(item for item in full if item["Name"] == "/" + WORKER)


def save(record):
    temporary = JOURNAL.with_suffix(".tmp")
    with temporary.open("w") as file:
        json.dump(record, file, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, JOURNAL)


def mounts(container):
    return sorted((row["Type"], row["Source"], row["Destination"], row["RW"]) for row in container["Mounts"])


os.umask(0o077)
with (STATE / ".backup.lock").open("a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if JOURNAL.exists():
        receipt = json.loads(JOURNAL.read_text())
        assert receipt["state"] == "active", "Cutover outcome needs reconciliation; do not replay"
        print(json.dumps(receipt, indent=2))
        raise SystemExit(0)
    before, old = snapshot()
    assert old["Id"] == QUALIFIED["previous_container_id"] and old["Image"] == QUALIFIED["base_image_id"]
    candidate = json.loads(run(["docker", "image", "inspect", QUALIFIED["target_image"]]))[0]
    assert candidate["Id"] == QUALIFIED["target_image_id"], "Qualified image changed"
    for field in ("User", "Entrypoint", "Cmd", "WorkingDir", "Env"):
        assert candidate["Config"][field] == json.loads(run(["docker", "image", "inspect", old["Image"]]))[0]["Config"][field]
    old_env = dict(pair.split("=", 1) for pair in old["Config"]["Env"])
    overlay = STATE / "config" / ("worker-role-compat-" + QUALIFIED["candidate_company_sha256"][:16] + ".compose.json")
    # This private operator file retains installed values; environment contents are never exported.
    overlay.write_text(json.dumps({"services": {"worker": {
        "image": QUALIFIED["target_image"], "environment": old_env,
        "mem_limit": old["HostConfig"]["Memory"], "cpus": old["HostConfig"]["NanoCpus"] / 1e9,
    }}}))
    labels = old["Config"]["Labels"]
    envfile = labels["com.docker.compose.project.environment_file"]
    files = QUALIFIED["compose_files"] + [str(overlay)]
    command = ["docker", "compose", "--project-name", QUALIFIED["project"], "--profile", "*", "--env-file", envfile]
    for name in files:
        command += ["-f", name]
    resolved = json.loads(run(command + ["config", "--format", "json"]))["services"]["worker"]
    assert resolved["image"] == QUALIFIED["target_image"]
    assert dict((key, str(value)) for key, value in resolved["environment"].items()) == old_env
    assert int(resolved["mem_limit"]) == old["HostConfig"]["Memory"]
    assert abs(float(resolved["cpus"]) * 1e9 - old["HostConfig"]["NanoCpus"]) < 1
    active = sql("SELECT (SELECT count(*) FROM turns WHERE status='running' AND response IS NULL) + "
                 "(SELECT count(*) FROM staff_runs WHERE state='running') + "
                 "(SELECT count(*) FROM staff_independent_reviews WHERE state IN ('running','pending') AND request IS NOT NULL AND response IS NULL)")
    assert active == 0, "In-flight model calls require reconciliation before worker restart"
    reviews_query = "SELECT COALESCE(jsonb_agg(to_jsonb(s)),'[]'::jsonb) FROM staff_independent_reviews s WHERE state='blocked' AND error='uncertain'"
    calls_query = "SELECT COALESCE(jsonb_agg(to_jsonb(s)),'[]'::jsonb) FROM maintenance_calls s WHERE created_at<'2026-10-07T00:00:00Z'::timestamptz"
    reviews = hashes(reviews_query)
    calls = hashes(calls_query)
    global_release = run(["readlink", "-f", "/opt/quant-company/current"])
    record = {
        "state": "cutover_started", "started_at": datetime.now(UTC).isoformat(), "patch_commit": QUALIFIED["patch_commit"],
        "base_image": old["Config"]["Image"], "base_image_id": old["Image"], "target_image": QUALIFIED["target_image"],
        "target_image_id": candidate["Id"], "global_release": global_release, "before": before,
        "compose_files": files, "envfile": envfile, "override": str(overlay),
        "uncertain_review_sha256": reviews, "historical_maintenance_call_sha256": calls,
        "rollback": {"base_image": old["Config"]["Image"], "instruction": "Retain this exact worker-only stack and installed environment. Set only worker.image to base_image. Reconcile any in-flight request before retry; keep stable request IDs."},
    }
    save(record)
    run(command + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", "worker"], timeout=420)
    after, new = snapshot()
    assert new["Image"] == candidate["Id"] and new["State"]["Running"] and not new["State"]["OOMKilled"]
    assert new["Config"]["Labels"]["quant-company.turn-context-patch"] == QUALIFIED["patch_commit"]
    assert dict(pair.split("=", 1) for pair in new["Config"]["Env"]) == old_env
    for field in ("User", "Entrypoint", "Cmd", "WorkingDir"):
        assert new["Config"][field] == old["Config"][field]
    for field in ("ReadonlyRootfs", "CapDrop", "SecurityOpt", "Memory", "NanoCpus", "PidsLimit", "Tmpfs"):
        assert new["HostConfig"][field] == old["HostConfig"][field]
    assert mounts(new) == mounts(old), "Mount boundaries changed"
    assert set(new["NetworkSettings"]["Networks"]) == set(old["NetworkSettings"]["Networks"])
    assert all(after.get(name) == value for name, value in before.items() if name != WORKER), "Another service changed"
    assert run(["readlink", "-f", "/opt/quant-company/current"]) == global_release
    reviews_after, calls_after = hashes(reviews_query), hashes(calls_query)
    assert all(reviews_after.get(key) == value for key, value in reviews.items()), "Uncertain review changed"
    assert all(calls_after.get(key) == value for key, value in calls.items()), "Historical call changed"
    module_probe = "import hashlib;from pathlib import Path;import quant_company.company as c;print(hashlib.sha256(Path(c.__file__).read_bytes()).hexdigest())"
    assert run(["docker", "exec", WORKER, "python", "-c", module_probe]) == QUALIFIED["candidate_company_sha256"]
    record.update(candidate_company_sha256=QUALIFIED["candidate_company_sha256"], state="active", completed_at=datetime.now(UTC).isoformat(), after=after,
                  environment_unchanged=True, container_boundaries_unchanged=True,
                  uncertain_reviews_unchanged=True, historical_maintenance_calls_unchanged=True)
    save(record)
    print(json.dumps(record, indent=2))
