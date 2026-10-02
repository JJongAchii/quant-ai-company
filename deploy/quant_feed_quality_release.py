#!/usr/bin/env python3
"""Scoped Quant release and qualified publication resumption; preserve other workers."""

import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

STATE = Path("/var/lib/quant-company")
CURRENT = Path("/opt/quant-company/current")
SELECTED = ("api", "dispatch", "quant-feed-worker")
DEDICATED_RUNTIME = "quant-codex-runtime"
PROTOCOL_PROBE = """
import hashlib, json
from quant_company.contracts import ProviderRequest
from quant_company.providers.codex_runner import output_schema
from quant_company.quant_feed.editor import output_contract
contracts = {}
for stage in ('review', 'critique'):
    contract = output_contract(stage)
    request = ProviderRequest(request_id='quant-feed-protocol-probe', model='probe', prompt='probe',
                              output_contract=contract)
    schema = json.dumps(output_schema(request), sort_keys=True, separators=(',', ':'))
    contracts[contract] = hashlib.sha256(schema.encode()).hexdigest()
request = ProviderRequest(request_id='quant-feed-protocol-probe', model='probe', prompt='probe',
                          web_search=True, output_contract='quant_search_v1')
schema = json.dumps(output_schema(request), sort_keys=True, separators=(',', ':'))
contracts[request.output_contract] = hashlib.sha256(schema.encode()).hexdigest()
print(json.dumps(contracts, sort_keys=True))
"""
PUBLICATION_PROBE = """
import json
from quant_company.company import Company
from quant_company.config import Settings
from quant_company.quant_feed.store import QuantFeedStore
c = Company(Settings()); store = QuantFeedStore(c)
current = store.policy()
published = c.settings.quant_feed_publish_enabled
c.settings.quant_feed_publish_enabled = True
live = store.policy()
c.settings.quant_feed_publish_enabled = published
print(json.dumps({'authorized': store.authorized(), 'channel': c.settings.quant_feed_channel_id,
                  'publish_enabled': published, 'policy': current, 'live_policy': live}))
"""
SLACK_PROBE = """
import httpx,json
from quant_company.config import Settings
s=Settings(); credential=json.loads(s.slack_credentials_file.read_text())['quant_scout']
r=httpx.post('https://slack.com/api/auth.test',
 headers={'Authorization':'Bearer '+credential['bot_token']},timeout=12).json()
print(json.dumps({'ok':r.get('ok') is True,'user_id':r.get('user_id'),
 'team_id':r.get('team_id'),'app_id':credential.get('app_id')}))
"""


def run(command, **kwargs):
    result = subprocess.run(command, capture_output=True, check=False, timeout=1800, **kwargs)
    if result.returncode:
        raise RuntimeError(f"{command[0]} failed with exit {result.returncode}")
    return result.stdout.decode().strip()


