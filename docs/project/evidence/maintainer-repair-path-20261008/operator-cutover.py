"""Apply three qualified overlays with private installed configuration and durable effect receipts."""
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
QUALIFIED = json.loads((ROOT / "qualification.json").read_text())
STATE = Path("/var/lib/quant-company")
JOURNAL = ROOT / "cutover-v2.json"
os.umask(0o077)


def run(args, timeout=90):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Operator command failed: " + args[0])
    return result.stdout.strip()


def sql(query):
    return json.loads(run(["docker", "exec", "-u", "postgres", "quant-company-postgres-1",
        "psql", "-XqAt", "-v", "ON_ERROR_STOP=1", "-d", "quant_company", "-c",
        "BEGIN READ ONLY; " + query + "; COMMIT;"]))


def hashes(query):
    return {str(row["id"]): hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            for row in sql(query)}


def snapshot():
    names = [n for n in run(["docker", "ps", "-a", "--format", "{{.Names}}"]).splitlines()
             if n.startswith("quant-company-")]
    full = json.loads(run(["docker", "inspect", *names]))
    public = {c["Name"].lstrip("/"): {"id": c["Id"], "image_id": c["Image"], "running": c["State"]["Running"]}
              for c in full}
    return public, {c["Name"].lstrip("/"): c for c in full}


def save(record):
    temp = JOURNAL.with_suffix(".tmp")
    with temp.open("w") as file:
        json.dump(record, file, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp, JOURNAL)


def boundaries(old, new):
    for field in ("User", "Entrypoint", "Cmd", "WorkingDir"):
        assert new["Config"][field] == old["Config"][field], field
    assert dict(p.split("=", 1) for p in new["Config"]["Env"]) == dict(p.split("=", 1) for p in old["Config"]["Env"])
    for field in ("ReadonlyRootfs", "CapDrop", "CapAdd", "SecurityOpt", "Memory", "NanoCpus", "PidsLimit", "Tmpfs", "RestartPolicy"):
        assert new["HostConfig"][field] == old["HostConfig"][field], field
    def mounts(container):
        return sorted((r["Type"], r["Source"], r["Destination"], r["RW"]) for r in container["Mounts"])

    assert mounts(new) == mounts(old)
    assert set(new["NetworkSettings"]["Networks"]) == set(old["NetworkSettings"]["Networks"])


def active():
    return sql("""SELECT jsonb_build_object(
        'maintenance_calls',(SELECT count(*) FROM maintenance_calls c JOIN maintenance_jobs j ON j.id=c.job_id
            WHERE c.response IS NULL AND j.state IN ('review','triage','patch','design','evaluate','publish')),
        'maintenance_jobs',(SELECT count(*) FROM maintenance_jobs
            WHERE state IN ('review','triage','patch','design','evaluate','publish','ci','pr')),
        'running_turns',(SELECT count(*) FROM turns WHERE status='running'),
        'running_staff',(SELECT count(*) FROM staff_runs WHERE state='running'),
        'running_reviews',(SELECT count(*) FROM staff_independent_reviews WHERE state='running'))""")


def claude_running(container):
    env = dict(p.split("=", 1) for p in container["Config"]["Env"])
    mounts = [m for m in container["Mounts"] if m["Destination"] == env["CLAUDE_JOBS_DIR"]]
    assert len(mounts) == 1 and mounts[0]["Type"] == "bind"
    return sum(json.loads(p.read_text()).get("state") == "running" for p in Path(mounts[0]["Source"]).glob("*.json"))


