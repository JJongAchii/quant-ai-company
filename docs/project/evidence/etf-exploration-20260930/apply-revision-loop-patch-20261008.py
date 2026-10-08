"""Prepare inactive prompt-repair images; apply only after exact manifest review.

Retains the actual installed model-policy integration, scientific releases and
all other service modules. Uses the established backup/drain/rollback procedure.
"""

import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

STATE = Path("/var/lib/quant-company")
ENV = STATE / "config/runtime.env"
TARGET = "quant_company/research/controller.py"
os.umask(0o077)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def run(command, **kwargs):
    return subprocess.check_output(command, timeout=600, **kwargs)


def save(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temp, path)


def inspect():
    names = run(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=quant-company",
                 "--format", "{{.Names}}"], text=True).splitlines()
    return {row["Name"].removeprefix("/"): row for row in json.loads(run(["docker", "inspect", *names]))}


def public(rows):
    return {name: {"id": row["Id"], "image_id": row["Image"], "image": row["Config"]["Image"],
                   "running": row["State"]["Running"], "memory_limit": row["HostConfig"]["Memory"],
                   "environment_sha256": sha(json.dumps(environment(row), sort_keys=True).encode())}
            for name, row in rows.items()}


def environment(row):
    values = dict(item.split("=", 1) for item in row["Config"]["Env"])
    assert len(values) == len(row["Config"]["Env"]), "duplicate_environment_keys"
    return values


def compose(row, override=None):
    files = row["Config"]["Labels"]["com.docker.compose.project.config_files"].split(",")
    if override:
        files.append(str(override))
    return ["docker", "compose", "--project-name", "quant-company", "--profile", "*",
            "--env-file", str(ENV), *[item for path in files for item in ("-f", path)]]