def module(path):
    spec = importlib.util.spec_from_file_location("reviewed_release", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def inventory():
    ids = run(["docker", "ps", "-aq"]).split()
    rows = json.loads(run(["docker", "inspect", *ids])) if ids else []
    return {
        row["Name"].lstrip("/"): {
            "id": row["Id"],
            "image_id": row["Image"],
            "running": row["State"]["Running"],
            "oom": row["State"]["OOMKilled"],
            "restarts": row["RestartCount"],
            "health": row["State"].get("Health", {}).get("Status"),
            "image_revision": row["Config"].get("Labels", {}).get("org.opencontainers.image.revision"),
            "quant_publish": next(
                (
                    item.split("=", 1)[1]
                    for item in row["Config"]["Env"]
                    if item.startswith("QUANT_FEED_PUBLISH_ENABLED=")
                ),
                None,
            ),
        }
        for row in rows
    }


def activity():
    # The API and dispatch are stopped during cutover. Query the independent
    # database container so the second drain check is still executable.
    query = """SELECT json_build_object(
      'running_quant_calls', (SELECT count(*) FROM quant_feed_calls WHERE state='running'),
      'pending_quant_outbox', (SELECT count(*) FROM quant_feed_publications p
          JOIN outbox o ON o.id=p.id WHERE o.status IN ('pending','sending')),
      'sending_outbox', (SELECT count(*) FROM outbox WHERE status='sending'))::text"""
    return json.loads(
        run(
            [
                "docker",
                "exec",
                "-u",
                "postgres",
                "quant-company-postgres-1",
                "psql",
                "-X",
                "-A",
                "-t",
                "-v",
                "ON_ERROR_STOP=1",
                "-d",
                "quant_company",
                "-c",
                query,
            ]
        )
    )


def protocol_preflight(app_image_id, runtime_image_id):
    """Compare staged producer/consumer schemas without credentials or model calls."""
    try:
        consumer = json.loads(run([
            "docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true", "--pids-limit=32", "--memory=128m",
            "--entrypoint", "python", app_image_id, "-c", PROTOCOL_PROBE,
        ]))
        producer = json.loads(run([
            "docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true", "--pids-limit=32", "--memory=128m",
            "--entrypoint", "python", runtime_image_id, "-c", PROTOCOL_PROBE,
        ]))
        if (not isinstance(consumer, dict) or set(consumer) != {"quant_brief_v4", "quant_critique_v2", "quant_search_v1"}
                or not all(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value)
                           for value in consumer.values()) or consumer != producer):
            raise ValueError("schema_mismatch")
    except (RuntimeError, ValueError):
        raise ValueError("quant_native_protocol_requires_matching_staged_images") from None
    return consumer


def stage(args, previous, target, journal, helper):
    """Build only the two changed code images from dependency-identical installed bases."""
    if target.exists() or journal.exists() or shutil.disk_usage(target.parent).free < 3 * 1024**3:
        raise ValueError("quant_stage_target_exists_or_disk_headroom_low")
    archive = Path(args.archive)
    data = archive.read_bytes()
    if hashlib.sha256(data).hexdigest() != args.archive_sha256:
        raise ValueError("quant_stage_archive_digest_mismatch")
    before = inventory()
    runtime_service = (DEDICATED_RUNTIME if "quant-company-" + DEDICATED_RUNTIME + "-1" in before
                       else "codex-runtime")
    target.mkdir()
    record = {"phase": "staging", "commit": args.commit, "previous": str(previous),
              "archive_sha256": args.archive_sha256, "images": [], "build_method": "unchanged_dependency_code_update",
              "service_inventory_before": before, "started_at": time.time()}
    helper.atomic(journal, json.dumps(record).encode())
    try:
        module(previous / "deploy/housing_feed_release.py").unpack(data, target)
        quant = module(previous / "deploy/quant_feed_release.py")
        if quant.qdata_tree_digest(previous / "qdata") != args.qdata_tree_sha256:
            raise ValueError("quant_stage_qdata_tree_mismatch")
        shutil.copytree(previous / "qdata", target / "qdata")
        record["qdata_commit"] = json.loads((target / "deploy/qdata-source.json").read_text())["commit"]
        record["qdata_tree_sha256"] = args.qdata_tree_sha256
        runtime_name = "quant-company-" + runtime_service + "-1"
        runtime = json.loads(run(["docker", "inspect", runtime_name]))[0]
        app = json.loads(run(["docker", "inspect", "quant-company-api-1"]))[0]
        runtime_commit = runtime["Config"]["Labels"].get("org.opencontainers.image.revision")
        app_commit = app["Config"]["Labels"].get("org.opencontainers.image.revision")
        if (not re.fullmatch(r"[a-f0-9]{40}", runtime_commit or "")
                or not re.fullmatch(r"[a-f0-9]{40}", app_commit or "")
                or runtime["Image"] != before[runtime_name]["image_id"]
                or app["Image"] != before["quant-company-api-1"]["image_id"]):
            raise ValueError("quant_stage_base_image_changed")
        runtime_source = CURRENT.parent / "releases" / runtime_commit
        app_source = CURRENT.parent / "releases" / app_commit
        fixed_inputs = ("pyproject.toml", "uv.lock", "deploy/Dockerfile", "deploy/Dockerfile.code-update",
                        "deploy/entrypoint.py",
                        "deploy/qdata-source.json")
        if not runtime_source.is_dir() or not app_source.is_dir() or any(
                (target / name).read_bytes() != (base / name).read_bytes()
                for base in (previous, app_source, runtime_source) for name in fixed_inputs):
            raise ValueError("quant_stage_base_dependencies_changed")
        record.update(base_app_commit=app_commit, base_runtime_commit=runtime_commit,
                      base_runtime_service=runtime_service)
        expected = quant.source_inventory(target / "src/quant_company")
        probe = ("import hashlib,importlib.util,json,pathlib;"
                 "root=pathlib.Path(importlib.util.find_spec('quant_company').origin).parent;"
                 "print(json.dumps({'quant_company/'+str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() "
                 "for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}))")
        for name, base_image, repository in (("app", app["Image"], "quant-company"),
                                             ("codex", runtime["Image"], "quant-company-codex")):
            pinned = f"quant-feed-base-{name}:{base_image.split(':', 1)[1]}"
            run(["docker", "image", "tag", base_image, pinned])
            tag = f"{repository}:{args.commit}"
            run(["docker", "build", "--network=none", "--pull=false", "-f",
                 str(target / "deploy/Dockerfile.quant-code-update"), "--build-arg", "BASE_IMAGE=" + pinned,
                 "--build-arg", "RELEASE_COMMIT=" + args.commit, "-t", tag, str(target)])
            actual = json.loads(run(["docker", "run", "--rm", "--network=none", "--memory=128m",
                                     "--entrypoint", "python", tag, "-c", probe]))
            image = json.loads(run(["docker", "image", "inspect", tag]))[0]
            if (actual != expected or image["Config"]["Labels"].get("org.opencontainers.image.revision") != args.commit
                    or image["Config"]["Labels"].get("org.quant-company.qdata-revision") != record["qdata_commit"]):
                raise ValueError("quant_stage_installed_source_or_label_mismatch")
            record["images"].append({"target": name, "id": image["Id"], "base_id": base_image,
                                     "source_matches_commit": True})
            helper.atomic(journal, json.dumps(record).encode())
        if inventory() != before:
            raise ValueError("quant_stage_changed_production_services")
        record.update(phase="staged", staged_at=time.time())
    except BaseException as exc:
        record.update(phase="stage_failed", error=type(exc).__name__)
        raise
    finally:
        helper.atomic(journal, json.dumps(record).encode())
    print(json.dumps({"phase": "staged", "commit": args.commit, "images": record["images"],
                      "independent_services_preserved": True}), flush=True)


