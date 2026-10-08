"""Review, then switch the six existing services to the qualified scheduler repair.

Default: write a public review packet. --apply drains and backs up before switching
five services; the company worker remains stopped until --resume confirms the 3070
alignment. Original requests, proposals, assessments and launch receipts survive.
"""

import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import time
from datetime import UTC, datetime
from types import SimpleNamespace

SOURCE = "ec1fbeb29a49420a367f06af596283e6ead4ab72"
PREVIOUS = "e1e74d3cb1a25833c58a13baff3441f1381270ee"
STATE = pathlib.Path("/var/lib/quant-company")
CURRENT = pathlib.Path("/opt/quant-company/current")
TARGET = CURRENT.parent / "releases" / SOURCE
ENV = STATE / "config/runtime.env"
STAGE = STATE / "releases" / ("held-audit-stage-" + SOURCE + ".json")
VALIDATION = STATE / "releases" / ("held-audit-validation-" + SOURCE + ".json")
PACKET = STATE / "releases" / ("held-audit-review-" + SOURCE + ".json")
JOURNAL = STATE / "releases" / ("held-audit-cutover-" + SOURCE + ".json")
OVERLAY = STATE / "config" / ("held-audit-" + SOURCE + ".compose.json")
SELECTED = ["api", "dispatch", "slack-socket", "worker", "account-gateway", "codex-runtime"]
PROGRAM = "f7deaf96-e677-5afe-93d4-18ac387043bb"
DIGEST = "c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args, timeout=600):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=timeout).stdout


def atomic(path, value):
    body = value if isinstance(value, bytes) else (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    temporary = path.with_name(path.name + ".scoped-tmp")
    assert not temporary.exists(), "reconcile_unfinished_atomic_write"
    temporary.write_bytes(body)
    temporary.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o600)
    if path.exists():
        os.chown(temporary, path.stat().st_uid, path.stat().st_gid)
    os.replace(temporary, path)


