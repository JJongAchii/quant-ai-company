"""Qualify a new positive original in the live combined app image, without Slack writes."""

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path

STATE = Path("/var/lib/quant-company")
CURRENT = Path("/opt/quant-company/current")
BASE = "c54667e2827506d50b4cbfaa63d924f2b69f21ab"
CANDIDATE = "0e09b852e6a36e2fee52fb1cc7baab1c83a757ce"
TARGET = CURRENT.parent / "releases" / CANDIDATE
NEGATIVE = "eab01733dab66494bd5391d6b47154a3c80633108993eb4c0bf85210e59a6003"
POSITIVE = "5209c2517c42ae1b492e758c5f5f7dfeca5645d837aa54341a46b117f96f8d65"
RECEIPT = "qualification-cost-window-v8.json"
SOURCE = STATE / "operations/quant-feed-continuation-20260928/qualify_quant_editorial.py"
SOURCE_SHA256 = "5e12d4ee0083b8fff24d3e64e612c5d4657e1550917e30c2a90fcc4e47f132ca"
RECEIPT_DIR = STATE / "operations/quant-feed-continuation-20260928/preview"


def run(command, *, env=None):
    result = subprocess.run(command, capture_output=True, check=True, timeout=60, env=env)
    return result.stdout.decode().strip()


def inventory():
    names = run(["docker", "ps", "-aq"]).split()
    rows = json.loads(run(["docker", "inspect", *names])) if names else []
    return {
        row["Name"].lstrip("/"): {
            "id": row["Id"],
            "image_id": row["Image"],
            "running": row["State"]["Running"],
            "oom": row["State"]["OOMKilled"],
            "restarts": row["RestartCount"],
        }
        for row in rows if row["Name"].startswith("/quant-company-")
    }


def activity():
    query = """SELECT json_build_object(
      'running_quant_calls', (SELECT count(*) FROM quant_feed_calls WHERE state='running'),
      'pending_quant_outbox', (SELECT count(*) FROM quant_feed_publications p
          JOIN outbox o ON o.id=p.id WHERE o.status IN ('pending','sending')),
      'sending_outbox', (SELECT count(*) FROM outbox WHERE status='sending'),
      'quota_paused', EXISTS(SELECT 1 FROM runtime_control WHERE paused_until>now()))::text"""
    return json.loads(run(["docker", "exec", "-u", "postgres", "quant-company-postgres-1",
                           "psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1",
                           "-d", "quant_company", "-c", query]))


def publication_count():
    return int(run(["docker", "exec", "-u", "postgres", "quant-company-postgres-1",
                    "psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1",
                    "-d", "quant_company", "-c", "SELECT count(*) FROM quant_feed_publications"]))


def source_inventory(root):
    if any(path.is_symlink() for path in root.rglob("*")):
        raise ValueError("source_symlink")
    return {"quant_company/" + str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file() and "__pycache__" not in path.parts}


