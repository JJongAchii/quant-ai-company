"""Resume already-deployed services with their exact current identities and settings."""

import fcntl
import hashlib
import json
import pathlib
import subprocess
import time
from datetime import UTC, datetime

STATE = pathlib.Path("/var/lib/quant-company")
COMMIT = "231d6ba0755f658f167636a8ea2c3ef65b6af562"
RECORD = STATE / "releases/owner-research-resume-20261006.json"


def run(args):
    return subprocess.check_output(args, text=True, timeout=180, stderr=subprocess.STDOUT)


def inspect():
    names = run(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=quant-company", "--format", "{{.Names}}"])
    return {r["Name"].lstrip("/"): r for r in json.loads(run(["docker", "inspect", *names.splitlines()]))}


def public(rows):
    return {n: {"id": r["Id"], "image": r["Config"]["Image"], "image_id": r["Image"], "state": r["State"]["Status"],
                "health": r["State"].get("Health", {}).get("Status"), "restarts": r["RestartCount"], "oom": r["State"]["OOMKilled"]}
            for n, r in rows.items()}


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


with (STATE / ".backup.lock").open("a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert not RECORD.exists(), "existing_resume_receipt_requires_readback"
    root = pathlib.Path("/opt/quant-company/current").resolve()
    assert root.name == COMMIT, "global_release_changed"
    hashes = {n: sha(STATE / "config" / n) for n in ["runtime.env", "roles.json", "research-profiles.json", "research-qlab.json"]}
    before = inspect()
    assert before["quant-company-api-1"]["Image"] == "sha256:7f305b5f8b235d065aa2b4d45106436f31355ec4b66fd78445453148472767ff"
    check = json.loads(run(["docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d", "quant_company", "-qAt", "-v", "ON_ERROR_STOP=1", "-c",
                           "SELECT json_build_object('paused',(SELECT paused_until FROM runtime_control WHERE id=1),'jobs',(SELECT count(*) FROM research_jobs WHERE state IN ('queued','claimed','running','cancel_requested','uncertain')))"]))
    assert check["paused"] is None and check["jobs"] == 0, "research_or_pause_requires_reconciliation"
    order = ["codex-runtime", "quant-codex-runtime", "claude-runtime", "account-gateway", "api", "slack-socket", "worker", "dispatch", "news-worker", "quant-feed-worker", "data-watch-worker", "maintenance"]
    record = {"phase": "resuming", "started_at": datetime.now(UTC).isoformat(), "owner_authorization": "Continued owner-requested service normalization and research review preparation", "source_commit": COMMIT,
              "before": public(before), "configuration_sha256": hashes, "database_before": check, "recreated_containers": 0, "new_scientific_authority_granted": False}

    def save(**values):
        record.update(values)
        RECORD.write_text(json.dumps(record, indent=2) + "\n")
        RECORD.chmod(0o600)

    save()
    try:
        started = []
        for service in order:
            name = "quant-company-" + service + "-1"
            row = before.get(name)
            if row is not None and not row["State"]["Running"]:
                run(["docker", "start", row["Id"]])
                started.append(name)
                save(started=started)
        deadline = time.monotonic() + 180
        while True:
            after = inspect()
            socket = after["quant-company-slack-socket-1"]
            log = run(["docker", "logs", "--since", record["started_at"], socket["Id"]])
            connected = "Slack Socket Mode connected for " in log
            if (after["quant-company-api-1"]["State"].get("Health", {}).get("Status") == "healthy"
                    and socket["State"]["Running"] and connected
                    and after["quant-company-worker-1"]["State"]["Running"]):
                break
            assert time.monotonic() < deadline, "current_service_readiness_timeout"
            time.sleep(3)
        assert all(after[n]["Id"] == r["Id"] and after[n]["Image"] == r["Image"] for n, r in before.items()), "current_identity_changed"
        assert all(sha(STATE / "config" / n) == h for n, h in hashes.items())
        assert pathlib.Path("/opt/quant-company/current").resolve() == root
        save(phase="current_services_resumed", after=public(after), socket_connected=True, completed_at=datetime.now(UTC).isoformat())
        print(json.dumps({"phase": record["phase"], "receipt": str(RECORD), "started": started, "socket_connected": True}))
    except Exception as exc:
        save(phase="resume_incomplete", error_type=type(exc).__name__, after=public(inspect()))
        raise
