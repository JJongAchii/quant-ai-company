import fcntl
import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

COMMIT = "fadc0e54be0c470068bb18011cc6a1a9ec8ee971"
STATE = Path("/var/lib/quant-company")
STAGE = STATE / "releases" / ("maintainer-recovery-" + COMMIT + "-stage.json")
JOURNAL = STATE / "releases" / ("maintainer-recovery-" + COMMIT + "-cutover.json")


def run(args):
    r = subprocess.run(args, capture_output=True, timeout=90)
    if r.returncode:
        raise RuntimeError("operator cutover command failed: " + args[0])
    return r.stdout.decode().strip()


def sql(query):
    return json.loads(
        run(
            [
                "docker",
                "exec",
                "-u",
                "postgres",
                "quant-company-postgres-1",
                "psql",
                "-XAt",
                "-v",
                "ON_ERROR_STOP=1",
                "-d",
                "quant_company",
                "-c",
                query,
            ]
        )
    )


def hashes(query):
    return {
        r["id"]: hashlib.sha256(json.dumps(r, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        for r in sql(query)
    }


def snap():
    names = [
        n
        for n in run(["docker", "ps", "--format", "{{.Names}}"]).splitlines()
        if n.startswith("quant-company-")
    ]
    full = json.loads(run(["docker", "inspect", *names]))
    public = {
        r["Name"].lstrip("/"): {
            "id": r["Id"],
            "image": r["Config"]["Image"],
            "image_id": r["Image"],
            "running": r["State"]["Running"],
            "health": r["State"].get("Health", {}).get("Status"),
        }
        for r in full
    }
    maint = next(r for r in full if r["Name"] == "/quant-company-maintenance-1")
    return public, maint


def save(record):
    temp = JOURNAL.with_suffix(".tmp")
    with temp.open("w") as f:
        json.dump(record, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, JOURNAL)


os.umask(0o077)
with (STATE / ".backup.lock").open("a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if JOURNAL.exists():
        record = json.loads(JOURNAL.read_text())
        assert record["state"] == "active", (
            "cutover outcome needs operator reconciliation; no automatic replay"
        )
        print(json.dumps(record, indent=2))
        raise SystemExit(0)
    stage = json.loads(STAGE.read_text())
    before, old = snap()
    assert old["Id"] == stage["previous_container_id"] and old["Image"] == stage["base_image_id"], (
        "maintenance changed since qualification"
    )
    assert (
        json.loads(run(["docker", "image", "inspect", stage["target_image"]]))[0]["Id"]
        == stage["target_image_id"]
    ), "qualified image changed"
    assert not sql(
        "SELECT COALESCE(jsonb_agg(to_jsonb(j)),'[]'::jsonb) FROM maintenance_jobs j WHERE state IN ('review','triage','patch','design','evaluate','publish','ci','pr')"
    ), "active old maintenance job; hold cutover"
    reviews_query = "SELECT COALESCE(jsonb_agg(to_jsonb(v)),'[]'::jsonb) FROM staff_independent_reviews v WHERE state='blocked' AND error='uncertain'"
    calls_query = "SELECT COALESCE(jsonb_agg(to_jsonb(c)),'[]'::jsonb) FROM maintenance_calls c"
    review_hashes = hashes(reviews_query)
    call_hashes = hashes(calls_query)
    command = [
        "docker",
        "compose",
        "--project-name",
        stage["project"],
        "--profile",
        "maintenance",
        "--env-file",
        stage["envfile"],
    ]
    files = stage["compose_files"] + [stage["override"]]
    for name in files:
        command += ["-f", name]
    resolved = json.loads(run(command + ["config", "--format", "json"]))["services"]["maintenance"]
    old_env = dict(pair.split("=", 1) for pair in old["Config"]["Env"])
    assert all(
        k == "COMPANY_CODE_COMMIT" or old_env.get(k) == str(v)
        for k, v in resolved.get("environment", {}).items()
    ), "runtime environment drift"
    assert resolved["image"] == stage["target_image"], "target image mismatch"
    current = run(["readlink", "-f", "/opt/quant-company/current"])
    record = {
        "state": "cutover_started",
        "started_at": datetime.now(UTC).isoformat(),
        "commit": COMMIT,
        "base_image": stage["base_image"],
        "base_image_id": stage["base_image_id"],
        "target_image": stage["target_image"],
        "target_image_id": stage["target_image_id"],
        "global_release_before": current,
        "before": before,
        "compose_files_sha256": {n: hashlib.sha256(Path(n).read_bytes()).hexdigest() for n in files},
        "command_service": "maintenance",
        "uncertain_review_row_sha256_before": review_hashes,
        "existing_maintenance_call_sha256_before": call_hashes,
        "rollback": {
            "base_image": stage["base_image"],
            "compose_files": stage["compose_files"],
            "operator_overlay_to_preserve_environment": stage["override"],
            "instructions": "Use the same maintenance-only compose stack with a final override setting the recorded base image and COMPANY_CODE_COMMIT=837ac72a76cf94625afa2b4ccbee5eb103ae6e03. Inspect any newly uncertain request before a retry. Preserve all other services and global release.",
        },
    }
    save(record)
    run(command + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", "maintenance"])
    time.sleep(8)
    after, new = snap()
    assert (
        new["Image"] == stage["target_image_id"] and new["State"]["Running"] and not new["State"]["OOMKilled"]
    ), "new maintainer unhealthy"
    assert new["Config"]["Labels"]["org.opencontainers.image.revision"] == COMMIT, (
        "new maintainer revision mismatch"
    )
    new_env = dict(pair.split("=", 1) for pair in new["Config"]["Env"])
    assert {k: v for k, v in old_env.items() if k != "COMPANY_CODE_COMMIT"} == {
        k: v for k, v in new_env.items() if k != "COMPANY_CODE_COMMIT"
    }, "installed environment changed"
    for field in ("User", "Entrypoint"):
        assert new["Config"][field] == old["Config"][field], "container boundary changed: " + field
    for field in ("ReadonlyRootfs", "CapDrop", "SecurityOpt", "Memory", "NanoCpus"):
        assert new["HostConfig"][field] == old["HostConfig"][field], "container boundary changed: " + field

    def mounts(r):
        return sorted((m["Type"], m["Source"], m["Destination"], m["RW"]) for m in r["Mounts"])

    assert mounts(new) == mounts(old), "mount boundaries changed"
    others = {n for n in before if n != "quant-company-maintenance-1"}
    assert all(after.get(n) == before[n] for n in others), "another container changed across cutover"
    assert run(["readlink", "-f", "/opt/quant-company/current"]) == current, "global release changed"
    reviews_after = hashes(reviews_query)
    calls_after = hashes(calls_query)
    assert all(reviews_after.get(k) == v for k, v in review_hashes.items()), "uncertain review changed"
    assert all(calls_after.get(k) == v for k, v in call_hashes.items()), "existing maintenance call changed"
    record.update(
        state="active",
        finished_at=datetime.now(UTC).isoformat(),
        after=after,
        other_services_unchanged=True,
        global_release_unchanged=True,
        environment_and_container_boundaries_preserved=True,
        uncertain_reviews_unchanged=True,
        existing_maintenance_calls_unchanged=True,
        new_maintenance_call_ids=sorted(set(calls_after) - set(call_hashes)),
    )
    save(record)
    print(json.dumps(record, indent=2))
