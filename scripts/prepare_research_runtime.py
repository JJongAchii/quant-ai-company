"""Copy the first ETF's existing CPython 3.11 runtime offline, without installing.

Only an operator invokes this script. The supplied interpreter is trusted; its
site startup and .pth processing are disabled during metadata discovery. No
copied code is executed here. Import/parquet qualification is a separate step.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quant_company.research.sandbox import runtime_digest
from quant_company.research.workspace import canonical_digest, canonical_path

PROFILE_ID = "kr-etf-monthly-python-v1"
PACKAGES = {"numpy": "numpy", "pandas": "pandas", "pyarrow": "pyarrow", "dateutil": "python-dateutil",
            "six": "six", "pytz": "pytz", "tzdata": "tzdata"}
SYSTEM_LIBRARY_ROOTS = (Path("/lib"), Path("/lib64"), Path("/usr/lib"), Path("/usr/lib64"), Path("/usr/local/lib"))
EXCLUDED_NAMES = {"__pycache__", "test", "tests", "site-packages", "dist-packages", ".git", ".ssh", ".aws",
                  ".env", "sitecustomize.py", "usercustomize.py", "pyvenv.cfg", "direct_url.json"}

# No package import and no site/.pth startup. In Python 3.11, -S hides a venv's
# sys.prefix, so locate that venv from the requested executable, not base_prefix.
PROBE = r'''
import json
import sys
import sysconfig
from pathlib import Path

if sys.implementation.name != 'cpython' or sys.version_info[:2] != (3, 11):
    raise SystemExit('CPython 3.11 required')
requested = Path(sys.executable).absolute()
venv = requested.parent.parent
if (venv / 'pyvenv.cfg').is_file():
    prefix = venv
    candidates = [venv / name / 'python3.11' / 'site-packages' for name in ('lib', 'lib64')]
else:
    prefix = Path(sys.prefix)
    candidates = [Path(sysconfig.get_path(name)) for name in ('purelib', 'platlib')]
sites = sorted({str(path.resolve()) for path in candidates if path.is_dir()})
print(json.dumps({'implementation': 'cpython', 'version': sys.version.split()[0], 'prefix': str(prefix),
                  'executable': str(requested.resolve()), 'stdlib': sysconfig.get_path('stdlib'),
                  'libdir': sysconfig.get_config_var('LIBDIR'), 'sites': sites}, sort_keys=True))
'''


class RuntimePreparationError(ValueError):
    """Stable error code, never subprocess output or credential-bearing metadata."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise RuntimePreparationError(code)


def sha_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def elf_dynamic(path: Path) -> bool:
    with path.open("rb") as stream:
        header = stream.read(20)
    if not header.startswith(b"\x7fELF"):
        return False
    require(len(header) == 20 and header[5] in (1, 2), "invalid-elf-header")
    return int.from_bytes(header[16:18], "little" if header[5] == 1 else "big") in (2, 3)