with (STATE / ".backup.lock").open("a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if JOURNAL.exists():
        receipt = json.loads(JOURNAL.read_text())
        assert receipt["state"] == "active", "Prior effects require operator reconciliation; do not replay"
        print(json.dumps(receipt))
        raise SystemExit(0)
    assert QUALIFIED["ci"]["head_sha"] == QUALIFIED["patch_commit"]
    assert QUALIFIED["ci"]["conclusion"] == "success"
    before, old = snapshot()
    release = str(Path("/opt/quant-company/current").resolve())
    commands = {}
    overrides = []
    target_names = {t["name"] for t in QUALIFIED["targets"]}
    for t in QUALIFIED["targets"]:
        c = old[t["name"]]
        assert c["Id"] == t["id"] and c["Image"] == t["base_image_id"] and c["State"]["Running"]
        image = json.loads(run(["docker", "image", "inspect", t["target_image"]]))[0]
        assert image["Id"] == t["target_image_id"]
        for field in ("User", "Entrypoint", "Cmd", "WorkingDir", "Env"):
            assert image["Config"][field] == json.loads(run(["docker", "image", "inspect", c["Image"]]))[0]["Config"][field]
        env = dict(p.split("=", 1) for p in c["Config"]["Env"])
        override = STATE / "config" / ("maintainer-repair-" + QUALIFIED["patch_commit"] + "-" + t["service"] + ".compose.json")
        override.write_text(json.dumps({"services": {t["service"]: {
            "image": t["target_image"], "environment": env,
            "mem_limit": c["HostConfig"]["Memory"], "cpus": c["HostConfig"]["NanoCpus"] / 1e9}}}))
        files = t["compose_files"].split(",") + [str(override)]
        command = ["docker", "compose", "--project-name", t["project"], "--profile", "*", "--env-file", t["envfile"]]
        for file in files:
            command += ["-f", file]
        resolved = json.loads(run(command + ["config", "--format", "json"]))["services"][t["service"]]
        assert resolved["image"] == t["target_image"]
        assert dict((k, str(v)) for k, v in resolved["environment"].items()) == env
        assert int(resolved["mem_limit"]) == c["HostConfig"]["Memory"]
        assert abs(float(resolved["cpus"]) * 1e9 - c["HostConfig"]["NanoCpus"]) < 1
        commands[t["service"]] = command
        overrides.append(str(override))
    counts = active()
    deadline = time.monotonic() + 300
    while any(counts.values()) and time.monotonic() < deadline:
        time.sleep(2)
        counts = active()
    assert all(v == 0 for v in counts.values()), "Active execution requires reconciliation before cutover"
    assert sql("SELECT to_jsonb(paused_until>now()) FROM runtime_control WHERE id=1"), "Admission must remain paused during draining"
    assert claude_running(old["quant-company-claude-runtime-1"]) == 0
    reviews_query = "SELECT COALESCE(jsonb_agg(to_jsonb(s)),'[]'::jsonb) FROM staff_independent_reviews s WHERE state='blocked' AND error='uncertain'"
    calls_query = "SELECT COALESCE(jsonb_agg(to_jsonb(c)),'[]'::jsonb) FROM maintenance_calls c"
    reviews = hashes(reviews_query)
    calls = hashes(calls_query)
    grade_query = "SELECT COALESCE(jsonb_agg(to_jsonb(s)),'[]'::jsonb) FROM staff_runs s WHERE id IN (SELECT run_id FROM staff_independent_reviews WHERE state='blocked' AND error='uncertain')"
    grades = hashes(grade_query)
    record = {"state": "cutover_started", "started_at": datetime.now(UTC).isoformat(),
        "patch_commit": QUALIFIED["patch_commit"], "ci": QUALIFIED["ci"], "before": before,
        "global_release": release, "overrides": overrides, "active_before": counts,
        "uncertain_review_sha256": reviews, "historical_maintenance_call_sha256": calls,
        "original_staff_run_sha256": grades, "applied": [],
        "rollback": [{"service": t["service"], "base_image": t["image_tag"], "base_image_id": t["base_image_id"]}
                     for t in QUALIFIED["targets"]],
        "rollback_instruction": "Use each exact original compose stack and private environment; change only its image. Reconcile any active or ambiguous request before retry, retaining stable IDs."}
    save(record)
    try:
        # Gracefully drain claimers first. No model request is created by this operator script.
        run(["docker", "stop", "--time", "60", "quant-company-worker-1"], timeout=90)
        run(["docker", "stop", "--time", "60", "quant-company-maintenance-1"], timeout=90)
        assert all(v == 0 for v in active().values()), "Execution appeared during draining; reconcile before applying"
        assert claude_running(old["quant-company-claude-runtime-1"]) == 0
        for service in ("claude-runtime", "maintenance", "worker"):
            t = next(t for t in QUALIFIED["targets"] if t["service"] == service)
            run(commands[service] + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", service], timeout=180)
            new = json.loads(run(["docker", "inspect", t["name"]]))[0]
            assert new["Image"] == t["target_image_id"] and new["State"]["Running"] and not new["State"]["OOMKilled"]
            boundaries(old[t["name"]], new)
            record["applied"].append({"service": service, "container_id": new["Id"], "image_id": new["Image"]})
            save(record)
        for _ in range(50):
            _, after_full = snapshot()
            c = after_full["quant-company-claude-runtime-1"]
            if c["State"].get("Health", {}).get("Status") == "healthy":
                break
            time.sleep(1)
        assert c["State"].get("Health", {}).get("Status") == "healthy"
        after, _ = snapshot()
        assert all(after.get(n) == value for n, value in before.items() if n not in target_names), "Another service changed"
        assert str(Path("/opt/quant-company/current").resolve()) == release
        reviews_after, calls_after, grades_after = hashes(reviews_query), hashes(calls_query), hashes(grade_query)
        assert all(reviews_after.get(k) == v for k, v in reviews.items())
        assert all(calls_after.get(k) == v for k, v in calls.items())
        assert all(grades_after.get(k) == v for k, v in grades.items())
        record.update(state="active", completed_at=datetime.now(UTC).isoformat(), after=after,
            environment_unchanged=True, container_boundaries_unchanged=True, other_containers_unchanged=True,
            uncertain_reviews_unchanged=True, historical_maintenance_calls_unchanged=True,
            objective_grades_unchanged=True, model_calls_by_operator=0)
        save(record)
    except BaseException as error:
        record.update(state="requires_operator_reconciliation", error_type=type(error).__name__,
                      failed_at=datetime.now(UTC).isoformat())
        save(record)
        raise
    print(json.dumps(record))
