import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import tarfile
import time
from datetime import UTC, datetime
from types import SimpleNamespace

P = pathlib.Path
STATE = P("/var/lib/quant-company")
COMMIT = "3c848af95dc33b7da444a55018fd41d374c10025"
TARGET = P("/opt/quant-company/releases") / COMMIT
ENV = STATE / "config/runtime.env"
REGISTRY = STATE / "research/provisioned/data-evidence/registry.json"
OLD_REGISTRY = "bdef77801a3b65f7588bf00a3916a1a67f7b7e59657db0f1587c8999842646a3"
OLD_PROGRAM = "e06537d3-fac3-5c8c-bf25-ddabb3c7e282"
OLD_DIGEST = "53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b"
NEW_DIGEST = "04cee0f99abab3dfb94b37756a195753960fffd5a3f623ce0dd3563bf776d162"
RECORD = STATE / "releases" / ("exploration-cutover-" + COMMIT + ".json")
OVERRIDE = STATE / "config" / ("exploration-" + COMMIT + ".compose.json")
SELECTED = ["api", "dispatch", "slack-socket", "worker"]
EXPECTED = {
    "api": "sha256:830d346cb469c977188f6d5891eb1fb25977a8712618ba99311d469b7a6ed5a8",
    "worker": "sha256:6dce9a55e60b2a2b9f1a27aa99cd9a97c35bebe1b0c4c1bc9a1cccad2757f378",
}
APP = "quant-company:" + COMMIT
WORKER = "quant-company-autonomous:" + COMMIT


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


def swap_current(target):
    tmp = P("/opt/quant-company/current.exploration-tmp")
    tmp.symlink_to(target)
    os.replace(tmp, P("/opt/quant-company/current"))