def compose(helper, root, *args, image_overrides=None):
    overlay = root / "deploy/data-watch.compose.yaml"
    if not overlay.is_file():
        raise ValueError("data_watch_overlay_missing")
    if not (STATE / "config/data-watch-contracts.json").is_file():
        raise ValueError("data_watch_calendar_must_be_a_file")
    command = [*helper.compose_command(root), "--profile", "data-watch", "-f", str(overlay)]
    if image_overrides:
        command.extend(["-f", str(image_overrides)])
    return run([*command, *args])


def check(before, commit, app_image_id, runtime_image_id, publication_enabled=False):
    after = inventory()
    dedicated = "quant-company-" + DEDICATED_RUNTIME + "-1"
    if set(after) != set(before) | {dedicated}:
        raise ValueError("container_inventory_changed")
    selected = {"quant-company-" + name + "-1" for name in SELECTED}
    for name, expected in before.items():
        row = after[name]
        if name == dedicated:
            continue
        if name in selected:
            if (
                not row["running"]
                or row["oom"]
                or row["health"] not in (None, "healthy")
                or row["image_revision"] != commit
                or row["image_id"] != app_image_id
                or row["quant_publish"] != str(publication_enabled).lower()
            ):
                raise ValueError("quant_release_service_unhealthy")
        elif {key: row[key] for key in ("id", "image_id", "running", "oom", "restarts")} != {
            key: expected[key] for key in ("id", "image_id", "running", "oom", "restarts")
        }:
            raise ValueError("independent_service_changed")
    runtime = after[dedicated]
    if (not runtime["running"] or runtime["oom"] or runtime["health"] not in (None, "healthy")
            or runtime["image_revision"] != commit or runtime["image_id"] != runtime_image_id):
        raise ValueError("quant_dedicated_runtime_unhealthy")
    return {
        "selected": [DEDICATED_RUNTIME, *SELECTED],
        "independent_service_ids_preserved": True,
        "publication_enabled": publication_enabled,
    }


