"""Operator-profiled Linux bubblewrap execution; no host or unsandboxed fallback.

Only the trusted executor constructs these specifications. Models cannot choose a
runtime, mount source, executable, or environment. Process recovery/deduplication
belongs to the existing worker; a launch receipt is not an exactly-once guarantee.
"""

from __future__ import annotations

import math
import os
import platform
import signal
import stat
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .workspace import (
    COMMIT_PATTERN,
    DIGEST_PATTERN,
    WorkspaceError,
    canonical_digest,
    canonical_path,
    content_digest,
    safe_relative_path,
    snapshot_files,
)

WORKER_HOSTNAME = "DESKTOP-5T00NAF"
PRIVATE_GUEST_ROOTS = ("/code", "/inputs", "/output", "/home", "/tmp", "/proc", "/dev", "/sys", "/run")


class SandboxError(ValueError):
    """Sanitized policy or launch failure; child output stays in job-local logs."""


@dataclass(frozen=True)
class RuntimeMount:
    source: Path
    target: str
    sha256: str


@dataclass(frozen=True)
class RuntimeProfile:
    profile_id: str
    python_executable: str
    python_sha256: str
    mounts: tuple[RuntimeMount, ...]
    allowed_roots: tuple[Path, ...]
    bwrap_executable: Path = Path("/usr/bin/bwrap")


@dataclass(frozen=True)
class InputMount:
    source: Path
    target: str
    sha256: str


@dataclass(frozen=True)
class SandboxSpec:
    profile: RuntimeProfile
    code_root: Path
    code_commit: str
    code_files: dict[str, str]  # Operator-selected committed closure, not the whole repository.
    input_mounts: tuple[InputMount, ...]
    allowed_input_roots: tuple[Path, ...]
    allowed_job_root: Path
    output_dir: Path
    entrypoint: str
    argv: tuple[str, ...] = ()
    timeout_seconds: float = 300
    fixture_only: bool = False


@dataclass(frozen=True)
class SandboxReceipt:
    schema_version: int
    spec_digest: str
    code_commit: str
    profile_id: str
    pid: int
    exit_code: int
    timed_out: bool
    started_at: str
    completed_at: str
    stdout_path: Path
    stderr_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "stdout_path": str(self.stdout_path), "stderr_path": str(self.stderr_path)}


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise SandboxError(code)


def _path(path: Path, *, exists: bool = True) -> Path:
    try:
        return canonical_path(path, exists=exists)
    except (WorkspaceError, OSError):
        raise SandboxError("unsafe-mount-source") from None


def _sensitive_path(path: Path | PurePosixPath) -> bool:
    return any(part.casefold() in {".aws", ".ssh", ".gnupg", ".kube", "credentials", "sealed", "forward"}
               for part in path.parts) or path.name.casefold() == ".env"


def _roots(roots: tuple[Path, ...]) -> tuple[Path, ...]:
    resolved = tuple(_path(root) for root in roots)
    forbidden = {"/", "/home", "/root", "/Users", "/etc", "/proc", "/sys", "/dev", "/run", str(Path.home())}
    _require(bool(resolved) and all(root.is_dir() and str(root) not in forbidden
                                    and not _sensitive_path(root) for root in resolved),
             "overbroad-mount-root")
    return resolved


def _approved(path: Path, roots: tuple[Path, ...]) -> Path:
    source = _path(path)
    _require(any(source.is_relative_to(root) for root in roots), "mount-outside-approved-root")
    return source


def runtime_digest(path: Path, allowed_roots: tuple[Path, ...]) -> str:
    """Hash an operator-reviewed runtime file/tree, including modes and symlink text.

    Runtime-internal symlinks are permitted only if they resolve inside approved
    runtime roots. Code and input mounts have the stricter no-symlinks contract.
    """
    roots = _roots(allowed_roots)
    source = _approved(path, roots)
    if source.is_file():
        return content_digest(source.read_bytes())
    _require(source.is_dir(), "non-regular-runtime")
    records = []
    for directory, dirs, files in os.walk(source, followlinks=False):
        for name in sorted([*dirs, *files]):
            item = Path(directory) / name
            mode = item.lstat().st_mode
            relative = item.relative_to(source).as_posix()
            if stat.S_ISLNK(mode):
                try:
                    target = item.resolve(strict=True)
                except (OSError, RuntimeError):
                    raise SandboxError("runtime-symlink-escape") from None
                _require(any(target.is_relative_to(root) for root in roots), "runtime-symlink-escape")
                records.append([relative, "symlink", os.readlink(item)])
            elif stat.S_ISREG(mode):
                records.append([relative, "file", stat.S_IMODE(mode), content_digest(item.read_bytes())])
            elif stat.S_ISDIR(mode):
                records.append([relative, "directory", stat.S_IMODE(mode)])
            else:
                raise SandboxError("non-regular-runtime")
    return canonical_digest(sorted(records))


