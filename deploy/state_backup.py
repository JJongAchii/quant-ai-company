#!/usr/bin/env python3
"""Consistent office backup and restore-to-new-database. Preview is the default."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

DEPLOY = Path(__file__).resolve().parent
APP_SERVICES = ("worker", "dispatch", "api", "codex-runtime")


def config_values(path: Path) -> dict[str, str]:
    values = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or "$" in value:
            raise ValueError("Use plain KEY=value configuration; shell expansion is not supported")
        if any(marker in key for marker in ("TOKEN", "PASSWORD", "SECRET", "API_KEY")):
            raise ValueError("Runtime config must not contain secrets; use the dedicated secret files")
        values[key] = value.strip().strip("\"'")
    return values


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str], **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def unpack(archive: Path, destination: Path) -> dict:
    checksum = archive.with_suffix(archive.suffix + ".sha256").read_text().split()[0]
    if not re.fullmatch(r"[0-9a-f]{64}", checksum) or sha256(archive) != checksum:
        raise ValueError("Archive checksum mismatch")
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            path = PurePosixPath(member.name)
            allowed = member.name in {"manifest.json", "database.dump", "roles.json", "runtime.env"} or (
                len(path.parts) > 1 and path.parts[0] == "jobs"
            )
            if (not allowed or path.is_absolute() or ".." in path.parts or not member.isfile()
                    or member.issym() or member.islnk()):
                raise ValueError("Unexpected or unsafe archive member")
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(member) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
    manifest = json.loads((destination / "manifest.json").read_text())
    actual_files = {str(path.relative_to(destination)) for path in destination.rglob("*")
                    if path.is_file() and path != destination / "manifest.json"}
    if (manifest.get("schema_version") != 1 or set(manifest["files"]) != actual_files
            or not {"database.dump", "roles.json", "runtime.env"} <= actual_files):
        raise ValueError("Backup manifest does not match extracted files")
    for relative, expected in manifest["files"].items():
        if sha256(destination / relative) != expected:
            raise ValueError("Backup member checksum mismatch")
    if not re.fullmatch(r"[0-9a-f]{40}", manifest.get("release_commit", "")):
        raise ValueError("Backup has no exact release commit")
    if not (destination / "database.dump").stat().st_size:
        raise ValueError("Empty database archive")
    return manifest


def backup(args, cfg: dict[str, str], compose: list[str], state: Path) -> None:
    release = cfg.get("RELEASE_COMMIT", "")
    if not re.fullmatch(r"[0-9a-f]{40}", release):
        raise ValueError("Set RELEASE_COMMIT to the deployed exact commit")
    s3_uri = args.s3_uri or os.environ.get("BACKUP_S3_URI", "")
    if not re.fullmatch(r"s3://[a-z0-9.-]+/company/?", s3_uri):
        raise ValueError("Set --s3-uri or BACKUP_S3_URI to s3://bucket/company/")
    backup_root = state / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    os.chmod(backup_root, 0o700)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    with tempfile.TemporaryDirectory(prefix="staging-", dir=backup_root) as temporary:
        staging = Path(temporary)
        active = set(subprocess.check_output(compose + ["ps", "--services", "--status", "running"],
                                            text=True).splitlines())
        restart = [name for name in APP_SERVICES if name in active]
        try:
            # Stop writers before taking the DB snapshot and copying durable model receipts.
            if restart:
                run(compose + ["stop", "--timeout", "360", *restart])
            with (staging / "database.dump").open("wb") as output:
                run(compose + ["exec", "-T", "postgres", "pg_dump", "--username", "postgres",
                               "--format=custom", "--no-owner", "--no-privileges",
                               "--dbname", cfg.get("DATABASE_NAME", "quant_company")], stdout=output)
            shutil.copyfile(state / "config/roles.json", staging / "roles.json")
            shutil.copyfile(args.env_file, staging / "runtime.env")
            jobs = state / "codex/jobs"
            if any(path.is_symlink() for path in jobs.rglob("*")):
                raise ValueError("Model receipt tree contains an unexpected symbolic link")
            shutil.copytree(jobs, staging / "jobs")
        finally:
            if restart:
                run(compose + ["start", *reversed(restart)])
        files = {str(path.relative_to(staging)): sha256(path)
                 for path in sorted(staging.rglob("*")) if path.is_file()}
        manifest = {"schema_version": 1, "created_at": datetime.now(UTC).isoformat(),
                    "release_commit": release, "database": cfg.get("DATABASE_NAME", "quant_company"),
                    "includes_secrets": False, "files": files}
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        archive = backup_root / f"company-{stamp}.tar.gz"
        with tarfile.open(archive, "x:gz") as bundle:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    bundle.add(path, arcname=str(path.relative_to(staging)), recursive=False)
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(sha256(archive) + "  " + archive.name + "\n")
    destination = s3_uri.rstrip("/") + "/" + archive.name
    for source, target in ((archive, destination), (checksum, destination + ".sha256")):
        run(["aws", "s3", "cp", str(source), target, "--sse", "AES256", "--only-show-errors"])
    print(json.dumps({"backup": str(archive), "sha256": sha256(archive), "s3_uri": destination,
                      "release_commit": release, "secrets_included": False}))


def restore(args, cfg: dict[str, str], compose: list[str], state: Path) -> None:
    if not re.fullmatch(r"restore_[a-z0-9_]{1,55}", args.database or ""):
        raise ValueError("Target must be a new restore_* database")
    if args.database == cfg.get("DATABASE_NAME", "quant_company"):
        raise ValueError("Refusing to restore into the configured live database")
    parent = state / "backups/restored"
    parent.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix=args.database + "-", dir=parent))
    manifest = unpack(args.archive, destination)
    query = "SELECT 1 FROM pg_database WHERE datname = '" + args.database + "'"
    existing = subprocess.check_output(compose + ["exec", "-T", "postgres", "psql", "--username",
                                                  "postgres", "--dbname", "postgres", "--tuples-only",
                                                  "--no-align", "--command", query], text=True).strip()
    if existing:
        raise ValueError("Target database already exists; nothing was replaced")
    run(compose + ["exec", "-T", "postgres", "createdb", "--username", "postgres", "--owner", "company",
                   "--template", "template0", args.database])
    with (destination / "database.dump").open("rb") as source:
        run(compose + ["exec", "-T", "postgres", "pg_restore", "--username", "postgres", "--role", "company",
                       "--exit-on-error", "--single-transaction", "--no-owner", "--no-privileges",
                       "--dbname", args.database], stdin=source)
    print(json.dumps({"restored_database": args.database, "recovered_files": str(destination),
                      "release_commit": manifest["release_commit"], "cutover_performed": False,
                      "next": "Validate in isolation; reconcile pending/uncertain effects before any cutover."}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("backup", "restore"))
    parser.add_argument("--env-file", type=Path, default=DEPLOY / ".env")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--database")
    parser.add_argument("--s3-uri")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    cfg = config_values(args.env_file)
    state = Path(cfg.get("STATE_DIR", "/var/lib/quant-company"))
    if not state.is_absolute():
        parser.error("STATE_DIR must be absolute")
    if args.operation == "restore":
        if not args.archive or not re.fullmatch(r"restore_[a-z0-9_]{1,55}", args.database or ""):
            parser.error("Restore requires --archive and a new --database restore_* target")
        if args.database == cfg.get("DATABASE_NAME", "quant_company"):
            parser.error("Refusing to replace the live database")
    if not args.apply:
        print(json.dumps({"mode": "preview-only", "operation": args.operation, "state_dir": str(state),
                          "database": args.database, "external_calls": 0,
                          "backup_behavior": "briefly pause writers, pg_dump + config + model receipts, S3 copy",
                          "restore_behavior": "create new restore_* database; leave live config/receipts alone"}))
        return
    os.umask(0o077)
    compose = ["docker", "compose", "--env-file", str(args.env_file), "-f", str(DEPLOY / "compose.yaml")]
    # One local backup/restore process at a time. Locking never occurs in preview mode.
    import fcntl
    state.mkdir(parents=True, exist_ok=True)
    with (state / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        (backup if args.operation == "backup" else restore)(args, cfg, compose, state)


if __name__ == "__main__":
    main()
