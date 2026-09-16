#!/usr/bin/env python3
"""Prepare host directories and fresh secret files; default only prints the plan."""

import argparse
import json
import os
import secrets
from pathlib import Path


def create_file(path: Path, content: str, mode: int) -> None:
    # Existing credentials/config are never regenerated or overwritten.
    try:
        with path.open("x") as handle:
            handle.write(content)
        path.chmod(mode)
    except FileExistsError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/quant-company"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    state = args.state_dir
    if not state.is_absolute() or state == Path("/"):
        parser.error("--state-dir must be a dedicated absolute directory")
    directories = {
        "config": (0, 10001, 0o750), "secrets": (0, 0, 0o700),
        "codex/auth": (10001, 10001, 0o700), "codex/jobs": (10001, 10001, 0o700),
        "caddy/data": (10001, 10001, 0o700), "caddy/config": (10001, 10001, 0o700),
        "postgres": (0, 0, 0o700), "backups": (0, 0, 0o700),
    }
    print(json.dumps({"mode": "apply" if args.apply else "preview-only", "state_dir": str(state),
                      "directories": list(directories), "overwrite_existing": False}, indent=2))
    if not args.apply:
        return
    if os.geteuid() != 0:
        raise SystemExit("Run --apply as root on the target Linux host")
    state.mkdir(parents=True, exist_ok=True)
    state.chmod(0o711)  # Traversal only; each child enforces its own ownership.
    for relative, (uid, gid, mode) in directories.items():
        folder = state / relative
        folder.mkdir(parents=True, exist_ok=True)
        os.chown(folder, uid, gid)
        folder.chmod(mode)
    for name in ("database_password", "database_admin_password", "operator_token", "model_runtime_token"):
        create_file(state / "secrets" / name, secrets.token_hex(32) + "\n", 0o444)
    # File bind-mounts preserve host ownership/mode. Files are readable inside only the
    # explicitly authorized containers; the host secrets directory itself is root-only.
    create_file(state / "secrets/temporal_api_key", "REPLACE_TEMPORAL_API_KEY\n", 0o444)
    deploy = Path(__file__).parent
    create_file(state / "secrets/slack-credentials.json",
                (deploy / "slack-credentials.example.json").read_text(), 0o444)
    create_file(state / "config/runtime.env", (deploy / ".env.example").read_text(), 0o444)
    create_file(state / "config/roles.json",
                (deploy.parent / "src/quant_company/roles.json").read_text(), 0o444)
    print("Prepared state without printing credentials. Configure Temporal and Slack before startup.")


if __name__ == "__main__":
    main()
