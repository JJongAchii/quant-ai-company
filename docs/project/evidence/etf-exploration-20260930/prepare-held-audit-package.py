"""Freeze the merged, immutable held-audit scheduler repair for inactive qualification."""

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SOURCE = "ec1fbeb29a49420a367f06af596283e6ead4ab72"
TARGET = ROOT / ".local/held-audit-20261006"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def main():
    TARGET.mkdir(mode=0o700, parents=True, exist_ok=True)
    paths = ["src", "deploy", "scripts", "tests", "pyproject.toml", "uv.lock", "README.md"]
    archive = TARGET / (SOURCE + "-company.tar")
    bundle = TARGET / (SOURCE + "-company.bundle")
    manifest = TARGET / (SOURCE + "-package.json")
    assert not any(path.exists() for path in (bundle, manifest)), "read_existing_package_before_replacing"
    if archive.exists():
        assert archive.read_bytes() == git("archive", "--format=tar", SOURCE, *paths)
    else:
        git("archive", "--format=tar", "--output=" + str(archive), SOURCE, *paths)
    # Git bundle exports named refs. The snapshot includes this exact ancestor;
    # prepare_release checks out SOURCE and verifies that clean tree separately.
    ref = "HEAD"
    git("merge-base", "--is-ancestor", SOURCE, ref)
    git("bundle", "create", str(bundle), ref)
    names = git("ls-tree", "-r", "--name-only", SOURCE, "src").decode().splitlines()
    value = {"schema_version": 1, "source_commit": SOURCE,
             "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
             "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
             "files": {name: hashlib.sha256(git("show", SOURCE + ":" + name)).hexdigest() for name in names},
             "production_changed": False, "scientific_trials_added": 0}
    manifest.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    print(json.dumps({key: value[key] for key in ("source_commit", "archive_sha256", "bundle_sha256")} | {
        "source_files": len(names), "archive_bytes": archive.stat().st_size, "bundle_bytes": bundle.stat().st_size,
        "directory": str(TARGET.relative_to(ROOT))}))


if __name__ == "__main__":
    main()
