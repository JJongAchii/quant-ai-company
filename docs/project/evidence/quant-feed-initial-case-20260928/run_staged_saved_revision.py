"""Run one saved revision through the source-verified app image; no cutover."""

import importlib.util
import json
import os
import subprocess
from pathlib import Path

STATE = Path("/var/lib/quant-company")
OPERATION = STATE / "operations/quant-feed-quality-20260928"
CURRENT = Path("/opt/quant-company/current")
BASE = "c54667e2827506d50b4cbfaa63d924f2b69f21ab"
CANDIDATE = "9ef85902c0c5ff7aa323a1853809d3f065398c23"
TARGET = CURRENT.parent / "releases" / CANDIDATE
SOURCE_RECEIPT = "qualification-regime.json"
OUTPUT_RECEIPT = "regime-revision-critic-v8.json"


def load(path):
    spec = importlib.util.spec_from_file_location("staged_critic_support", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    if os.geteuid() != 0 or CURRENT.resolve().name != BASE or not TARGET.is_dir():
        raise ValueError("saved_revision_requires_exact_staged_release")
    values = dict(line.split("=", 1) for line in (STATE / "config/runtime.env").read_text().splitlines()
                  if "=" in line and not line.startswith("#"))
    if (values.get("RELEASE_COMMIT") != BASE or values.get("QUANT_FEED_ENABLED") != "true"
            or values.get("QUANT_FEED_PUBLISH_ENABLED") != "false"):
        raise ValueError("saved_revision_requires_paused_quant")
    release = load(OPERATION / "release.py")
    journal = json.loads((STATE / "releases" / f"quant-feed-{CANDIDATE}.json").read_text())
    images = [item["id"] for item in journal["images"] if item["target"] == "app"]
    image_id = release.run(["docker", "image", "inspect", "--format", "{{.Id}}",
                            f"quant-company:{CANDIDATE}"])
    app_records = [item for item in journal["images"] if item["target"] == "app"]
    if (journal["phase"] != "staging" or journal["commit"] != CANDIDATE
            or len(images) != 1 or image_id != images[0] or len(app_records) != 1
            or not app_records[0]["source_matches_commit"]):
        raise ValueError("saved_revision_app_image_not_source_verified")
    if any(release.activity().values()):
        raise ValueError("saved_revision_activity_not_drained")
    receipt_dir = OPERATION / "preview"
    if (receipt_dir.stat().st_uid != 10001 or receipt_dir.stat().st_mode & 0o777 != 0o700
            or not (receipt_dir / SOURCE_RECEIPT).is_file()):
        raise ValueError("saved_revision_receipt_permissions")
    before = release.inventory()
    preserved = {name: row for name, row in before.items() if name.startswith("quant-company-")}
    required = ("quant-company-api-1", "quant-company-dispatch-1",
                "quant-company-quant-feed-worker-1", "quant-company-postgres-1")
    if any(name not in preserved or not preserved[name]["running"] or preserved[name]["oom"]
           for name in required):
        raise ValueError("saved_revision_service_not_running")
    helper = load(TARGET / "deploy/maintenance_release.py")
    overlay = TARGET / "deploy/data-watch.compose.yaml"
    compose = [*helper.compose_command(TARGET), "--profile", "data-watch", "-f", str(overlay)]
    environment = os.environ.copy()
    environment.update(RELEASE_COMMIT=CANDIDATE, QDATA_COMMIT=journal["qdata_commit"],
                       QDATA_BUILD_CONTEXT=str(TARGET / "qdata"))
    configuration = json.loads(release.run([*compose, "config", "--format", "json"], env=environment))
    if configuration["services"]["quant-feed-worker"]["image"] != f"quant-company:{CANDIDATE}":
        raise ValueError("saved_revision_compose_image_mismatch")
    source = OPERATION / "qualify_saved_revision.py"
    command = [*compose, "run", "--rm", "--no-deps", "-T",
               "-v", f"{source}:/qualification/qualify.py:ro",
               "-v", f"{receipt_dir}:/qualification/receipts",
               "quant-feed-worker", "python", "/qualification/qualify.py",
               "--source", f"/qualification/receipts/{SOURCE_RECEIPT}",
               "--output", f"/qualification/receipts/{OUTPUT_RECEIPT}", "--live"]
    result = subprocess.run(command, check=False, timeout=3600, env=environment)
    after = release.inventory()
    keys = ("id", "image_id", "running", "oom", "restarts")
    preserved_unchanged = all(after.get(name) is not None
                              and all(after[name][key] == row[key] for key in keys)
                              for name, row in preserved.items())
    receipt = receipt_dir / OUTPUT_RECEIPT
    state = json.loads(receipt.read_text()).get("state") if receipt.exists() else "missing_receipt"
    print(json.dumps({"candidate": CANDIDATE, "release_phase": journal["phase"],
                      "app_image_source_verified": True, "receipt_state": state,
                      "running_services_preserved": preserved_unchanged,
                      "current_release_unchanged": CURRENT.resolve().name == BASE,
                      "publication_enabled": False, "slack_writes": False,
                      "returncode": result.returncode}), flush=True)
    if result.returncode or state != "passed" or not preserved_unchanged or CURRENT.resolve().name != BASE:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
