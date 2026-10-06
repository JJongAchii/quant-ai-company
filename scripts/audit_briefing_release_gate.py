"""Read-only check of a briefing candidate against the installed release policy."""

import argparse
import hashlib
import importlib.util
import json
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PATHS = ("src", "deploy", "slack-apps", "pyproject.toml", "uv.lock", "README.md", "AGENTS.md")


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def snapshot(commit, target, archive):
    with archive.open("wb") as output:
        subprocess.run(["git", "archive", "--format=tar.gz", commit], cwd=ROOT, check=True, stdout=output)
    with tarfile.open(archive, "r:gz") as source:
        source.extractall(target, filter="data")


def runtime_archive(commit, target):
    """Export exact committed build inputs, not a substitute maintenance checkout."""
    if target.exists():
        raise ValueError("runtime_archive_exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="briefing-runtime-archive-") as temp:
        archive = Path(temp) / "runtime.tar.gz"
        with archive.open("wb") as output:
            subprocess.run(["git", "archive", "--format=tar.gz", "--prefix=company/", commit,
                            *RUNTIME_PATHS], cwd=ROOT, check=True, stdout=output)
        inventory = {}
        with tarfile.open(archive, "r:gz") as source:
            for member in source.getmembers():
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ValueError("runtime_archive_nonregular_file")
                relative = Path(member.name).relative_to("company").as_posix()
                inventory[relative] = {"bytes": member.size,
                    "sha256": hashlib.sha256(source.extractfile(member).read()).hexdigest()}
        total = sum(row["bytes"] for row in inventory.values())
        if total > 32 * 1024 * 1024:
            raise ValueError("runtime_archive_exceeds_installed_size_bound")
        data = archive.read_bytes()
        with target.open("xb") as output:
            output.write(data)
    return {"scope": "Runtime build inputs only; not a full checkout or maintenance-policy approval",
            "commit": commit, "archive": str(target), "archive_sha256": hashlib.sha256(data).hexdigest(),
            "compressed_bytes": len(data), "uncompressed_bytes": total, "files": inventory,
            "historical_evidence": "Preserved in Git, excluded from runtime build inputs",
            "host_modified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", required=True, help="commit installed on the host")
    parser.add_argument("--candidate", required=True, help="reviewable candidate commit")
    parser.add_argument("--output", type=Path, help="optional JSON receipt path")
    parser.add_argument("--runtime-archive", type=Path,
                        help="optional bounded runtime artifact; does not authorize a protected release")
    args = parser.parse_args()
    previous = git("rev-parse", "--verify", args.previous + "^{commit}")
    candidate = git("rev-parse", "--verify", args.candidate + "^{commit}")

    with tempfile.TemporaryDirectory(prefix="briefing-release-gate-") as temp:
        base = Path(temp)
        before, after = base / "previous", base / "candidate"
        before.mkdir()
        after.mkdir()
        snapshot(previous, before, base / "previous.tar.gz")
        snapshot(candidate, after, base / "candidate.tar.gz")

        policy_path = before / "deploy/maintenance_release.py"
        spec = importlib.util.spec_from_file_location("installed_maintenance_release", policy_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("installed release policy cannot be loaded")
        policy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(policy)
        try:
            policy.validate_tree(before, after)
            error = None
        except ValueError as exc:
            error = str(exc)

        changes = git("diff", "--name-only", previous, candidate).splitlines()
        old_roles = {role["id"] for role in json.loads((before / "src/quant_company/roles.json").read_text())}
        new_roles = {role["id"] for role in json.loads((after / "src/quant_company/roles.json").read_text())}
        critical = [name for name in changes if name.startswith("deploy/") or name in {
            "pyproject.toml", "uv.lock", "src/quant_company/cli.py", "src/quant_company/config.py",
            "src/quant_company/db.py", "src/quant_company/state_schema.sql",
            "src/quant_company/roles.json", "src/quant_company/socket_mode.py",
        }]
        receipt = {
            "checked_at": datetime.now(UTC).isoformat(),
            "previous_commit": previous,
            "candidate_commit": candidate,
            "policy_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
            "validator": "installed deploy/maintenance_release.py:validate_tree",
            "accepted": error is None,
            "error": error,
            "changed_file_count": len(changes),
            "critical_changed_paths": critical,
            "roles_added": sorted(new_roles - old_roles),
            "roles_removed": sorted(old_roles - new_roles),
            "host_modified": False,
        }

    if args.runtime_archive:
        receipt["runtime_artifact"] = runtime_archive(candidate, args.runtime_archive)
    rendered = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