os.umask(0o077)
with (STATE / ".backup.lock").open("a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert not RECORD.exists() and not OVERRIDE.exists(), "cutover_already_attempted"
    oldroot = P("/opt/quant-company/current").resolve()
    assert oldroot.name == "5defb8cb9dd7c655214663b05670cd2896006b30", "app_changed"
    oldenv = ENV.read_bytes()
    oldregistry = REGISTRY.read_bytes()
    assert sha(ENV) == "ab086c6612eb404bcccc3610606f2e4c14bc80fa1d596fcfd0b7319039df8b4b", "runtime_changed"
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
        == "quant-company-autonomous:0286ba3e7426ff6546b90e6926124ae65e71ba79"
    )
    for service, image in [("api", APP), ("worker", WORKER)]:
        assert json.loads(run(["docker", "image", "inspect", image]))[0]["Id"] == EXPECTED[service]
    prior_pause = sql(
        "SELECT row_to_json(c) FROM (SELECT paused_until,reason FROM runtime_control WHERE id=1)c"
    )
    assert prior_pause["paused_until"] is None, "runtime_already_paused"
    until = sql(
        "WITH c AS (UPDATE runtime_control SET paused_until=now()+interval '15 minutes',reason='approved_pr99_cutover' WHERE id=1 AND paused_until IS NULL RETURNING paused_until) SELECT json_build_object('until',(SELECT paused_until FROM c))"
    )["until"]
    assert until, "pause_ownership_changed"
    record = {
        "phase": "draining",
        "commit": COMMIT,
        "owner_authorization": "응 진행해; PR #99 operating application authorized on 2026-10-01",
        "started_at": datetime.now(UTC).isoformat(),
        "before": public(before),
        "previous_app": str(oldroot),
        "profiles": profiles,
        "pause_until": until,
        "pause_reason": "approved_pr99_cutover",
        "registry_before_sha256": OLD_REGISTRY,
        "program_digest": NEW_DIGEST,
        "new_scientific_authority_granted": False,
        "selected_services": SELECTED,
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
                "UPDATE runtime_control SET paused_until=NULL,reason=NULL WHERE id=1 AND reason='approved_pr99_cutover' AND paused_until="
                + literal(until)
                + "::timestamptz",
            ]
        )

    changed = []
    stopped = []
    registry_changed = False
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
        for n, r in before.items():
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
        lines = [
            l
            for l in oldenv.decode().splitlines()
            if l.split("=", 1)[0] not in ["RELEASE_COMMIT", "PINNED_COMPANY_WORKER_IMAGE"]
        ]
        atomic(
            ENV,
            (
                "\n".join([*lines, "RELEASE_COMMIT=" + COMMIT, "PINNED_COMPANY_WORKER_IMAGE=" + WORKER])
                + "\n"
            ).encode(),
        )
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
        swap_current(TARGET)
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
                and row["Image"] == EXPECTED["worker" if service == "worker" else "api"]
            ), "installed_image_mismatch"
        assert all(
            after[n]["Id"] == r["Id"]
            for n, r in before.items()
            if n not in {"quant-company-" + s + "-1" for s in SELECTED}
        ), "unrelated_container_recreated"
        # The additional packet is operator evidence, not a data-readiness or scientific approval.
        staging = P("/tmp/exploration-review-staged")
        staging.mkdir()
        with tarfile.open("/tmp/review.tar") as tf:
            for m in tf.getmembers():
                path = pathlib.PurePosixPath(m.name)
                assert m.isfile() and not path.is_absolute() and ".." not in path.parts
                target = staging.joinpath(*path.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tf.extractfile(m).read())
                target.chmod(0o444)
        package = json.loads((staging / "review-package.json").read_text())
        assert (
            package["program_digest"] == NEW_DIGEST
            and sha(staging / "candidate-program.json") == package["spec_file_sha256"]
        )
        packet = json.loads((staging / "candidate-evidence-packet.json").read_text())
        assert (
            sha(staging / "candidate-evidence-packet.json") == package["packet_file_sha256"]
            and packet["program_digest"] == NEW_DIGEST
        )
        assert REGISTRY.read_bytes() == oldregistry, "registry_concurrent_change"
        for name, e in packet["reports"].items():
            dest = REGISTRY.parent / name
            src = staging / "source-reports" / name
            assert (
                sha(src) == e["sha256"] and e["path"] == "/state/research/provisioned/data-evidence/" + name
            )
            if dest.exists():
                assert sha(dest) == e["sha256"], "existing_report_changed"
            else:
                dest.write_bytes(src.read_bytes())
                os.chown(dest, 10001, 10001)
                dest.chmod(0o444)
        registry = json.loads(oldregistry)
        assert len(registry["packets"]) == 2 and not any(
            p["program_digest"] == NEW_DIGEST for p in registry["packets"]
        )
        registry["packets"].append(packet)
        atomic(REGISTRY, (json.dumps(registry, ensure_ascii=False, indent=2) + "\n").encode())
        registry_changed = True
        # Preserve the old packet bytes and prove new-policy parsing plus exact source/profile admission.
        verify = r"""import json,pathlib;from quant_company.company import Company,fingerprint;from quant_company.config import Settings;from quant_company.research.program_contracts import ResearchProgram;from quant_company.research.builds import profile_for;from quant_company.research.data_evidence import load_packets;from quant_company.research.programs import ProgramStore;company=Company(Settings());spec=ResearchProgram.model_validate_json(pathlib.Path('/tmp/candidate-program.json').read_text());digest=fingerprint(spec.model_dump(mode='json'));assert digest=='04cee0f99abab3dfb94b37756a195753960fffd5a3f623ce0dd3563bf776d162';envelopes={e.name:e for e in spec.envelopes};packet=load_packets(company,{'manifest_digest':digest},envelopes);assert len(packet)==1;assert not packet['etf_strategy'][0].blocking_gaps;
with company.db.transaction() as conn:
 project=company._project(conn,'9aac0de4-2b97-5195-a720-287d324234f3');assert project['revision']==5;company._check_sources(conn,project['id'],spec.source_ids);[profile_for(company,e.template) for e in spec.envelopes];old=ProgramStore(company).snapshot(conn,'e06537d3-fac3-5c8c-bf25-ddabb3c7e282');assert old['usage']['trials']==0
print(json.dumps({'program_digest':digest,'packet_count':len(packet),'old_program_readable':True,'exact_sources_profiles_reports_inputs_engine_verified':True,'scientific_authority_granted':False}))
"""
        run(
            [
                "docker",
                "cp",
                str(staging / "candidate-program.json"),
                "quant-company-worker-1:/tmp/candidate-program.json",
            ]
        )
        proof = json.loads(
            run(
                [
                    "docker",
                    "exec",
                    "quant-company-worker-1",
                    "python",
                    "/app/entrypoint.py",
                    "python",
                    "-c",
                    verify,
                ]
            )
        )
        assert all(sha(STATE / "config" / n) == h for n, h in profiles.items())
        assert activity()["reservations"] == 0 and activity()["missions"] == 0
        save(
            phase="active_waiting_for_3070_and_pause_release",
            after=public(after),
            verification=proof,
            registry_after_sha256=sha(REGISTRY),
            registry_packets=3,
            completed_at=datetime.now(UTC).isoformat(),
        )
        print(
            json.dumps(
                {
                    "phase": record["phase"],
                    "receipt": str(RECORD),
                    "backup": receipt,
                    "registry_after_sha256": record["registry_after_sha256"],
                    "verification": proof,
                }
            ),
            flush=True,
        )
    except Exception as exc:
        if stopped:
            run(["docker", "start", *reversed(stopped)], stderr=subprocess.STDOUT)
        if registry_changed:
            assert (
                sha(REGISTRY)
                == hashlib.sha256(
                    (json.dumps(registry, ensure_ascii=False, indent=2) + "\n").encode()
                ).hexdigest()
            ), "rollback_registry_ownership_changed"
            atomic(REGISTRY, oldregistry)
        atomic(ENV, oldenv)
        if OVERRIDE.exists():
            rollback = {}
            for n, r in before.items():
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
        if P("/opt/quant-company/current").resolve() != oldroot:
            swap_current(oldroot)
        restore_pause()
        save(
            phase="rolled_back" if changed else "activation_aborted",
            error=str(exc)[:200],
            after=public(inspect()),
        )
        raise
