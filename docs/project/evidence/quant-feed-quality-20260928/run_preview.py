"""Run one bounded Quant editorial qualification against frozen production originals."""

import importlib.util
import json
import os
import subprocess
from pathlib import Path

STATE = Path("/var/lib/quant-company")
ROOT = Path("/opt/quant-company/current").resolve()
OPERATION = STATE / "operations/quant-feed-quality-20260928"
COMMIT = "12398af25dc111fd93d99fa3408fbc66cb699121"
NEGATIVE = "eab01733dab66494bd5391d6b47154a3c80633108993eb4c0bf85210e59a6003"
POSITIVE = "ea2936c4b831e4542fa83c77fd234e5913315eba5d9f67904189ba9fa4575db9"


def load(path):
    spec = importlib.util.spec_from_file_location("quant_preview_support", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    if os.geteuid() != 0 or ROOT.name != COMMIT:
        raise ValueError("preview_requires_exact_qualified_release_and_root")
    env = dict(
        line.split("=", 1)
        for line in (STATE / "config/runtime.env").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    if (env.get("RELEASE_COMMIT") != COMMIT or env.get("QUANT_FEED_ENABLED") != "true"
            or env.get("QUANT_FEED_PUBLISH_ENABLED") != "false"):
        raise ValueError("quant_publication_not_paused")
    release = load(OPERATION / "release.py")
    if any(release.activity().values()):
        raise ValueError("quant_or_outbox_activity_not_drained")
    receipt_dir = OPERATION / "preview"
    if (not receipt_dir.is_dir() or receipt_dir.stat().st_uid != 10001
            or receipt_dir.stat().st_mode & 0o777 != 0o700):
        raise ValueError("preview_receipt_directory_permissions")
    source = ROOT / "scripts/qualify_quant_editorial.py"
    helper = load(ROOT / "deploy/maintenance_release.py")
    overlay = ROOT / "deploy/data-watch.compose.yaml"
    command = [*helper.compose_command(ROOT), "--profile", "data-watch", "-f", str(overlay),
               "run", "--rm", "--no-deps", "-T",
               "-v", f"{source}:/qualification/qualify.py:ro",
               "-v", f"{receipt_dir}:/qualification/receipts",
               "quant-feed-worker", "python", "/qualification/qualify.py",
               "--negative", NEGATIVE, "--positive", POSITIVE,
               "--output", "/qualification/receipts/qualification.json", "--live"]
    result = subprocess.run(command, check=False, timeout=3600)
    receipt = receipt_dir / "qualification.json"
    state = json.loads(receipt.read_text()).get("state") if receipt.exists() else "missing_receipt"
    print(json.dumps({"candidate": COMMIT, "receipt_state": state,
                      "publication_enabled": False, "slack_writes": False,
                      "returncode": result.returncode}), flush=True)
    if result.returncode or state != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