def cutover(args, previous, target, journal, helper):
    record = json.loads(journal.read_text())
    if (
        record.get("phase") != "staged"
        or record.get("commit") != args.commit
        or record.get("previous") != str(previous)
        or not target.is_dir()
    ):
        raise ValueError("quality_release_not_staged")
    app_images = [image for image in record["images"] if image["target"] == "app"]
    runtime_images = [image for image in record["images"] if image["target"] == "codex"]
    if len(app_images) != 1 or len(runtime_images) != 1:
        raise ValueError("quality_release_images_missing")
    env = STATE / "config/runtime.env"
    original = env.read_bytes()
    values = dict(
        line.split("=", 1)
        for line in original.decode().splitlines()
        if "=" in line and not line.startswith("#")
    )
    before = inventory()
    publication_enabled = values.get("QUANT_FEED_PUBLISH_ENABLED") == "true"
    if (
        values.get("RELEASE_COMMIT") != before["quant-company-api-1"]["image_revision"]
        or values.get("QUANT_FEED_ENABLED") != "true"
        or values.get("QUANT_FEED_PUBLISH_ENABLED") not in {"true", "false"}
        or publication_enabled and not getattr(args, "preserve_publication", False)
    ):
        raise ValueError("quality_release_environment_changed")
    if not (STATE / "config/data-watch-contracts.json").is_file():
        raise ValueError("data_watch_calendar_must_be_a_file")
    runtime_service = record.get("base_runtime_service", "codex-runtime")
    runtime_name = "quant-company-" + runtime_service + "-1"
    if (
        before["quant-company-api-1"]["image_id"] != app_images[0]["base_id"]
        or before[runtime_name]["image_id"] != runtime_images[0]["base_id"]
    ):
        raise ValueError("quality_release_base_image_changed")
    selected = {"quant-company-" + name + "-1" for name in SELECTED}
    required = selected | ({runtime_name} if runtime_service == DEDICATED_RUNTIME else set())
    if any(not before[name]["running"] or before[name]["oom"] for name in required):
        raise ValueError("quality_release_service_not_running")
    if any(activity().values()):
        raise ValueError("quality_release_activity_not_drained")
    # Never stop a service or write release state before proving compatibility.
    protocol = protocol_preflight(app_images[0]["id"], runtime_images[0]["id"])
    # Other deployments can leave Quant pinned to older exact images. Restore
    # those images on rollback rather than inferring them from RELEASE_COMMIT.
    rollback_images = journal.with_suffix(".rollback.compose.json")
    rollback_services = {name: {"image": before["quant-company-" + name + "-1"]["image_id"]}
                         for name in SELECTED}
    if runtime_service == DEDICATED_RUNTIME:
        rollback_services[DEDICATED_RUNTIME] = {"image": before[runtime_name]["image_id"]}
    helper.atomic(rollback_images, json.dumps({"services": rollback_services}).encode())
    # Stop only the Quant consumer first. Its durable workflow may start a final
    # activity between the first idle check and shutdown; inspect it again.
    run(["docker", "stop", "--time", "900", "quant-company-quant-feed-worker-1"])
    try:
        if any(activity().values()):
            raise ValueError("quality_release_activity_started_during_stop")
    except BaseException:
        run(["docker", "start", "quant-company-quant-feed-worker-1"])
        raise
    old_env_path = journal.with_suffix(".env")
    changed = False
    try:
        helper.atomic(old_env_path, original)
        record.update(
            phase="cutover_started",
            cutover_started_at=time.time(),
            original_env_sha256=hashlib.sha256(original).hexdigest(),
            quant_native_protocol=protocol,
            publication_enabled=publication_enabled,
            rollback_image_overrides=str(rollback_images),
            independent_before={key: value for key, value in before.items()
                                if key not in selected and key != "quant-company-" + DEDICATED_RUNTIME + "-1"},
        )
        helper.atomic(journal, json.dumps(record).encode())
        run(["docker", "stop", "--time", "360", "quant-company-dispatch-1", "quant-company-api-1"])
        if any(activity().values()):
            raise ValueError("quality_release_activity_after_dispatch_stop")
        release = module(previous / "deploy/quant_feed_release.py")
        changed = True  # Restore the snapshot even if the environment write fails.
        release.setenv(
            helper,
            {
                "RELEASE_COMMIT": args.commit,
                "QDATA_COMMIT": record["qdata_commit"],
                "QDATA_BUILD_CONTEXT": str(target / "qdata"),
            },
        )
        helper.link(target)
        compose(
            helper, target, "up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "120",
            DEDICATED_RUNTIME,
        )
        live_protocol = json.loads(run(["docker", "exec", "quant-company-" + DEDICATED_RUNTIME + "-1",
                                        "python", "-c", PROTOCOL_PROBE]))
        if live_protocol != protocol:
            raise ValueError("quant_live_runtime_schema_mismatch")
        compose(
            helper,
            target,
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "--wait",
            "--wait-timeout",
            "120",
            *SELECTED,
        )
        result = check(before, args.commit, app_images[0]["id"], runtime_images[0]["id"], publication_enabled)
        for _ in range(2):
            time.sleep(5)
            result = check(before, args.commit, app_images[0]["id"], runtime_images[0]["id"], publication_enabled)
        if not publication_enabled and any(activity()[key] for key in ("pending_quant_outbox", "sending_outbox")):
            raise ValueError("quality_release_unexpected_outbox_activity")
        record.update(
            phase="live_active" if publication_enabled else "preview_active",
            cutover_completed_at=time.time(),
            result=result,
            original_roles_and_credentials_preserved=True,
        )
        helper.atomic(journal, json.dumps(record).encode())
        print(json.dumps({"phase": record["phase"], "commit": args.commit, **result}), flush=True)
    except BaseException as exc:
        try:
            unresolved = changed and any(activity().values())
        except Exception:
            unresolved = changed
        if unresolved:
            record.update(phase="intervention_required", error=type(exc).__name__,
                          reason="new_quant_call_may_have_an_ambiguous_effect")
            helper.atomic(journal, json.dumps(record).encode())
            raise
        if changed:
            helper.atomic(env, original)
            helper.link(previous)
            with contextlib.suppress(Exception):
                compose(helper, target, "stop", DEDICATED_RUNTIME)
            if runtime_service == DEDICATED_RUNTIME:
                compose(helper, previous, "up", "-d", "--no-deps", "--force-recreate", "--wait",
                        "--wait-timeout", "120", DEDICATED_RUNTIME, image_overrides=rollback_images)
        compose(
            helper,
            previous,
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "--wait",
            "--wait-timeout",
            "120",
            *SELECTED,
            image_overrides=rollback_images,
        )
        record.update(phase="rolled_back", error=type(exc).__name__)
        helper.atomic(journal, json.dumps(record).encode())
        raise


