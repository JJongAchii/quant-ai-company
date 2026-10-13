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

P = pathlib.Path
STATE = P("/var/lib/quant-company")
COMMIT = "79586479aeef07554be311951e40bbe00c33cafe"
TARGET = P("/opt/quant-company/releases") / COMMIT
ENV = STATE / "config/runtime.env"
REGISTRY = STATE / "research/provisioned/data-evidence/registry.json"
OLD_REGISTRY = "023aa88c3cb1b5ed61794ecbca9c3770b1288ec125ceef51e1722ea68db4eb66"
OLD_PROGRAM = "e06537d3-fac3-5c8c-bf25-ddabb3c7e282"
OLD_DIGEST = "53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b"
NEW_DIGEST = "04cee0f99abab3dfb94b37756a195753960fffd5a3f623ce0dd3563bf776d162"
RECORD = STATE / "releases" / ("exploration-cutover-" + COMMIT + "-cancel.json")
OVERRIDE = STATE / "config" / ("exploration-" + COMMIT + "-cancel.compose.json")
SELECTED = ["slack-socket"]
EXPECTED = {
    "slack-socket": "sha256:9c6be6fdc4d385c44b1ebbfafdb5c2d11f16a28d84f31014acfd462abeed1005",
    "worker": "sha256:6dce9a55e60b2a2b9f1a27aa99cd9a97c35bebe1b0c4c1bc9a1cccad2757f378",
}
APP = "quant-company:" + COMMIT
WORKER = "quant-company-autonomous:3c848af95dc33b7da444a55018fd41d374c10025"


def run(args, **kw):
    return subprocess.check_output(args, timeout=1800, **kw)


def sha(p):
    with p.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def sql(q):
    return json.loads(
        run(
            [
                "docker",
                "exec",
                "quant-company-postgres-1",
                "psql",
                "-U",
                "postgres",
                "-d",
                "quant_company",
                "-qAt",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                q,
            ]
        )
    )


def literal(v):
    return "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"


def atomic(p, raw):
    old = p.stat() if p.exists() else None
    temp = p.with_name(p.name + ".exploration-tmp")
    temp.write_bytes(raw)
    temp.chmod(old.st_mode & 0o777 if old else 0o600)
    if old:
        os.chown(temp, old.st_uid, old.st_gid)
    os.replace(temp, p)


def inspect():
    names = run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            "label=com.docker.compose.project=quant-company",
            "--format",
            "{{.Names}}",
        ],
        text=True,
    ).splitlines()
    return {r["Name"].lstrip("/"): r for r in json.loads(run(["docker", "inspect", *names]))}


def public(rows):
    return {
        n: {
            "id": r["Id"],
            "image": r["Config"]["Image"],
            "image_id": r["Image"],
            "state": r["State"]["Status"],
            "health": r["State"].get("Health", {}).get("Status"),
        }
        for n, r in rows.items()
    }


def activity():
    return sql(
        "SELECT json_build_object('running_turns',(SELECT count(*) FROM turns WHERE status='running'),'active_jobs',(SELECT count(*) FROM research_jobs WHERE state IN ('queued','claimed','running','cancel_requested','uncertain')),'sending_outbox',(SELECT count(*) FROM outbox WHERE status='sending'),'digest',(SELECT manifest_digest FROM research_programs WHERE id="
        + literal(OLD_PROGRAM)
        + "),'state',(SELECT state FROM research_programs WHERE id="
        + literal(OLD_PROGRAM)
        + "),'revision',(SELECT revision FROM projects WHERE id='9aac0de4-2b97-5195-a720-287d324234f3'),'missions',(SELECT count(*) FROM research_missions WHERE program_id="
        + literal(OLD_PROGRAM)
        + "),'reservations',(SELECT count(*) FROM research_program_reservations WHERE program_id="
        + literal(OLD_PROGRAM)
        + "))"
    )


def compose(row):
    files = row["Config"]["Labels"]["com.docker.compose.project.config_files"].split(",")
    return [
        "docker",
        "compose",
        "--project-name",
        "quant-company",
        "--profile",
        "*",
        "--env-file",
        str(ENV),
        *[x for p in files for x in ["-f", p]],
    ]