def _guest_absolute(value: str) -> str:
    _require(isinstance(value, str) and value.startswith("/") and value != "/", "invalid-runtime-target")
    try:
        safe_relative_path(value[1:])
    except WorkspaceError:
        raise SandboxError("invalid-runtime-target") from None
    _require(str(PurePosixPath(value)) == value, "invalid-runtime-target")
    return value


def _overlaps(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _validate(spec: SandboxSpec) -> tuple[list[tuple[Path, str]], dict[str, Any]]:
    _require(isinstance(spec, SandboxSpec), "untyped-sandbox-spec")
    _require(spec.fixture_only or platform.system() == "Linux", "linux-required")
    _require(spec.fixture_only or platform.node() == WORKER_HOSTNAME, "registered-worker-required")
    _require(type(spec.fixture_only) is bool and type(spec.timeout_seconds) in (float, int)
             and math.isfinite(spec.timeout_seconds) and spec.timeout_seconds > 0, "invalid-launch-limit")
    _require(isinstance(spec.argv, tuple) and all(isinstance(arg, str) and "\0" not in arg for arg in spec.argv),
             "invalid-entrypoint-arguments")
    _require(bool(COMMIT_PATTERN.fullmatch(spec.code_commit)), "invalid-code-commit")
    job_root = _roots((spec.allowed_job_root,))[0]
    code = _approved(spec.code_root, (job_root,))
    output = _approved(spec.output_dir, (job_root,))
    _require(code.is_dir() and output.is_dir() and not _overlaps(str(code), str(output)), "invalid-job-mounts")
    try:
        _require(isinstance(spec.code_files, dict) and bool(spec.code_files), "code-manifest-empty")
        entrypoint = safe_relative_path(spec.entrypoint)
        _require(entrypoint.endswith(".py") and entrypoint in spec.code_files, "entrypoint-not-committed")
        committed_files = snapshot_files(code, spec.code_commit)
        _require(all(name in committed_files and committed_files[name] == digest
                     for name, digest in spec.code_files.items()), "code-manifest-mismatch")
    except WorkspaceError:
        raise SandboxError("code-identity-invalid") from None
    profile = spec.profile
    _require(isinstance(profile, RuntimeProfile) and bool(profile.profile_id), "unregistered-runtime")
    runtime_roots = _roots(profile.allowed_roots)
    mounts: list[tuple[Path, str]] = []
    runtime_targets: list[str] = []
    runtime_identity = []
    for mount in profile.mounts:
        _require(isinstance(mount, RuntimeMount), "untyped-runtime-mount")
        target = _guest_absolute(mount.target)
        _require(not any(_overlaps(target, reserved) for reserved in PRIVATE_GUEST_ROOTS), "reserved-runtime-target")
        _require(not any(_overlaps(target, other) for other in runtime_targets), "overlapping-runtime-mount")
        source = _approved(mount.source, runtime_roots)
        _require(not _overlaps(str(source), str(output)), "writable-readonly-alias")
        _require(bool(DIGEST_PATTERN.fullmatch(mount.sha256))
                 and runtime_digest(source, runtime_roots) == mount.sha256, "runtime-digest-mismatch")
        runtime_targets.append(target)
        mounts.append((source, target))
        runtime_identity.append({"source": str(source), "target": target, "sha256": mount.sha256})
    python = _guest_absolute(profile.python_executable)
    python_sources = [source / PurePosixPath(python).relative_to(target) if source.is_dir() else source
                      for source, target in mounts if python == target or (source.is_dir() and python.startswith(target + "/"))]
    _require(len(python_sources) == 1, "python-outside-runtime")
    try:
        python_source = python_sources[0].resolve(strict=True)
    except (OSError, RuntimeError):
        raise SandboxError("python-outside-runtime") from None
    _require(any(python_source.is_relative_to(root) for root in runtime_roots)
             and python_source.is_file() and os.access(python_source, os.X_OK), "invalid-python-executable")
    _require(bool(DIGEST_PATTERN.fullmatch(profile.python_sha256))
             and content_digest(python_source.read_bytes()) == profile.python_sha256, "python-digest-mismatch")
    for name, digest in sorted(spec.code_files.items()):
        _require(not _sensitive_path(PurePosixPath(name)), "sensitive-code-path")
        _require(bool(DIGEST_PATTERN.fullmatch(digest)), "invalid-code-digest")
        source = _path(code / name)
        mounts.append((source, "/code/" + name))
    input_roots = _roots(spec.allowed_input_roots) if spec.input_mounts else ()
    input_targets: list[str] = []
    input_identity = []
    for mount in spec.input_mounts:
        _require(isinstance(mount, InputMount), "untyped-input-mount")
        try:
            name = safe_relative_path(mount.target)
        except WorkspaceError:
            raise SandboxError("invalid-input-target") from None
        _require(not any(_overlaps(name, other) for other in input_targets), "overlapping-input-mount")
        source = _approved(mount.source, input_roots)
        _require(source.is_file(), "input-must-be-a-file")
        _require(not _sensitive_path(source), "sensitive-input-path")
        _require(not _overlaps(str(source), str(output)), "writable-readonly-alias")
        _require(bool(DIGEST_PATTERN.fullmatch(mount.sha256))
                 and content_digest(source.read_bytes()) == mount.sha256, "input-digest-mismatch")
        input_targets.append(name)
        mounts.append((source, "/inputs/" + name))
        input_identity.append({"source": str(source), "target": name, "sha256": mount.sha256})
    identity = {
        "schema_version": 1, "profile_id": profile.profile_id, "python": python,
        "python_sha256": profile.python_sha256, "runtime": runtime_identity,
        "code_commit": spec.code_commit, "code_files": spec.code_files, "inputs": input_identity,
        "output_dir": str(output), "entrypoint": entrypoint, "argv": list(spec.argv),
        "timeout_seconds": spec.timeout_seconds, "fixture_only": spec.fixture_only,
    }
    return mounts, identity


def _command(spec: SandboxSpec, mounts: list[tuple[Path, str]]) -> tuple[str, ...]:
    command = [str(_path(spec.profile.bwrap_executable)), "--die-with-parent", "--new-session", "--unshare-all",
               "--cap-drop", "ALL", "--clearenv", "--tmpfs", "/", "--proc", "/proc", "--dev", "/dev"]
    directories = {"/code", "/inputs", "/output", "/home", "/home/sandbox", "/tmp"}
    for _source, target in mounts:
        directories.update(str(parent) for parent in PurePosixPath(target).parents if str(parent) != "/")
    for directory in sorted(directories, key=lambda value: (value.count("/"), value)):
        command.extend(["--dir", directory])
    for source, target in mounts:
        command.extend(["--ro-bind", str(source), target])
    command.extend(["--bind", str(spec.output_dir.absolute()), "/output", "--chdir", "/code",
                    "--setenv", "HOME", "/home/sandbox", "--setenv", "PATH", "/usr/bin:/bin",
                    "--setenv", "LANG", "C.UTF-8", "--setenv", "PYTHONNOUSERSITE", "1",
                    "--setenv", "PYTHONDONTWRITEBYTECODE", "1", "--setenv", "PYTHONHASHSEED", "0",
                    "--", spec.profile.python_executable, "-E", "-s", "-B", "/code/" + spec.entrypoint, *spec.argv])
    return tuple(command)


def build_command(spec: SandboxSpec) -> tuple[str, ...]:
    """Validate identities and construct argv only. Never creates a process or output."""
    mounts, _ = _validate(spec)
    return _command(spec, mounts)


def run_sandbox(spec: SandboxSpec) -> SandboxReceipt:
    """Launch once, bound its lifetime, and return a factual process exit receipt."""
    _require(platform.system() == "Linux", "linux-execution-required")
    mounts, identity = _validate(spec)
    command = _command(spec, mounts)
    output = _path(spec.output_dir)
    _require(not any(output.iterdir()), "output-directory-not-empty")
    stdout_path, stderr_path = output / "stdout.log", output / "stderr.log"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    started_at = datetime.now(UTC).isoformat()
    timed_out = False
    try:
        with os.fdopen(os.open(stdout_path, flags, 0o600), "wb") as stdout:
            with os.fdopen(os.open(stderr_path, flags, 0o600), "wb") as stderr:
                process = subprocess.Popen(command, shell=False, env={}, cwd=output, stdin=subprocess.DEVNULL,
                                           stdout=stdout, stderr=stderr, close_fds=True, start_new_session=True)
                try:
                    exit_code = process.wait(timeout=spec.timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    exit_code = process.wait()
    except OSError:
        raise SandboxError("sandbox-launch-unavailable") from None
    return SandboxReceipt(1, canonical_digest(identity), spec.code_commit, spec.profile.profile_id,
                          process.pid, exit_code, timed_out, started_at, datetime.now(UTC).isoformat(),
                          stdout_path, stderr_path)


def profile_from_dict(value: dict[str, Any]) -> RuntimeProfile:
    """Load an operator-owned profile, never a model proposal; unknown fields fail."""
    try:
        _require(set(value) == {"profile_id", "python_executable", "python_sha256", "mounts",
                               "allowed_roots", "bwrap_executable"}, "invalid-runtime-profile")
        _require(all(set(item) == {"source", "target", "sha256"} for item in value["mounts"]),
                 "invalid-runtime-profile")
        return RuntimeProfile(
            profile_id=value["profile_id"], python_executable=value["python_executable"],
            python_sha256=value["python_sha256"],
            mounts=tuple(RuntimeMount(Path(item["source"]), item["target"], item["sha256"]) for item in value["mounts"]),
            allowed_roots=tuple(Path(root) for root in value["allowed_roots"]),
            bwrap_executable=Path(value["bwrap_executable"]),
        )
    except (KeyError, TypeError):
        raise SandboxError("invalid-runtime-profile") from None