def sql(query):
    return json.loads(run(["docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres", "-d",
                           "quant_company", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", query]))


def activity():
    return sql("""SELECT json_build_object(
 'running_turns',(SELECT count(*) FROM turns WHERE status='running'),
 'active_jobs',(SELECT count(*) FROM research_jobs WHERE state IN ('queued','claimed','running','cancel_requested','uncertain')),
 'sending_outbox',(SELECT count(*) FROM outbox WHERE status='sending'),
 'program',(SELECT json_build_object('state',state,'revision',revision,'digest',manifest_digest,'approval_event_id',approval_event_id)
  FROM research_programs WHERE id='f7deaf96-e677-5afe-93d4-18ac387043bb'))""")


def inspect():
    names = run(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=quant-company",
                 "--format", "{{.Names}}"], 30).splitlines()
    return {row["Name"].removeprefix("/"): row for row in json.loads(run(["docker", "inspect", *names], 30))}


def public(rows):
    return {name: {"id": row["Id"], "image": row["Config"]["Image"], "image_id": row["Image"],
                   "state": row["State"]["Status"], "health": row["State"].get("Health", {}).get("Status")}
            for name, row in rows.items()}


def runtime_busy():
    for suffix in ("", "-news", "-quant", "-brief"):
        path = STATE / "codex/jobs" / (".runtime" + suffix + ".lock")
        if not path.exists():
            continue
        with path.open("r") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
    return False


def verify_authority(value):
    assert value["program"]["state"] == "active" and value["program"]["revision"] == 5
    assert value["program"]["digest"] == DIGEST and value["program"]["approval_event_id"].startswith("slack:")
    assert value["active_jobs"] == 0, "reconcile_existing_research_execution_first"


def prepared_packet():
    stage = json.loads(STAGE.read_bytes())
    assert stage["state"] == "exact_images_qualified_inactive" and stage["source_commit"] == SOURCE
    validation = json.loads(VALIDATION.read_bytes())
    assert validation["source_commit"] == SOURCE and validation["state"] == "engineering_qualification_passed"
    assert validation["full_tests"]["failures"] == validation["full_tests"]["errors"] == 0
    assert validation["full_tests"]["real_postgresql"] and validation["full_tests"]["real_temporal"]
    assert validation["lint"] == "passed"
    assert CURRENT.resolve().name == PREVIOUS and not JOURNAL.exists()
    assert stage["config_sha256"] == {name: sha(STATE / "config" / name) for name in stage["config_sha256"]}
    rows = inspect()
    selected = {"quant-company-" + name + "-1": rows["quant-company-" + name + "-1"] for name in SELECTED}
    assert all(row["State"]["Running"] for row in selected.values())
    images = {item["target"]: item for item in stage["images"]}
    for item in images.values():
        assert json.loads(run(["docker", "image", "inspect", item["image"]], 30))[0]["Id"] == item["image_id"]
    value = activity()
    verify_authority(value)
    api = selected["quant-company-api-1"]
    files = api["Config"]["Labels"]["com.docker.compose.project.config_files"].split(",")
    assert all(pathlib.Path(name).is_file() for name in files)
    return {"schema_version": 1, "state": "prepared_for_review", "source_commit": SOURCE,
            "previous_commit": PREVIOUS, "stage_receipt_sha256": sha(STAGE), "images": images,
            "validation_receipt_sha256": sha(VALIDATION),
            "config_sha256": stage["config_sha256"], "selected_services": SELECTED,
            "previous_services": public(selected), "compose_files": files, "scientific_authority": value["program"],
            "program_id": PROGRAM, "program_digest": DIGEST, "scientific_budget_changed": False,
            "worker_config_sha256": "37a90b3f38bcc19c8ebd68be35c625fac2bf6028942a45ef9454433166a3ce14",
            "worker_release": "/home/achii/quant-company-qualification/held-audit-20261006/release-candidates/" + SOURCE,
            "pr": "https://github.com/JJongAchii/quant-ai-company/pull/105",
            "authorization": "Existing owner requests to deploy and continue the normalization; root reviews this narrow scheduler repair. No new exact-SHA human approval is claimed.",
            "prepared_at": datetime.now(UTC).isoformat()}


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--apply", action="store_true")
parser.add_argument("--resume", action="store_true")
parser.add_argument("--review-sha")
args = parser.parse_args()
assert not (args.apply and args.resume)
if not args.apply and not args.resume:
    value = prepared_packet()
    assert not PACKET.exists(), "read_existing_review_packet"
    atomic(PACKET, value)
    print(json.dumps({"state": value["state"], "review_sha256": sha(PACKET), "packet": str(PACKET),
                      "source_commit": SOURCE, "production_changed": False}))
    raise SystemExit(0)

assert args.review_sha == sha(PACKET), "review_packet_changed"
packet = json.loads(PACKET.read_bytes())
assert packet["source_commit"] == SOURCE and packet["stage_receipt_sha256"] == sha(STAGE)
assert packet["validation_receipt_sha256"] == sha(VALIDATION)
compose = ["docker", "compose", "--project-name", "quant-company", "--env-file", str(ENV)]
for name in packet["compose_files"]:
    compose += ["-f", name]

with (STATE / ".backup.lock").open("a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if args.resume:
        record = json.loads(JOURNAL.read_bytes())
        assert record["phase"] == "server_active_company_worker_stopped" and CURRENT.resolve() == TARGET
        aligned = json.loads((STATE / "releases" / ("held-audit-worker-active-" + SOURCE + ".json")).read_bytes())
        assert aligned["phase"] == "active_worker_aligned" and aligned["source_commit"] == SOURCE
        assert aligned["active_config_sha256"] == packet["worker_config_sha256"]
        verify_authority(activity())
        run(compose + ["-f", str(OVERLAY), "up", "-d", "--no-deps", "worker"], 180)
        record.update(phase="server_and_worker_active", worker_alignment=aligned, completed_at=datetime.now(UTC).isoformat())
        atomic(JOURNAL, record)
        print(json.dumps({"phase": record["phase"], "source_commit": SOURCE, "scientific_trials_added_by_operator": 0}))
        raise SystemExit(0)
    current = prepared_packet()
    assert current["previous_services"] == packet["previous_services"] and current["config_sha256"] == packet["config_sha256"]
    rows = inspect()
    old_env = ENV.read_bytes()
    timers = [name for name in ("quant-company-release.timer", "quant-company-backup.timer")
              if subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True).stdout.strip() == "active"]
    record = {"schema_version": 1, "phase": "prepared", "source_commit": SOURCE, "review_sha256": sha(PACKET),
              "previous_services": public(rows), "timers": timers, "scientific_trials_added_by_operator": 0,
              "started_at": datetime.now(UTC).isoformat()}
    atomic(JOURNAL, record)
    stopped = []
    try:
        if timers:
            run(["systemctl", "stop", *timers], 30)
        run(["docker", "stop", "--time", "360", rows["quant-company-dispatch-1"]["Id"]], 400)
        stopped.append(rows["quant-company-dispatch-1"]["Id"])
        deadline = time.monotonic() + 360
        while True:
            value = activity()
            verify_authority(value)
            if value["running_turns"] == 0 and value["sending_outbox"] == 0 and not runtime_busy():
                break
            assert time.monotonic() < deadline, "drain_timeout_original_requests_retained"
            time.sleep(2)
        # Stop company writers while retaining PostgreSQL, Temporal and the proxy.
        application = [row["Id"] for name, row in rows.items() if row["State"]["Running"]
                       and any(marker in name for marker in ("worker", "socket", "dispatch", "api", "gateway", "runtime", "maintenance"))
                       and row["Id"] not in stopped]
        run(["docker", "stop", "--time", "360", *application], 450)
        stopped.extend(application)
        assert not runtime_busy(), "runtime_not_drained"
        record.update(phase="writers_stopped")
        atomic(JOURNAL, record)
        old_root = CURRENT.resolve()
        spec = importlib.util.spec_from_file_location("scoped_backup", old_root / "deploy/state_backup.py")
        backup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backup)
        backup.APP_SERVICES = ()
        for name, value in backup.config_values(STATE / "config/backup.env").items():
            os.environ[name] = value
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            backup.backup(SimpleNamespace(env_file=ENV, s3_uri=None), backup.config_values(ENV), compose, STATE)
        receipt = json.loads(output.getvalue().strip().splitlines()[-1])
        assert sha(pathlib.Path(receipt["backup"])) == receipt["sha256"]
        record.update(phase="backed_up", backup=receipt)
        atomic(JOURNAL, record)
        atomic(STATE / "releases" / ("held-audit-" + SOURCE + ".before.env"), old_env)
        values = backup.config_values(ENV)
        values.update(RELEASE_COMMIT=SOURCE, COMPANY_CODE_COMMIT=SOURCE)
        atomic(ENV, ("\n".join(name + "=" + value for name, value in values.items()) + "\n").encode())
        services = {}
        for name in SELECTED:
            target = "autonomous-research" if name == "worker" else "codex" if name == "codex-runtime" else "app"
            services[name] = {"image": packet["images"][target]["image"], "environment": {"COMPANY_CODE_COMMIT": SOURCE}}
        atomic(OVERLAY, {"services": services})
        pointer = CURRENT.with_name("current.scoped-tmp")
        assert not pointer.exists()
        pointer.symlink_to(TARGET)
        os.replace(pointer, CURRENT)
        run(compose + ["-f", str(OVERLAY), "config", "--quiet"], 30)
        run(compose + ["-f", str(OVERLAY), "up", "-d", "--no-deps", *[name for name in SELECTED if name != "worker"]], 180)
        selected_ids = {rows["quant-company-" + name + "-1"]["Id"] for name in SELECTED}
        unrelated = [identity for identity in stopped if identity not in selected_ids]
        if unrelated:
            run(["docker", "start", *unrelated], 120)
        stopped = [rows["quant-company-worker-1"]["Id"]]
        record.update(phase="server_active_company_worker_stopped", active_services=public(inspect()),
                      env_sha256=sha(ENV), overlay_sha256=sha(OVERLAY))
        atomic(JOURNAL, record)
        print(json.dumps({"phase": record["phase"], "source_commit": SOURCE, "backup_sha256": receipt["sha256"],
                          "scientific_trials_added_by_operator": 0}))
    except Exception as error:
        record.update(failed_phase=record["phase"], phase="forward_reconciliation_required", error_type=type(error).__name__)
        atomic(JOURNAL, record)
        if CURRENT.resolve().name == PREVIOUS and ENV.read_bytes() == old_env and stopped:
            run(["docker", "start", *reversed(stopped)], 120)
        raise
    finally:
        if timers:
            run(["systemctl", "start", *timers], 30)