os.umask(0o077)
with (STATE / ".backup.lock").open("a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert not RECORD.exists() and not OVERRIDE.exists(), "cutover_already_attempted"
    oldroot = P("/opt/quant-company/current").resolve()
    oldenv = ENV.read_bytes()
    oldregistry = REGISTRY.read_bytes()
    assert sha(REGISTRY) == OLD_REGISTRY, "registry_changed"
    profiles = {
        n: sha(STATE / "config" / n) for n in ["roles.json", "research-profiles.json", "research-qlab.json"]
    }
    assert (
        profiles["research-qlab.json"] == "9281ff53d7e13ff594c9ed26228d6597baad972aade814dbf807fdf9d2e3cedd"
    )
    before = inspect()
    assert (
        before["quant-company-worker-1"]["Config"]["Image"]
        == "quant-company-autonomous:3c848af95dc33b7da444a55018fd41d374c10025"
    )
    assert (
        before["quant-company-slack-socket-1"]["Config"]["Image"]
        == "quant-company:3c848af95dc33b7da444a55018fd41d374c10025"
    ), "receiver_changed"
    for service, image in [("slack-socket", APP)]:
        assert json.loads(run(["docker", "image", "inspect", image]))[0]["Id"] == EXPECTED[service]
    prior_pause = sql(
        "SELECT row_to_json(c) FROM (SELECT paused_until,reason FROM runtime_control WHERE id=1)c"
    )
    assert prior_pause["paused_until"] is None, "runtime_already_paused"
    until = sql(
        "WITH c AS (UPDATE runtime_control SET paused_until=now()+interval '15 minutes',reason='approved_program_cancel_repair' WHERE id=1 AND paused_until IS NULL RETURNING paused_until) SELECT json_build_object('until',(SELECT paused_until FROM c))"
    )["until"]
    assert until, "pause_ownership_changed"
    record = {
        "phase": "draining",
        "commit": COMMIT,
        "owner_authorization": "Owner requested continuation of reviewed PR99 operating activation and required signed replacement workflow; no scientific authority granted",
        "started_at": datetime.now(UTC).isoformat(),
        "before": public(before),
        "previous_app": str(oldroot),
        "profiles": profiles,
        "pause_until": until,
        "pause_reason": "approved_program_cancel_repair",
        "registry_before_sha256": OLD_REGISTRY,
        "program_digest": NEW_DIGEST,
        "new_scientific_authority_granted": False,
        "selected_services": SELECTED,
        "scope_amendment": "Only signed Socket Mode ingress is replaced. Preserve independently updated API, dispatch, global current release and runtime.env.",
    }

    def save(**v):
        record.update(v)
        atomic(RECORD, (json.dumps(record, indent=2) + "\n").encode())

    def restore_pause():
        run(
            [
                "docker",
                "exec",
                "quant-company-postgres-1",
                "psql",
                "-U",
                "postgres",
                "-d",
                "quant_company",
                "-qAt",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                "UPDATE runtime_control SET paused_until=NULL,reason=NULL WHERE id=1 AND reason='approved_program_cancel_repair' AND paused_until="
                + literal(until)
                + "::timestamptz",
            ]
        )

    changed = []
    stopped = []
    save()
    try:
        deadline = time.monotonic() + 360
        while True:
            active = activity()
            assert (
                active["active_jobs"] == 0
                and active["digest"] == OLD_DIGEST
                and active["state"] == "active"
                and active["revision"] == 5
                and active["missions"] == 0
                and active["reservations"] == 0
            ), "research_or_revision_changed"
            if active["running_turns"] == 0 and active["sending_outbox"] == 0:
                break
            assert time.monotonic() < deadline, "drain_timeout"
            time.sleep(1)
        assert ENV.read_bytes() == oldenv and REGISTRY.read_bytes() == oldregistry
        atomic(STATE / "releases" / ("exploration-" + COMMIT + ".before.env"), oldenv)
        atomic(STATE / "releases" / ("exploration-" + COMMIT + ".before.registry.json"), oldregistry)
        # Stop every current writer by its captured ID, including independently pinned services.
        # The backup helper runs under this same lock and never replaces PostgreSQL.
        stopped = [
            r["Id"] for n, r in before.items() if n != "quant-company-postgres-1" and r["State"]["Running"]
        ]
        save(phase="consistent_backup", database_before=active)
        print(json.dumps({"phase": "stopping_writers_for_consistent_backup"}), flush=True)
        run(["docker", "stop", "--time", "120", *stopped], stderr=subprocess.STDOUT)
        spec = importlib.util.spec_from_file_location(
            "exploration_backup", oldroot / "deploy/state_backup.py"
        )
        backup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backup)
        backup.APP_SERVICES = ()
        for k, v in backup.config_values(STATE / "config/backup.env").items():
            os.environ[k] = v
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                backup.backup(
                    SimpleNamespace(env_file=ENV, s3_uri=None),
                    backup.config_values(ENV),
                    compose(before["quant-company-api-1"]),
                    STATE,
                )
        finally:
            run(["docker", "start", *reversed(stopped)], stderr=subprocess.STDOUT)
            stopped = []
        receipt = json.loads(output.getvalue().strip().splitlines()[-1])
        assert sha(P(receipt["backup"])) == receipt["sha256"]
        save(phase="backed_up", backup=receipt)
        print(json.dumps({"phase": "consistent_backup_uploaded", "sha256": receipt["sha256"]}), flush=True)
        assert (
            ENV.read_bytes() == oldenv
            and REGISTRY.read_bytes() == oldregistry
            and P("/opt/quant-company/current").resolve() == oldroot
        )
        assert all(sha(STATE / "config" / n) == h for n, h in profiles.items())
        updates = {}
        for r in before.values():
            service = r["Config"]["Labels"].get("com.docker.compose.service")
            if not service or service == "postgres":
                continue
            env = dict(x.split("=", 1) for x in r["Config"]["Env"])
            image = r["Config"]["Image"]
            if service in SELECTED:
                env["COMPANY_CODE_COMMIT"] = COMMIT
                image = WORKER if service == "worker" else APP
            updates[service] = {"image": image, "environment": env}
        atomic(OVERRIDE, json.dumps({"services": updates}).encode())
        for service in SELECTED:
            row = before["quant-company-" + service + "-1"]
            command = [*compose(row), "-f", str(OVERRIDE)]
            run([*command, "config", "--quiet"])
            config = json.loads(run([*command, "config", "--format", "json"]))
            desired = config["services"][service]
            assert desired["image"] == (WORKER if service == "worker" else APP)
            assert desired["environment"]["COMPANY_CODE_COMMIT"] == COMMIT
            expected = {
                (m["Source"], m["Destination"], m["RW"]) for m in row["Mounts"] if m["Type"] == "bind"
            }
            actual = {
                (m["source"], m["target"], not m.get("read_only", False))
                for m in desired.get("volumes", [])
                if m["type"] == "bind"
            }
            for s in desired.get("secrets", []):
                target = s.get("target", s["source"])
                target = target if target.startswith("/") else "/run/secrets/" + target
                actual.add((config["secrets"][s["source"]]["file"], target, False))
            assert actual == expected, "service_mounts_would_change:" + service
        save(phase="activating", override_path=str(OVERRIDE))
        for service in SELECTED:
            changed.append(service)
            run(
                [
                    *compose(before["quant-company-" + service + "-1"]),
                    "-f",
                    str(OVERRIDE),
                    "up",
                    "-d",
                    "--no-deps",
                    "--no-build",
                    "--pull",
                    "never",
                    service,
                ],
                stderr=subprocess.STDOUT,
            )
        deadline = time.monotonic() + 120
        while True:
            after = inspect()
            api = after["quant-company-api-1"]
            if api["State"].get("Health", {}).get("Status") == "healthy":
                break
            assert time.monotonic() < deadline, "api_health_timeout"
            time.sleep(2)
        for service in SELECTED:
            row = after["quant-company-" + service + "-1"]
            assert (
                row["State"]["Running"]
                and row["Image"] == EXPECTED[service]
            ), "installed_image_mismatch"
        assert all(
            after[n]["Id"] == r["Id"]
            for n, r in before.items()
            if n not in {"quant-company-" + s + "-1" for s in SELECTED}
        ), "unrelated_container_recreated"
        proof = json.loads(
            run(
                [
                    "docker",
                    "exec",
                    "quant-company-slack-socket-1",
                    "python",
                    "-c",
                    'import json,os;from quant_company.research.approvals import short_command;value=short_command("연구 프로그램 취소 e06537d3-fac3-5c8c-bf25-ddabb3c7e282 53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b");assert value["action"]=="cancel_program";print(json.dumps({"company_commit":os.environ["COMPANY_CODE_COMMIT"],"exact_cancel_parser":True}))',
                ]
            )
        )
        assert sha(REGISTRY) == OLD_REGISTRY and all(
            sha(STATE / "config" / n) == h for n, h in profiles.items()
        )
        assert ENV.read_bytes() == oldenv and P("/opt/quant-company/current").resolve() == oldroot, (
            "global_app_or_runtime_changed"
        )
        restore_pause()
        save(
            phase="active",
            after=public(after),
            verification=proof,
            registry_after_sha256=sha(REGISTRY),
            research_worker_and_3070_unchanged=True,
            completed_at=datetime.now(UTC).isoformat(),
        )
        print(
            json.dumps(
                {"phase": record["phase"], "receipt": str(RECORD), "backup": receipt, "verification": proof}
            ),
            flush=True,
        )
    except Exception as exc:
        if stopped:
            run(["docker", "start", *reversed(stopped)], stderr=subprocess.STDOUT)
        if OVERRIDE.exists():
            rollback = {}
            for r in before.values():
                s = r["Config"]["Labels"].get("com.docker.compose.service")
                if s and s != "postgres":
                    rollback[s] = {
                        "image": r["Config"]["Image"],
                        "environment": dict(x.split("=", 1) for x in r["Config"]["Env"]),
                    }
            atomic(OVERRIDE, json.dumps({"services": rollback}).encode())
            for service in reversed(changed):
                run(
                    [
                        *compose(before["quant-company-" + service + "-1"]),
                        "-f",
                        str(OVERRIDE),
                        "up",
                        "-d",
                        "--no-deps",
                        "--no-build",
                        "--pull",
                        "never",
                        service,
                    ],
                    stderr=subprocess.STDOUT,
                )
        assert ENV.read_bytes() == oldenv and P("/opt/quant-company/current").resolve() == oldroot, (
            "global_app_or_runtime_changed"
        )
        restore_pause()
        save(
            phase="rolled_back" if changed else "activation_aborted",
            error=str(exc)[:200],
            after=public(inspect()),
        )
        raise
