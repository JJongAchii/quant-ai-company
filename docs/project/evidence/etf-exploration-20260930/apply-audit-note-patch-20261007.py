"""Prepare two images, then apply only the reviewed audit service patch.

Run on the existing company host beside manifest.json and the two patch files.
Default prepares inactive images. --apply requires their persisted preparation.
No worker scientific release, credential, runtime env, data or registry changes.
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

parser = argparse.ArgumentParser()
parser.add_argument("--apply", action="store_true")
args = parser.parse_args()
ROOT = Path(__file__).resolve().parent
manifest = json.loads((ROOT / "manifest.json").read_text())
assert re.fullmatch(r"[0-9a-f]{40}", manifest["commit"])
STATE = Path("/var/lib/quant-company")
ENV = STATE / "config/runtime.env"
PREFIX = "audit-note-" + manifest["commit"]
PREPARED = STATE / "releases" / (PREFIX + "-prepared.json")
APPLIED = STATE / "releases" / (PREFIX + "-applied.json")
REASON = PREFIX
os.umask(0o077)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def run(command, **kwargs):
    return subprocess.check_output(command, timeout=600, **kwargs)


def save(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temp.chmod(0o600)
    os.replace(temp, path)


def inspect():
    names = run(["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=quant-company",
                 "--format", "{{.Names}}"], text=True).splitlines()
    return {r["Name"].removeprefix("/"): r for r in json.loads(run(["docker", "inspect", *names]))}


def public(rows):
    return {name: {"id": row["Id"], "image_id": row["Image"], "image": row["Config"]["Image"],
                  "running": row["State"]["Running"]} for name, row in rows.items()}


SOURCE_CODE = '''
import hashlib,json,pathlib
root=pathlib.Path('/app/src')
files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*.py'))}
targets=['quant_company/research/audit_delivery.py','quant_company/research/mission_backend.py']
others={k:v for k,v in files.items() if k not in targets}
print(json.dumps({'patch_files':{k:files[k] for k in targets},'source_count':len(files),
 'other_sources_digest':hashlib.sha256(json.dumps(others,sort_keys=True,separators=(',',':')).encode()).hexdigest()}))
'''


def sources(identity, image=False):
    command = (["docker", "run", "--rm", "--network", "none", "--entrypoint", "python", identity]
               if image else ["docker", "exec", identity, "python"])
    return json.loads(run([*command, "-c", SOURCE_CODE], text=True))


def verify_baseline(rows):
    for expected in manifest["baseline"]["services"]:
        row = rows[expected["name"]]
        assert row["Id"] == expected["container_id"] and row["Image"] == expected["image_id"]
        assert row["State"]["Running"]
        assert row["Config"]["Labels"]["com.docker.compose.project.config_files"].split(",") == expected["compose_files"]
        assert sources(row["Id"]) == {key: expected[key] for key in
            ("patch_files", "source_count", "other_sources_digest")}


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
        "'active_jobs',(SELECT count(*) FROM research_jobs WHERE state IN ('queued','claimed','running','cancel_requested','uncertain')))")


for name, expected in manifest["patch_files"].items():
    assert digest((ROOT / Path(name).name).read_bytes()) == expected

if not args.apply:
    assert not PREPARED.exists(), "preparation_already_exists"
    before = inspect()
    verify_baseline(before)
    images = {}
    for service in ("api", "worker"):
        row = before["quant-company-" + service + "-1"]
        base = "quant-company-audit-baseline-" + service + ":" + manifest["commit"]
        tag = "quant-company-audit-" + service + ":" + manifest["commit"]
        run(["docker", "tag", row["Image"], base])
        dockerfile = ROOT / (service + ".Dockerfile")
        dockerfile.write_text("FROM " + base + "\n" + "".join(
            "COPY " + Path(name).name + " /app/src/" + name + "\n" for name in manifest["patch_files"]))
        with (ROOT / (service + "-build.private.log")).open("wb") as log:
            subprocess.run(["docker", "build", "--network", "none", "-f", str(dockerfile), "-t", tag, str(ROOT)],
                           check=True, timeout=180, stderr=subprocess.STDOUT, stdout=log)
        image = json.loads(run(["docker", "image", "inspect", tag]))[0]
        proof = sources(tag, image=True)
        assert proof["patch_files"] == manifest["patch_files"]
        assert proof["other_sources_digest"] == manifest["baseline"]["services"][0]["other_sources_digest"]
        assert proof["source_count"] == manifest["baseline"]["services"][0]["source_count"]
        images[service] = {"tag": tag, "image_id": image["Id"], "baseline_image_id": row["Image"], **proof}
    verify_baseline(inspect())
    result = {"state": "inactive_patch_images_prepared", "observed_at": datetime.now(UTC).isoformat(),
        "commit": manifest["commit"], "manifest_sha256": digest((ROOT / "manifest.json").read_bytes()),
        "images": images, "before": public(before), "production_changed": False}
    save(PREPARED, result)
    print(json.dumps(result, indent=2))
    raise SystemExit(0)

prepared = json.loads(PREPARED.read_text())
assert prepared["manifest_sha256"] == digest((ROOT / "manifest.json").read_bytes())
assert not APPLIED.exists(), "patch_already_attempted"
with (STATE / ".backup.lock").open("a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    before = inspect()
    verify_baseline(before)
    assert public(before) == prepared["before"], "concurrent_deployment_changed"
    protected_paths = [ENV, *[STATE / "config" / name for name in
        ("roles.json", "research-profiles.json", "research-qlab.json")]]
    protected = {str(p): digest(p.read_bytes()) for p in protected_paths}
    assert sql("SELECT json_build_object('pause',paused_until) FROM runtime_control WHERE id=1")["pause"] is None
    until = sql("WITH c AS (UPDATE runtime_control SET paused_until=now()+interval '15 minutes',reason="
        + literal(REASON) + " WHERE id=1 AND paused_until IS NULL RETURNING paused_until)"
        " SELECT json_build_object('until',(SELECT paused_until FROM c))")["until"]
    assert until
    result = {"state": "draining", "commit": manifest["commit"], "started_at": datetime.now(UTC).isoformat(),
        "before": public(before), "prepared_sha256": digest(PREPARED.read_bytes()), "protected_config": protected,
        "selected_services": ["api", "worker"], "authority": "Existing owner deploy/continue and monitoring authorization; reviewed PR105 patch",
        "scientific_worker_release_changed": False}
    save(APPLIED, result)
    stopped = []
    changed = []
    try:
        deadline = time.monotonic() + 60
        while True:
            active = activity()
            assert active["active_jobs"] == 0, "scientific_job_active"
            if active["running_turns"] == active["sending_outbox"] == 0:
                break
            assert time.monotonic() < deadline, "external_effect_not_drained"
            time.sleep(1)
        stopped = [r["Id"] for name, r in before.items()
                   if name != "quant-company-postgres-1" and r["State"]["Running"]]
        run(["docker", "stop", "--time", "60", *stopped])
        oldroot = Path("/opt/quant-company/current").resolve()
        spec = importlib.util.spec_from_file_location("audit_patch_backup", oldroot / "deploy/state_backup.py")
        backup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backup)
        backup.APP_SERVICES = ()
        for name, value in backup.config_values(STATE / "config/backup.env").items():
            os.environ[name] = value
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            backup.backup(SimpleNamespace(env_file=ENV, s3_uri=None), backup.config_values(ENV),
                          compose(before["quant-company-api-1"]), STATE)
        result["backup"] = json.loads(output.getvalue().strip().splitlines()[-1])
        assert digest(Path(result["backup"]["backup"]).read_bytes()) == result["backup"]["sha256"]
        save(APPLIED, {**result, "state": "backed_up"})
        for service in ("api", "worker"):
            override = STATE / "config" / (PREFIX + "-" + service + ".compose.json")
            assert not override.exists()
            save(override, {"services": {service: {"image": prepared["images"][service]["tag"]}}})
            row = before["quant-company-" + service + "-1"]
            command = compose(row, override)
            run([*command, "config", "--quiet"])
            changed.append(service)
            run([*command, "up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "60", service])
            stopped.remove(row["Id"])
        run(["docker", "start", *reversed(stopped)])
        stopped = []
        after = inspect()
        for service in ("api", "worker"):
            row = after["quant-company-" + service + "-1"]
            assert row["Image"] == prepared["images"][service]["image_id"] and row["State"]["Running"]
            assert sources(row["Id"])["patch_files"] == manifest["patch_files"]
            assert sources(row["Id"])["other_sources_digest"] == prepared["images"][service]["other_sources_digest"]
        for name, row in before.items():
            if name not in {"quant-company-api-1", "quant-company-worker-1"}:
                assert after[name]["Id"] == row["Id"] and after[name]["Image"] == row["Image"]
                assert after[name]["State"]["Running"] == row["State"]["Running"]
        assert {str(p): digest(p.read_bytes()) for p in protected_paths} == protected
        result.update(state="two_file_audit_patch_applied", after=public(after), completed_at=datetime.now(UTC).isoformat(),
                      other_service_identities_preserved=True, protected_config_preserved=True)
    except BaseException:
        for service in reversed(changed):
            row = before["quant-company-" + service + "-1"]
            run([*compose(row), "up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "60", service])
            if row["Id"] in stopped:
                stopped.remove(row["Id"])
        if stopped:
            run(["docker", "start", *reversed(stopped)])
        result.update(state="audit_patch_failed_original_images_restored", failed_at=datetime.now(UTC).isoformat())
        raise
    finally:
        sql("WITH c AS (UPDATE runtime_control SET paused_until=NULL,reason=NULL WHERE id=1 AND reason="
            + literal(REASON) + " AND paused_until=" + literal(until) + "::timestamptz RETURNING id)"
            " SELECT json_build_object('restored',(SELECT count(*) FROM c))")
        save(APPLIED, result)
    print(json.dumps(result, indent=2))
