"""Archive committed company code for an inactive runtime qualification."""

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SOURCE = "bb1e42e8a33899f7a290e96bd71d3540a23eeb37"
TARGET = ROOT / ".local/data-response-20261006"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def main():
    TARGET.mkdir(mode=0o700, parents=True, exist_ok=True)
    archive = TARGET / (SOURCE + "-src.tar")
    assert not archive.exists(), "read_back_existing_package_before_replacement"
    git("archive", "--format=tar", "--output=" + str(archive), SOURCE, "src")
    names = git("ls-tree", "-r", "--name-only", SOURCE, "src").decode().splitlines()
    manifest = {"schema_version": 1, "source_commit": SOURCE,
                "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "files": {name: hashlib.sha256(git("show", SOURCE + ":" + name)).hexdigest()
                          for name in names},
                "production_changed": False, "scientific_trials_added": 0}
    path = TARGET / (SOURCE + "-src.json")
    path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"archive": str(archive.relative_to(ROOT)), "manifest": str(path.relative_to(ROOT)),
                      "source_commit": SOURCE, "archive_sha256": manifest["archive_sha256"],
                      "source_files": len(names)}))


if __name__ == "__main__":
    main()