def preflight():
    if os.geteuid() != 0 or CURRENT.resolve().name != BASE or not TARGET.is_dir():
        raise ValueError("combined_preview_requires_exact_base_and_target")
    values = runtime_values()
    if (values.get("RELEASE_COMMIT") != BASE or values.get("QUANT_FEED_ENABLED") != "true"
            or values.get("QUANT_FEED_PUBLISH_ENABLED") != "false"):
        raise ValueError("quant_publication_not_paused")
    before = inventory()
    for name in ("quant-company-api-1", "quant-company-dispatch-1", "quant-company-quant-feed-worker-1",
                 "quant-company-housing-feed-worker-1", "quant-company-news-worker-1",
                 "quant-company-worker-1", "quant-company-postgres-1"):
        if name not in before or not before[name]["running"] or before[name]["oom"]:
            raise ValueError("required_service_not_running")
    image = json.loads(run(["docker", "image", "inspect", "quant-company:" + CANDIDATE]))[0]
    pin = json.loads((TARGET / "deploy/qdata-source.json").read_text())
    if (image["Config"]["Labels"].get("org.opencontainers.image.revision") != CANDIDATE
            or image["Config"]["Labels"].get("org.quant-company.qdata-revision") != pin["commit"]):
        raise ValueError("combined_app_image_revision_mismatch")
    verify = (
        "import hashlib,importlib.util,json,pathlib;"
        "root=pathlib.Path(importlib.util.find_spec('quant_company').origin).parent;"
        "print(json.dumps({'quant_company/'+str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() "
        "for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}))"
    )
    actual = json.loads(run(["docker", "run", "--rm", "--network=none", "--memory=128m", "--cpus=0.5",
                             "--entrypoint", "python", image["Id"], "-c", verify]))
    if actual != source_inventory(TARGET / "src/quant_company"):
        raise ValueError("combined_app_installed_source_mismatch")
    spec = importlib.util.spec_from_file_location("combined_preview_compose", TARGET / "deploy/maintenance_release.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    overlay = TARGET / "deploy/data-watch.compose.yaml"
    command = [*helper.compose_command(TARGET), "--profile", "data-watch", "-f", str(overlay)]
    environment = os.environ.copy()
    environment.update(RELEASE_COMMIT=CANDIDATE, QDATA_COMMIT=pin["commit"],
                       QDATA_BUILD_CONTEXT=str(TARGET / "qdata"))
    config = json.loads(run([*command, "config", "--format", "json"], env=environment))
    if config["services"]["quant-feed-worker"]["image"] != "quant-company:" + CANDIDATE:
        raise ValueError("combined_preview_compose_image_mismatch")
    if (SOURCE.is_symlink() or not SOURCE.is_file()
            or hashlib.sha256(SOURCE.read_bytes()).hexdigest() != SOURCE_SHA256):
        raise ValueError("combined_preview_qualifier_source_mismatch")
    return before, command, environment


def runtime_values():
    return dict(line.split("=", 1) for line in (STATE / "config/runtime.env").read_text().splitlines()
                if "=" in line and not line.startswith("#"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--check-only", action="store_true")
    choice.add_argument("--live", action="store_true")
    args = parser.parse_args()
    before, compose, environment = preflight()
    pending = activity()
    if args.check_only:
        print(json.dumps({"candidate": CANDIDATE, "app_source_verified": True,
                          "publication_enabled": False, "activity": pending,
                          "live_preview_ready": not any(pending.values())}), flush=True)
        return
    if any(pending.values()) or (RECEIPT_DIR / RECEIPT).exists():
        raise ValueError("combined_preview_activity_or_existing_receipt")
    if RECEIPT_DIR.is_symlink():
        raise ValueError("combined_preview_receipt_symlink")
    if not RECEIPT_DIR.exists():
        RECEIPT_DIR.mkdir(parents=True, mode=0o700)
        os.chown(RECEIPT_DIR, 10001, 10001)
    if RECEIPT_DIR.stat().st_uid != 10001 or RECEIPT_DIR.stat().st_mode & 0o777 != 0o700:
        raise ValueError("combined_preview_receipt_permissions")
    publications_before = publication_count()
    command = [*compose, "run", "--rm", "--no-deps", "-T",
               "-v", f"{SOURCE}:/qualification/qualify.py:ro",
               "-v", f"{RECEIPT_DIR}:/qualification/receipts",
               "quant-feed-worker", "python", "/qualification/qualify.py",
               "--negative", NEGATIVE, "--positive", POSITIVE,
               "--output", f"/qualification/receipts/{RECEIPT}", "--live"]
    result = subprocess.run(command, check=False, timeout=3600, env=environment)
    after = inventory()
    unchanged = after == before
    publication_unchanged = publication_count() == publications_before
    publication_paused = runtime_values().get("QUANT_FEED_PUBLISH_ENABLED") == "false"
    receipt = RECEIPT_DIR / RECEIPT
    state = json.loads(receipt.read_text()).get("state") if receipt.exists() else "missing_receipt"
    print(json.dumps({"candidate": CANDIDATE, "receipt_state": state,
                      "running_services_preserved": unchanged,
                      "current_release_unchanged": CURRENT.resolve().name == BASE,
                      "publication_enabled": not publication_paused,
                      "quant_publication_count_unchanged": publication_unchanged,
                      "returncode": result.returncode}), flush=True)
    if (result.returncode or state != "passed" or not unchanged or CURRENT.resolve().name != BASE
            or not publication_paused or not publication_unchanged):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