def sql(query):
    return json.loads(run(["docker", "exec", "quant-company-postgres-1", "psql", "-U", "postgres",
                          "-d", "quant_company", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", query]))


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def activity():
    return sql("SELECT json_build_object('running_turns',(SELECT count(*) FROM turns WHERE status='running'),"
               "'sending_outbox',(SELECT count(*) FROM outbox WHERE status='sending'),"
               "'active_jobs',(SELECT count(*) FROM research_jobs WHERE state IN "
               "('queued','claimed','running','cancel_requested','uncertain')))")


def authority(manifest):
    value = sql("SELECT json_build_object('program_state',r.state,'program_digest',r.manifest_digest,"
                "'mission_state',m.state,'mission_digest',m.manifest_digest,'revision',p.revision,"
                "'project_status',p.status,'max_trials',r.spec->'max_total_trials',"
                "'max_compute_seconds',r.spec->'max_compute_seconds')"
                " FROM research_programs r JOIN research_missions m ON m.program_id=r.id"
                " JOIN projects p ON p.id=r.project_id WHERE r.id=" + literal(manifest["program_id"])
                + " AND m.id=" + literal(manifest["mission_id"]))
    assert value["program_digest"] == manifest["program_digest"]
    assert value["program_state"] == value["mission_state"] == value["project_status"] == "active"
    assert value["revision"] == 5
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    manifest_bytes = (root / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["commit"])
    assert sha(Path(__file__).read_bytes()) == manifest["operator_sha256"]
    for name, digest in manifest["payload_files"].items():
        assert sha((root / name).read_bytes()) == digest
    prefix = "revision-loop-" + manifest["commit"]
    prepared_path = STATE / "releases" / (prefix + "-prepared.json")
    applied_path = STATE / "releases" / (prefix + "-applied.json")
    module_spec = importlib.util.spec_from_file_location("revision_loop_inventory", root / manifest["inventory_file"])
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)

    def sources(identity, image=False):
        command = (["docker", "run", "--rm", "--network", "none", "--memory", "256m",
                    "--entrypoint", "python", identity] if image else ["docker", "exec", identity, "python"])
        return json.loads(run([*command, "-c", module.CODE], text=True))

    def verify_baseline(rows):
        for baseline in manifest["baseline"]["services"]:
            row = rows[baseline["name"]]
            assert row["Id"] == baseline["container_id"] and row["Image"] == baseline["image_id"]
            assert row["State"]["Running"] and row["HostConfig"]["Memory"] == baseline["memory_limit"]
            assert row["Config"]["Labels"]["com.docker.compose.project.config_files"].split(",") == baseline["compose_files"]
            assert sources(row["Id"]) == {key: baseline[key] for key in ("source", "runtime")}

    def verify_patch(proof, baseline):
        for mode, payload in (("source", "controller.py"), ("runtime", "runtime_controller.py")):
            expected = {**baseline[mode], "patch_files": {TARGET: manifest["payload_files"][payload]}}
            assert proof[mode] == expected, mode + "_patch_or_other_sources_changed"

    before = inspect()
    verify_baseline(before)
    approved = authority(manifest)
    if not args.apply:
        assert not prepared_path.exists(), "preparation_already_exists"
        images = {}
        for service in ("api", "worker"):
            row = before["quant-company-" + service + "-1"]
            baseline = next(s for s in manifest["baseline"]["services"] if s["name"] == row["Name"].removeprefix("/"))
            base = "quant-company-revision-loop-baseline-" + service + ":" + manifest["commit"]
            tag = "quant-company-revision-loop-" + service + ":" + manifest["commit"]
            run(["docker", "tag", row["Image"], base])
            dockerfile = root / (service + ".Dockerfile")
            dockerfile.write_text("FROM " + base + "\nCOPY controller.py /app/src/" + TARGET
                                 + "\nCOPY runtime_controller.py " + baseline["runtime"]["root"] + "/" + TARGET + "\n")
            with (root / (service + "-build.private.log")).open("wb") as log:
                subprocess.run(["docker", "build", "--network", "none", "-f", str(dockerfile), "-t", tag, str(root)],
                               check=True, timeout=180, stdout=log, stderr=subprocess.STDOUT)
            proof = sources(tag, image=True)
            verify_patch(proof, baseline)
            image = json.loads(run(["docker", "image", "inspect", tag]))[0]
            images[service] = {"tag": tag, "image_id": image["Id"], "baseline_image_id": row["Image"], **proof}
        assert public(inspect()) == public(before), "concurrent_deployment_changed"
        result = {"state": "inactive_revision_loop_images_prepared", "observed_at": datetime.now(UTC).isoformat(),
                  "commit": manifest["commit"], "manifest_sha256": sha(manifest_bytes), "images": images,
                  "before": public(before), "authority": approved, "production_changed": False}
        save(prepared_path, result)
        print(json.dumps(result, indent=2))
        return

    prepared = json.loads(prepared_path.read_text())
    assert prepared["manifest_sha256"] == sha(manifest_bytes)
    assert public(before) == prepared["before"], "concurrent_deployment_changed"
    assert approved == prepared["authority"], "approved_authority_changed"
    assert not applied_path.exists(), "application_receipt_already_exists"
    assert manifest["regression"]["full_suite_passed"] and manifest["regression"]["lint_passed"]
    with (STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        protected_paths = [ENV, *[STATE / "config" / name for name in
                                 ("roles.json", "research-profiles.json", "research-qlab.json")]]
        protected = {str(path): sha(path.read_bytes()) for path in protected_paths}
        assert sql("SELECT json_build_object('pause',paused_until) FROM runtime_control WHERE id=1")["pause"] is None
        until = sql("WITH c AS (UPDATE runtime_control SET paused_until=now()+interval '15 minutes',reason="
                    + literal(prefix) + " WHERE id=1 AND paused_until IS NULL RETURNING paused_until)"
                    " SELECT json_build_object('until',(SELECT paused_until FROM c))")["until"]
        assert until
        result = {"state": "draining", "commit": manifest["commit"], "started_at": datetime.now(UTC).isoformat(),
                  "before": public(before), "prepared_sha256": sha(prepared_path.read_bytes()), "protected_config": protected,
                  "apply_operator_sha256": sha(Path(__file__).read_bytes()), "selected_services": ["api", "worker"],
                  "authority": "Existing owner deploy/continue and current repair request; reviewed exact PR105 operation",
                  "scientific_release_or_scope_changed": False}
        save(applied_path, result)
        stopped, changed, runtime_fds = [], [], []
        try:
            deadline = time.monotonic() + 300
            while True:
                active = activity()
                assert active["active_jobs"] == 0, "scientific_job_active"
                if active["running_turns"] == active["sending_outbox"] == 0:
                    break
                assert time.monotonic() < deadline, "external_effect_not_drained"
                time.sleep(1)
            for relative in ("codex/jobs/.runtime.lock", "codex/jobs/.runtime-news.lock",
                             "codex/jobs/.runtime-brief.lock", "codex/jobs/.runtime-quant.lock", "claude/jobs/.runtime.lock"):
                path = STATE / relative
                if path.exists():
                    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
                    runtime_fds.append(fd)
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            stopped = [row["Id"] for name, row in before.items()
                       if name != "quant-company-postgres-1" and row["State"]["Running"]]
            run(["docker", "stop", "--time", "60", *stopped])
            current = Path("/opt/quant-company/current").resolve()
            backup_spec = importlib.util.spec_from_file_location("revision_loop_backup", current / "deploy/state_backup.py")
            backup = importlib.util.module_from_spec(backup_spec)
            backup_spec.loader.exec_module(backup)
            backup.APP_SERVICES = ()
            for name, value in backup.config_values(STATE / "config/backup.env").items():
                os.environ[name] = value
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                backup.backup(SimpleNamespace(env_file=ENV, s3_uri=None), backup.config_values(ENV),
                              compose(before["quant-company-api-1"]), STATE)
            result["backup"] = json.loads(output.getvalue().strip().splitlines()[-1])
            assert sha(Path(result["backup"]["backup"]).read_bytes()) == result["backup"]["sha256"]
            save(applied_path, {**result, "state": "backed_up"})
            for service in ("api", "worker"):
                override = STATE / "config" / (prefix + "-" + service + ".compose.json")
                assert not override.exists()
                row = before["quant-company-" + service + "-1"]
                # Keep every actual active value; Compose can reorder environment
                # entries and interpolate defaults from a newer host release.
                # This host-only file is mode 0600, never printed or exported.
                save(override, {"services": {service: {"image": prepared["images"][service]["tag"],
                                                        "environment": environment(row)}}})
                run([*compose(row, override), "config", "--quiet"])
                configured = json.loads(run([*compose(row, override), "config", "--format", "json"]))
                assert configured["services"][service]["environment"] == environment(row), "compiled_environment_changed"
                changed.append(service)
                run([*compose(row, override), "up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "60", service])
                stopped.remove(row["Id"])
            if stopped:
                run(["docker", "start", *reversed(stopped)])
                stopped = []
            after = inspect()
            for service in ("api", "worker"):
                name = "quant-company-" + service + "-1"
                row = after[name]
                assert row["Image"] == prepared["images"][service]["image_id"] and row["State"]["Running"]
                assert environment(row) == environment(before[name]), "active_environment_values_changed"
                assert row["HostConfig"]["Memory"] == before[name]["HostConfig"]["Memory"]
                baseline = next(s for s in manifest["baseline"]["services"] if s["name"] == name)
                verify_patch(sources(row["Id"]), baseline)
            for name in before:
                if name not in {"quant-company-api-1", "quant-company-worker-1"}:
                    assert public(after)[name] == public(before)[name]
            assert {str(path): sha(path.read_bytes()) for path in protected_paths} == protected
            assert authority(manifest) == approved
            result.update(state="revision_loop_navigation_patch_applied", after=public(after),
                          completed_at=datetime.now(UTC).isoformat(), other_service_identities_preserved=True,
                          protected_config_preserved=True, installed_model_policy_preserved=True)
        except BaseException as error:
            result["failure_type"] = type(error).__name__
            for service in reversed(changed):
                row = before["quant-company-" + service + "-1"]
                rollback = STATE / "config" / (prefix + "-rollback-" + service + ".compose.json")
                save(rollback, {"services": {service: {"image": row["Image"], "environment": environment(row)}}})
                run([*compose(row, rollback), "up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "60", service])
                if row["Id"] in stopped:
                    stopped.remove(row["Id"])
            if stopped:
                run(["docker", "start", *reversed(stopped)])
            restored = inspect()
            for service in changed:
                name = "quant-company-" + service + "-1"
                assert restored[name]["Image"] == before[name]["Image"] and restored[name]["State"]["Running"]
                assert environment(restored[name]) == environment(before[name]), "rollback_environment_values_changed"
            result["rollback_images_and_environment_verified"] = True
            result.update(state="revision_loop_patch_failed_original_images_restored", failed_at=datetime.now(UTC).isoformat())
            raise
        finally:
            for fd in reversed(runtime_fds):
                os.close(fd)
            result["pause_restored"] = sql(
                "WITH c AS (UPDATE runtime_control SET paused_until=NULL,reason=NULL WHERE id=1 AND reason="
                + literal(prefix) + " AND paused_until=" + literal(until) + "::timestamptz RETURNING id)"
                " SELECT json_build_object('restored',(SELECT count(*) FROM c))")["restored"] == 1
            save(applied_path, result)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