def publication_probe():
    return json.loads(run(["docker", "exec", "quant-company-api-1", "python",
                           "/app/entrypoint.py", "python", "-c", PUBLICATION_PROBE]))


def resume(args, current, deployment_journal, helper):
    """Change only the publication gate; never enqueue or revive old documents."""
    journal = STATE / "releases" / ("quant-feed-resume-" + args.commit + ".json")
    if journal.exists():
        saved = json.loads(journal.read_text())
        if (saved.get("phase") == "live_active" and publication_probe()["publish_enabled"] is True):
            print(json.dumps(saved), flush=True)
            return
        raise ValueError("quant_resume_requires_manual_reconciliation")
    deployed = json.loads(deployment_journal.read_text())
    if (args.base != args.commit or deployed.get("commit") != args.commit
            or deployed.get("phase") != "preview_active" or not args.channel
            or not re.fullmatch(r"[CG][A-Z0-9]+", args.channel)):
        raise ValueError("quant_resume_release_precondition")
    env = STATE / "config/runtime.env"
    original = env.read_bytes()
    values = dict(line.split("=", 1) for line in original.decode().splitlines()
                  if "=" in line and not line.startswith("#"))
    if (values.get("RELEASE_COMMIT") != args.commit or values.get("QUANT_FEED_ENABLED") != "true"
            or values.get("QUANT_FEED_PUBLISH_ENABLED") != "false"
            or values.get("QUANT_FEED_CHANNEL_ID") != args.channel
            or not (STATE / "config/data-watch-contracts.json").is_file()):
        raise ValueError("quant_resume_environment_mismatch")
    before = inventory()
    selected = {"quant-company-" + name + "-1" for name in SELECTED}
    dedicated = "quant-company-" + DEDICATED_RUNTIME + "-1"
    if any(name not in before or not before[name]["running"] or before[name]["oom"]
           or before[name]["image_revision"] != args.commit for name in selected | {dedicated}):
        raise ValueError("quant_resume_services_not_qualified")
    probe = publication_probe()
    if (not probe["authorized"] or probe["publish_enabled"] or probe["channel"] != args.channel):
        raise ValueError("quant_resume_policy_precondition")
    proofs = []
    for item in args.qualification or []:
        path, digest = item.rsplit(":", 1)
        path = Path(path)
        if (not path.resolve().is_relative_to(STATE / "operations")
                or not re.fullmatch(r"[a-f0-9]{64}", digest)):
            raise ValueError("quant_resume_qualification_path")
        raw = path.read_bytes()
        proof = json.loads(raw)
        if (hashlib.sha256(raw).hexdigest() != digest or proof.get("state") != "passed"
                or proof.get("slack_writes") not in (0, False)
                or proof.get("document_writes", False) not in (0, False)):
            raise ValueError("quant_resume_qualification_failed")
        proofs.append({"sha256": digest, "policy": proof.get("policy"),
                       "cases": proof.get("case_results", {})})
    if len({p["sha256"] for p in proofs}) < 2 or not any(
            p["policy"] == probe["policy"] and p["cases"].get("positive", {}).get("state") == "passed"
            and p["cases"].get("negative", {}).get("state") == "passed" for p in proofs):
        raise ValueError("quant_resume_current_policy_qualification_missing")
    quant = module(current / "deploy/quant_feed_release.py")
    slack = json.loads(run(["docker", "exec", "quant-company-dispatch-1", "python",
                            "/app/entrypoint.py", "python", "-c", SLACK_PROBE]))
    if slack != {"ok": True, "user_id": quant.BOT_USER_ID, "app_id": quant.APP_ID,
                 "team_id": values.get("SLACK_TEAM_ID")}:
        raise ValueError("quant_resume_slack_identity")
    if any(activity().values()):
        raise ValueError("quant_resume_activity_not_drained")
    preserved = {name: (STATE / name).read_bytes() for name in (
        "secrets/slack-credentials.json", "config/roles.json", "config/research-profiles.json",
        "config/research-qlab.json")}
    changed = False
    run(["docker", "stop", "--time", "900", "quant-company-quant-feed-worker-1"])
    record = {"phase": "prepared", "commit": args.commit, "channel": args.channel,
              "operator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "qualifications": proofs, "policy_before": probe["policy"], "policy_after": probe["live_policy"],
              "service_inventory_before": before, "started_at": time.time()}
    try:
        if any(activity().values()):
            raise ValueError("quant_resume_activity_started_during_stop")
        unresolved = run(["docker", "exec", "-u", "postgres", "quant-company-postgres-1", "psql",
                          "-XAt", "-v", "ON_ERROR_STOP=1", "-d", "quant_company", "-c",
                          "SELECT count(*) FROM quant_feed_publications p JOIN outbox o ON o.id=p.id "
                          "WHERE o.status NOT IN ('delivered','stale')"])
        if unresolved != "0":
            raise ValueError("quant_resume_prior_delivery_unresolved")
        helper.atomic(journal.with_suffix(".env"), original)
        record["phase"] = "activating"
        helper.atomic(journal, json.dumps(record).encode())
        changed = True
        quant.setenv(helper, {"QUANT_FEED_PUBLISH_ENABLED": "true"})
        compose(helper, current, "up", "-d", "--no-deps", "--force-recreate", "--wait",
                "--wait-timeout", "120", "api", "dispatch")
        after_probe = publication_probe()
        if (not after_probe["authorized"] or not after_probe["publish_enabled"]
                or after_probe["channel"] != args.channel or after_probe["policy"] != probe["live_policy"]):
            raise ValueError("quant_resume_gate_not_propagated")
        compose(helper, current, "up", "-d", "--no-deps", "--force-recreate", "--wait",
                "--wait-timeout", "120", "quant-feed-worker")
        after = inventory()
        if set(after) != set(before):
            raise ValueError("quant_resume_inventory_changed")
        for name, previous in before.items():
            row = after[name]
            if name in selected:
                if (not row["running"] or row["oom"] or row["image_id"] != previous["image_id"]
                        or row["image_revision"] != args.commit or row["quant_publish"] != "true"
                        or row["health"] not in (None, "healthy")):
                    raise ValueError("quant_resume_selected_service_unhealthy")
            elif row != previous:
                raise ValueError("quant_resume_independent_service_changed")
        if any((STATE / name).read_bytes() != raw for name, raw in preserved.items()):
            raise ValueError("quant_resume_unrelated_config_changed")
        record.update(phase="live_active", publication_enabled=True, completed_at=time.time(),
                      independent_services_preserved=True, old_documents_untouched=True)
        helper.atomic(journal, json.dumps(record).encode())
        print(json.dumps(record), flush=True)
    except BaseException as exc:
        # Once production has begun work, do not replay a call or assume a Slack
        # write did not happen. Preserve the journal for explicit reconciliation.
        if changed and any(activity().values()):
            record.update(phase="intervention_required", error=type(exc).__name__)
            helper.atomic(journal, json.dumps(record).encode())
            raise
        if changed:
            helper.atomic(env, original)
            compose(helper, current, "up", "-d", "--no-deps", "--force-recreate", "--wait",
                    "--wait-timeout", "120", *SELECTED)
        else:
            run(["docker", "start", "quant-company-quant-feed-worker-1"])
        record.update(phase="rolled_back", publication_enabled=False, error=type(exc).__name__)
        helper.atomic(journal, json.dumps(record).encode())
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stage", "cutover", "resume"))
    parser.add_argument("base")
    parser.add_argument("commit")
    parser.add_argument("--archive")
    parser.add_argument("--archive-sha256")
    parser.add_argument("--qdata-tree-sha256")
    parser.add_argument("--channel")
    parser.add_argument("--qualification", action="append")
    parser.add_argument("--preserve-publication", action="store_true",
                        help="Cut over without changing an already-active publication gate after protocol qualification")
    args = parser.parse_args()
    if not all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (args.base, args.commit)):
        raise ValueError("invalid_release_commit")
    if os.geteuid() != 0:
        raise ValueError("root_required")
    os.umask(0o077)
    with (STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = CURRENT.resolve()
        if previous.name != args.base:
            raise ValueError("production_baseline_changed")
        target = CURRENT.parent / "releases" / args.commit
        journal = STATE / "releases" / ("quant-feed-" + args.commit + ".json")
        helper = module(previous / "deploy/maintenance_release.py")
        if args.action == "stage":
            if not args.archive or not args.archive_sha256 or not args.qdata_tree_sha256 or journal.exists():
                raise ValueError("quality_stage_requires_new_exact_inputs")
            stage(args, previous, target, journal, helper)
        elif args.action == "cutover":
            cutover(args, previous, target, journal, helper)
        else:
            resume(args, previous, journal, helper)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": type(exc).__name__,
                    "code": str(exc) if isinstance(exc, ValueError) else "release_command_failed",
                }
            ),
            flush=True,
        )
        sys.exit(1)
