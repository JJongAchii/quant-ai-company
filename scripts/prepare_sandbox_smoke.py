"""Prepare an isolated, credential-free Linux Python runtime for engineering fixtures.

This copies a local system Python/standard library and its ELF library closure.
It does not provision a scientific profile, download packages, or modify a service.
"""

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

from quant_company.research.sandbox import runtime_digest


def sha_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != "Linux":
        raise SystemExit("Linux fixture runtime required")
    root = args.destination.absolute()
    if root.exists():
        raise SystemExit("Use a fresh qualification runtime destination")
    root.mkdir(parents=True, mode=0o700)
    runtime = root / "runtime"
    runtime.mkdir()
    executable = Path(sys.executable).resolve()
    stdlib = Path(sysconfig.get_path("stdlib")).resolve()
    if not str(executable).startswith("/usr/") or not str(stdlib).startswith("/usr/"):
        raise SystemExit("Invoke with the operator's system /usr/bin/python3")
    copied_python = runtime / executable.relative_to("/")
    copied_python.parent.mkdir(parents=True)
    shutil.copy2(executable, copied_python)
    copied_stdlib = runtime / stdlib.relative_to("/")
    shutil.copytree(stdlib, copied_stdlib, symlinks=False,
                    ignore=shutil.ignore_patterns("site-packages", "dist-packages", "__pycache__", "test", "tests"))
    binaries = [executable, *stdlib.rglob("*.so")]
    libraries = set()
    for binary in binaries:
        result = subprocess.run(["ldd", str(binary)], capture_output=True, text=True, check=False)
        if result.returncode:
            raise SystemExit("Unable to resolve the system Python runtime closure")
        if "not found" in result.stdout:
            raise SystemExit("System Python has an unresolved ELF dependency")
        libraries.update(Path(value) for value in re.findall(r"(/[^\s()]+)", result.stdout))
    for source in sorted(libraries):
        destination = runtime / source.relative_to("/")
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source.resolve(strict=True), destination)
    mounts = [{"source": str(child), "target": "/" + child.name,
               "sha256": runtime_digest(child, (runtime,))} for child in sorted(runtime.iterdir())]
    profile = {"profile_id": "engineering-namespace-smoke-v1", "python_executable": str(executable),
               "python_sha256": sha_file(copied_python), "mounts": mounts,
               "allowed_roots": [str(runtime)], "bwrap_executable": "/usr/bin/bwrap"}
    (root / "profile.json").write_text(json.dumps(profile, indent=2) + "\n")
    print(json.dumps({"kind": "engineering_fixture_only", "hostname": platform.node(),
                      "profile": str(root / "profile.json"), "mount_count": len(mounts)}, sort_keys=True))


if __name__ == "__main__":
    main()