def probe_interpreter(interpreter: Path) -> dict[str, Any]:
    requested = interpreter.absolute()
    try:
        executable = requested.resolve(strict=True)
        require(executable.is_file() and os.access(executable, os.X_OK) and elf_dynamic(executable),
                "trusted-linux-python-required")
        result = subprocess.run([str(requested), "-I", "-S", "-c", PROBE],
                                env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8"},
                                cwd="/", stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        require(result.returncode == 0, "python311-package-discovery-failed")
        value = json.loads(result.stdout)
        require(value["implementation"] == "cpython" and re.fullmatch(r"3\.11\.\d+", value["version"]) is not None,
                "python311-required")
        require(Path(value["executable"]).resolve(strict=True) == executable, "python-source-identity-mismatch")
        value["packages"] = discover_packages(tuple(Path(site) for site in value["sites"]))
        return value
    except (OSError, KeyError, TypeError, json.JSONDecodeError, subprocess.TimeoutExpired):
        raise RuntimePreparationError("python-source-unavailable") from None


def discover_packages(sites: tuple[Path, ...]) -> list[dict[str, Any]]:
    """Read just the allowlisted wheel metadata, without importing a package/.pth."""
    def normalize(name):
        return re.sub(r"[-_.]+", "-", name).lower()

    roots = {site.resolve(strict=True) for site in sites}
    result = []
    for module, distribution in PACKAGES.items():
        matches = [path for site in roots for path in site.glob("*.dist-info")
                   if normalize(path.name[:-10].rsplit("-", 1)[0]) == normalize(distribution)]
        require(len(matches) == 1, "package-metadata-missing-or-ambiguous")
        info = matches[0]
        require(info.is_dir() and info.resolve().is_relative_to(info.parent.resolve()), "dist-info-escapes-site")
        for filename in ("METADATA", "WHEEL", "direct_url.json"):
            child = info / filename
            require(not child.exists() or (child.is_file() and child.resolve().is_relative_to(info.resolve())),
                    "metadata-symlink-escapes-dist-info")
        metadata = importlib.metadata.Distribution.at(info)
        require(normalize(metadata.metadata.get("Name", "")) == normalize(distribution)
                and bool(metadata.version), "package-metadata-identity-mismatch")
        direct = info / "direct_url.json"
        if direct.exists():
            try:
                editable = json.loads(direct.read_text()).get("dir_info", {}).get("editable")
            except (ValueError, AttributeError):
                raise RuntimePreparationError("invalid-package-origin-metadata") from None
            require(not editable, "editable-package-rejected")
        site = info.parent.resolve()
        package = site / module
        if not package.is_dir():
            package = site / (module + ".py")
        require(package.exists() and package.resolve().is_relative_to(site), "package-missing-or-escapes-site")
        libs = site / (module + ".libs")
        require(not libs.exists() or (libs.is_dir() and libs.resolve().is_relative_to(site)), "package-libs-escape-site")
        result.append({"module": module, "distribution": distribution, "version": metadata.version,
                       "package": str(package), "dist_info": str(info), "libs": [str(libs)] if libs.exists() else []})
    return result


def parse_ldd(output: str, returncode: int) -> list[tuple[str | None, Path]]:
    require(returncode == 0, "elf-inspection-failed")
    dependencies = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line == "statically linked" or re.fullmatch(r"linux-(?:vdso|gate)\S*\s+\(0x[0-9a-fA-F]+\)", line):
            continue
        require("not found" not in line, "missing-elf-library")
        match = re.fullmatch(r"(?:(\S+)\s+=>\s+)?(/[^\s()]+)\s+\(0x[0-9a-fA-F]+\)", line)
        require(match is not None, "unrecognized-ldd-output")
        requested, source = match.groups()
        require(requested is None or "/" not in requested or requested.startswith("/"), "invalid-elf-library-name")
        dependencies.append((requested, Path(source)))
    return dependencies


def inspect_elf(path: Path, search_dirs: tuple[Path, ...]) -> list[tuple[str | None, Path]]:
    environment = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C",
                   "LD_LIBRARY_PATH": ":".join(map(str, search_dirs))}
    try:
        result = subprocess.run(["/usr/bin/ldd", str(path)], env=environment, cwd="/", stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimePreparationError("elf-inspection-unavailable") from None
    return parse_ldd(result.stdout, result.returncode)


class Copier:
    """One private destination, byte provenance, no source hardlinks or symlinks."""

    def __init__(self, destination: Path, trusted_roots: tuple[Path, ...],
                 tree_roots: tuple[Path, ...] | None = None):
        self.destination = destination
        self.trusted_roots = tuple(root.resolve(strict=True) for root in trusted_roots)
        self.tree_roots = tuple(root.resolve(strict=True) for root in (tree_roots or trusted_roots))
        self.files: dict[str, dict[str, Any]] = {}
        self.sources: dict[str, dict[str, Any]] = {}
        self.elf_sources: set[Path] = set()

    def trusted(self, path: Path) -> Path:
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise RuntimePreparationError("missing-or-cyclic-source") from None
        require(any(resolved == root or resolved.is_relative_to(root) for root in self.trusted_roots),
                "source-escapes-runtime-closure")
        return resolved

    def copy_file(self, source: Path, destination: Path) -> None:
        source = self.trusted(source)
        require(source.is_file(), "non-regular-runtime-source")
        require(destination.is_relative_to(self.destination) and ".." not in destination.parts,
                "destination-escapes-private-root")
        key = destination.relative_to(self.destination).as_posix()
        require(not destination.is_symlink(), "destination-symlink")
        digest = sha_file(source)
        if destination.exists():
            require(destination.is_file() and sha_file(destination) == digest, "conflicting-runtime-library")
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        previous = self.sources.get(str(source))
        if previous:
            require(previous["sha256"] == digest, "source-changed-during-copy")
            # Alias only an already private copy; never hardlink to the host source.
            os.link(self.destination / previous["copied_paths"][0], destination)
        else:
            with source.open("rb") as incoming, destination.open("xb") as outgoing:
                while chunk := incoming.read(1024 * 1024):
                    outgoing.write(chunk)
            require(sha_file(destination) == digest, "source-changed-during-copy")
            destination.chmod(0o555 if source.stat().st_mode & 0o111 else 0o444)
            previous = {"sha256": digest, "size": source.stat().st_size, "copied_paths": []}
            self.sources[str(source)] = previous
        previous["copied_paths"].append(key)
        self.files[key] = {"sha256": digest, "source": str(source)}
        if elf_dynamic(source):
            self.elf_sources.add(source)

    def copy_tree(self, source: Path, destination: Path, ancestors: frozenset[Path] = frozenset()) -> None:
        resolved = self.trusted(source)
        require(resolved not in ancestors, "cyclic-runtime-directory")
        allowed = False
        for root in self.tree_roots:
            if resolved == root or resolved.is_relative_to(root):
                parts = resolved.relative_to(root).parts
                allowed |= not any(part in EXCLUDED_NAMES or Path(part).suffix.casefold() in {".pth", ".egg-link", ".pyc"}
                                   for part in parts)
        require(allowed, "tree-symlink-escapes-approved-packages")
        if resolved.is_file():
            self.copy_file(resolved, destination)
            return
        require(resolved.is_dir(), "non-regular-runtime-source")
        destination.mkdir(parents=True, exist_ok=True)
        for item in sorted(resolved.iterdir()):
            if item.name in EXCLUDED_NAMES or item.suffix.casefold() in {".pth", ".egg-link", ".pyc"}:
                continue
            self.copy_tree(item, destination / item.name, ancestors | {resolved})

    def verify_sources(self) -> None:
        require(all(sha_file(Path(name)) == info["sha256"] for name, info in self.sources.items()),
                "source-changed-during-copy")


def system_library(path: Path) -> bool:
    return path.is_absolute() and ".." not in path.parts and any(path.is_relative_to(root) for root in SYSTEM_LIBRARY_ROOTS)


def copy_elf_closure(copier: Copier, search_dirs: tuple[Path, ...],
                     private_library_roots: tuple[Path, ...] = ()) -> list[dict[str, Any]]:
    private_library_roots = tuple(path.resolve(strict=True) for path in private_library_roots)
    inspected: set[Path] = set()
    edges = []
    while remaining := copier.elf_sources - inspected:
        binary = min(remaining)
        inspected.add(binary)
        for requested, logical_source in inspect_elf(binary, search_dirs):
            resolved = copier.trusted(logical_source)
            require(elf_dynamic(resolved), "dependency-is-not-elf")
            if system_library(logical_source):
                copier.copy_file(resolved, copier.destination / "elf" / logical_source.relative_to("/"))
            else:
                # Standalone CPython can keep libssl/libsqlite/etc beside
                # libpython. Follow only ldd's exact files in that directory;
                # it does not authorize other site-packages below the prefix.
                require(str(resolved) in copier.sources or resolved.parent in private_library_roots,
                        "unregistered-private-elf-library")
                copier.copy_file(resolved, copier.destination / "elf/lib" / resolved.name)
            if requested and requested.startswith("/"):
                require(system_library(Path(requested)), "absolute-private-elf-dependency")
                copier.copy_file(resolved, copier.destination / "elf" / requested[1:])
            elif requested:
                require(requested not in {".", ".."} and "/" not in requested, "invalid-elf-library-name")
                # Default loader search also handles a relocated libpython whose
                # original RUNPATH pointed at the operator's home or /opt prefix.
                copier.copy_file(resolved, copier.destination / "elf/lib" / requested)
            else:
                require(system_library(logical_source), "private-elf-loader")
            edges.append({"binary": str(binary), "requested": requested, "source": str(logical_source),
                          "resolved_source": str(resolved), "sha256": sha_file(resolved)})
    return edges


def prepare_runtime(interpreter: Path, destination: Path) -> dict[str, Any]:
    require(platform.system() == "Linux", "linux-runtime-preparation-required")
    root = canonical_path(destination, exists=False)
    require(not root.exists() and root.parent.is_dir(), "fresh-private-destination-required")
    info = probe_interpreter(interpreter)
    executable, stdlib = Path(info["executable"]), Path(info["stdlib"])
    prefix = Path(info["prefix"]).resolve(strict=True)
    require(not root.is_relative_to(prefix), "destination-inside-source-runtime")
    package_roots = [Path(item[key]) for item in info["packages"] for key in ("package", "dist_info")]
    package_libs = [Path(path) for item in info["packages"] for path in item["libs"]]
    source_roots = (executable, stdlib, *package_roots, *package_libs)
    require(all(not root.is_relative_to(path.resolve(strict=True)) for path in source_roots),
            "destination-inside-source-runtime")
    system_roots = tuple(path for path in SYSTEM_LIBRARY_ROOTS if path.is_dir())
    libdir = Path(info["libdir"]).resolve(strict=True) if info.get("libdir") else executable.parent.parent / "lib"
    require(libdir.is_dir(), "python-library-directory-missing")
    root.mkdir(mode=0o700)
    runtime, elf = root / "runtime", root / "elf"
    runtime.mkdir()
    elf.mkdir()
    copier = Copier(root, (*source_roots, libdir, *system_roots), tree_roots=source_roots)
    copier.copy_file(executable, runtime / "python")
    copier.copy_tree(stdlib, runtime / "lib/python3.11")
    package_payloads = []
    site = runtime / "lib/python3.11/site-packages"
    for item in info["packages"]:
        before = set(copier.files)
        for source in (Path(item["package"]), Path(item["dist_info"]), *(Path(path) for path in item["libs"])):
            copier.copy_tree(source, site / source.name)
        hashes = {name: copier.files[name]["sha256"] for name in set(copier.files) - before}
        package_payloads.append({**item, "payload_sha256": canonical_digest(hashes), "file_count": len(hashes)})
    # libpython may live in a trusted standalone-Python prefix rather than a system
    # loader path. Seed its exact configured library directory's ELF files only.
    for library in sorted(libdir.glob("libpython3.11*.so*")):
        copier.copy_file(library, elf / "lib" / library.name)
    search = tuple(dict.fromkeys((libdir, *package_libs, *(Path(item["package"]) for item in info["packages"]
                                                       if Path(item["package"]).is_dir()))))
    edges = copy_elf_closure(copier, search, (libdir,))
    copier.verify_sources()
    for payload in (runtime, elf):
        for directory, _, _ in os.walk(payload, topdown=False):
            Path(directory).chmod(0o555)
    mounts = [{"source": str(runtime), "target": "/runtime", "sha256": runtime_digest(runtime, (root,))}]
    mounts.extend({"source": str(child), "target": "/" + child.name, "sha256": runtime_digest(child, (root,))}
                  for child in sorted(elf.iterdir()))
    profile = {"profile_id": PROFILE_ID, "python_executable": "/runtime/python", "python_sha256": sha_file(runtime / "python"),
               "mounts": mounts, "allowed_roots": [str(root)], "bwrap_executable": "/usr/bin/bwrap"}
    profile_path = root / "profile.json"
    profile_path.write_text(json.dumps(profile, sort_keys=True, indent=2) + "\n")
    profile_path.chmod(0o400)
    manifest = {
        "schema_version": 1, "kind": "operator_research_runtime", "profile_id": PROFILE_ID,
        "created_at": datetime.now(UTC).isoformat(), "hostname": platform.node(),
        "python": {key: value for key, value in info.items() if key != "packages"},
        "requested_python": str(interpreter.absolute()), "packages": package_payloads,
        "source_files": copier.sources, "copied_files": copier.files, "elf_dependencies": edges,
        "script_sha256": sha_file(Path(__file__)), "profile_sha256": sha_file(profile_path),
        "payload_sha256": canonical_digest({name: item["sha256"] for name, item in copier.files.items()}),
        "qualification": "not-run; requires Linux import and synthetic parquet roundtrip in the sandbox",
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    manifest_path.chmod(0o400)
    return {"profile": str(profile_path), "profile_sha256": sha_file(profile_path),
            "manifest": str(manifest_path), "manifest_sha256": sha_file(manifest_path),
            "python_version": info["version"], "packages": {item["module"]: item["version"] for item in package_payloads}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", dest="interpreter", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = prepare_runtime(args.interpreter, args.destination)
    except RuntimePreparationError as exc:
        raise SystemExit(f"offline runtime preparation failed: {exc}") from None
    except (OSError, ValueError):
        # Source metadata may contain private URLs; do not echo subprocess output.
        raise SystemExit("offline runtime preparation failed; inspect the approved source and fresh destination") from None
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
