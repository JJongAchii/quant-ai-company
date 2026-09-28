"""Qualify a staged Quant image without changing any running service or publication flag."""

import importlib.util
import json
import os
import subprocess
from pathlib import Path

STATE = Path("/var/lib/quant-company")
OPERATION = STATE / "operations/quant-feed-quality-20260928"
CURRENT = Path("/opt/quant-company/current")
BASE = "ccdcc43c2a0985dcc035262f33d19e0ae979f0a1"
CANDIDATE = "0c6dcba5ce747f242c2aa919ab70e71078df9e1e"
TARGET = CURRENT.parent / "releases" / CANDIDATE
NEGATIVE = "eab01733dab66494bd5391d6b47154a3c80633108993eb4c0bf85210e59a6003"
POSITIVE = "ea2936c4b831e4542fa83c77fd234e5913315eba5d9f67904189ba9fa4575db9"
RECEIPT = "qualification-normalized.json"


def load(path):
    spec = importlib.util.spec_from_file_location("quant_staged_support", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    if os.geteuid() != 0 or CURRENT.resolve().name != BASE or not TARGET.is_dir():
        raise ValueError("staged_preview_requires_exact_base")
    envfile = STATE / "config/runtime.env"
    values = dict(line.split("=", 1) for line in envfile.read_text().splitlines()
                  if "=" in line and not line.startswith("#"))
    if (values.get("RELEASE_COMMIT") != BASE or values.get("QUANT_FEED_ENABLED") != "true"
            or values.get("QUANT_FEED_PUBLISH_ENABLED") != "false"):
        raise ValueError("staged_preview_requires_paused_quant")
    release = load(OPERATION / "release.py")
    journal = json.loads((STATE / "releases" / f"quant-feed-{CANDIDATE}.json").read_text())
    images = [item["id"] for item in journal["images"] if item["target"] == "app"]
    if journal["phase"] != "staged" or journal["commit"] != CANDIDATE or len(images) != 1:
        raise ValueError("staged_preview_image_not_qualified")
    image_id = release.run(["docker", "image", "inspect", "--format", "{{.Id}}",
                            f"quant-company:{CANDIDATE}"])
    if image_id != images[0] or any(release.activity().values()):
        raise ValueError("staged_preview_image_or_activity_changed")
    receipt_dir = OPERATION / "preview"
    if (not receipt_dir.is_dir() or receipt_dir.stat().st_uid != 10001
            or receipt_dir.stat().st_mode & 0o777 != 0o700):
        raise ValueError("staged_preview_receipt_permissions")
    preserved = ("quant-company-api-1", "quant-company-dispatch-1",
                 "quant-company-quant-feed-worker-1", "quant-company-housing-feed-worker-1",
                 "quant-company-news-worker-1", "quant-company-worker-1",
                 "quant-company-postgres-1")
    before = release.inventory()
    if any(name not in before for name in preserved):
        raise ValueError("staged_preview_service_missing")
    source = TARGET / "scripts/qualify_quant_editorial.py"
    helper = load(TARGET / "deploy/maintenance_release.py")
    overlay = TARGET / "deploy/data-watch.compose.yaml"
    compose = [*helper.compose_command(TARGET), "--profile", "data-watch", "-f", str(overlay)]
    environment = os.environ.copy()
    environment.update(RELEASE_COMMIT=CANDIDATE, QDATA_COMMIT=journal["qdata_commit"],
                       QDATA_BUILD_CONTEXT=str(TARGET / "qdata"))
    configuration = json.loads(release.run([*compose, "config", "--format", "json"], env=environment))
    if configuration["services"]["quant-feed-worker"]["image"] != f"quant-company:{CANDIDATE}":
        raise ValueError("staged_preview_compose_image_mismatch")
    command = [*compose,
               "run", "--rm", "--no-deps", "-T",
               "-v", f"{source}:/qualification/qualify.py:ro",
               "-v", f"{receipt_dir}:/qualification/receipts",
               "quant-feed-worker", "python", "/qualification/qualify.py",
               "--negative", NEGATIVE, "--positive", POSITIVE,
               "--output", f"/qualification/receipts/{RECEIPT}", "--live"]
    result = subprocess.run(command, check=False, timeout=3600, env=environment)
    after = release.inventory()
    preserved_unchanged = all(after.get(name, {}).get("id") == before[name]["id"]
                              and after.get(name, {}).get("restarts") == before[name]["restarts"]
                              for name in preserved)
    receipt = receipt_dir / RECEIPT
    state = json.loads(receipt.read_text()).get("state") if receipt.exists() else "missing_receipt"
    print(json.dumps({"candidate": CANDIDATE, "receipt_state": state,
                      "running_services_preserved": preserved_unchanged,
                      "current_release_unchanged": CURRENT.resolve().name == BASE,
                      "publication_enabled": False, "slack_writes": False,
                      "returncode": result.returncode}), flush=True)
    if result.returncode or state != "passed" or not preserved_unchanged or CURRENT.resolve().name != BASE:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
