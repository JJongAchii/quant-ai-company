"""Prepare immutable company release/config pairs and atomically select one.

This helper does not SSH, restart services, fetch remote code or remove releases.
Already-launched jobs keep their stored execution-config and previous checkout.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .worker import WorkerConfig, atomic_json, exclusive_lock, read_json, sha_file
from .workspace import COMMIT_PATTERN, WorkspaceError, canonical_path, prepare_workspace, snapshot_files


class ReleaseError(ValueError):
    """Stable public release preflight code, with no credentials or raw output."""


@dataclass(frozen=True)
class PreparedRelease:
    commit: str
    directory: Path
    config_path: Path
    config_sha256: str
    code_files: dict[str, str]


def verify_company_pin(config: WorkerConfig) -> None:
    """Check exact HEAD and clean bytes before attempting any detached launch."""
    try:
        repo = canonical_path(config.company_repo)
        snapshot_files(repo, config.company_commit)
        for name in ("src/quant_company/research/worker.py", "src/quant_company/research/executor.py"):
            path = repo / name
            if not path.is_file():
                raise ReleaseError("release-entrypoint-missing")
    except (OSError, WorkspaceError):
        raise ReleaseError("company-checkout-pin-mismatch") from None


def prepare_release(*, source_snapshot: Path, snapshot_sha256: str, commit: str,
                    release_root: Path, config: WorkerConfig) -> PreparedRelease:
    """Materialize a local Git bundle once, then generate the matching config."""
    try:
        root = canonical_path(release_root)
        if not root.is_dir():
            raise ReleaseError("release-root-not-directory")
        directory = root / commit
        if directory.exists():
            raise ReleaseError("release-already-exists")
        prepared = prepare_workspace(source_snapshot, snapshot_sha256, commit, directory, (), (), ())
        pinned = WorkerConfig.model_validate({**config.model_dump(mode="json"),
                                             "company_repo": str(prepared.worktree), "company_commit": commit,
                                             "release_registry_file": str(config.release_registry_file or root / "registry.json")})
        # Relative paths would change meaning when the active configuration moves.
        for name in ("token_file", "state_dir", "repo_source", "input_source", "evidence_repo",
                     "research_python", "company_repo"):
            if not getattr(pinned, name).is_absolute():
                raise ReleaseError("release-config-path-must-be-absolute")
        if not pinned.release_registry_file.is_absolute():
            raise ReleaseError("release-registry-path-must-be-absolute")
        if any(not path.is_absolute() for path in pinned.adaptive_profiles.values()):
            raise ReleaseError("release-profile-path-must-be-absolute")
        verify_company_pin(pinned)
        for name in prepared.manifest["files"]:
            if name.endswith(".py"):
                try:
                    ast.parse((prepared.worktree / name).read_text(), filename=name)
                except (SyntaxError, UnicodeError):
                    raise ReleaseError("release-python-syntax-invalid") from None
        # Dependencies are supplied by the existing company interpreter. Import
        # only trusted, pinned company code, with no Slack/token/client execution.
        command = [sys.executable, "-I", "-B", "-c",
                   "import sys;sys.path.insert(0,sys.argv[1]);"
                   "from quant_company.research.worker import WorkerConfig;"
                   "from quant_company.research.executor import execute_child;"
                   "assert callable(execute_child)", str(prepared.worktree / "src")]
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30,
                                env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent",
                                     "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"})
        if result.returncode:
            raise ReleaseError("release-import-preflight-failed")
        path = directory / "worker-config.json"
        atomic_json(path, pinned.model_dump(mode="json"))
        # The persisted representation, rather than a claim, must round-trip.
        restored = WorkerConfig.from_file(path)
        if restored != pinned:
            raise ReleaseError("release-config-roundtrip-mismatch")
        verify_company_pin(restored)
        receipt = {
            "schema_version": 1, "commit": commit, "source_snapshot_sha256": snapshot_sha256,
            "config_sha256": sha_file(path), "code_files": prepared.manifest["files"],
            "preflight_exit_code": result.returncode,
        }
        atomic_json(directory / "release.json", receipt)
        path.chmod(0o400)
        return PreparedRelease(commit, directory, path, sha_file(path), prepared.manifest["files"])
    except (WorkspaceError, OSError, subprocess.SubprocessError):
        raise ReleaseError("release-preparation-failed") from None


def activate_release(release: PreparedRelease, *, active_config: Path) -> WorkerConfig:
    """Atomically replace only the pointer/config after verifying the prepared pair."""
    try:
        path = canonical_path(release.config_path)
        receipt = read_json(release.directory / "release.json")
        if (sha_file(path) != release.config_sha256 or receipt["config_sha256"] != release.config_sha256
                or receipt["commit"] != release.commit or receipt["code_files"] != release.code_files):
            raise ReleaseError("prepared-release-identity-mismatch")
        config = WorkerConfig.from_file(path)
        if config.company_commit != release.commit:
            raise ReleaseError("prepared-release-pin-mismatch")
        verify_company_pin(config)
        if snapshot_files(config.company_repo, config.company_commit) != release.code_files:
            raise ReleaseError("prepared-release-files-mismatch")
        target = canonical_path(active_config, exists=False)
        if target.is_relative_to(release.directory) or not target.parent.is_dir():
            raise ReleaseError("invalid-active-config-path")
        registry = canonical_path(config.release_registry_file, exists=False)
        if registry.is_relative_to(release.directory) or registry == target or not registry.parent.is_dir():
            raise ReleaseError("invalid-release-registry-path")
        with exclusive_lock(registry.with_name(registry.name + ".lock")):
            refs = _read_registry(registry)
            previous = refs.get(release.commit)
            if previous is not None and previous != str(path):
                raise ReleaseError("release-pin-already-registered")
            refs[release.commit] = str(path)
            # A crash here can add an unused retained release, never leave an
            # active config referring to an unregistered release.
            atomic_json(registry, {"schema_version": 1, "releases": refs})
            if target.exists():
                atomic_json(target.with_name(target.name + ".previous"), read_json(target))
            atomic_json(target, config.model_dump(mode="json"))
        return config
    except (WorkspaceError, OSError, KeyError):
        raise ReleaseError("release-activation-failed") from None


def _read_registry(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    value = read_json(path)
    if set(value) != {"schema_version", "releases"} or value["schema_version"] != 1:
        raise ReleaseError("invalid-release-registry")
    refs = value["releases"]
    if not isinstance(refs, dict) or any(
        not isinstance(commit, str) or not COMMIT_PATTERN.fullmatch(commit)
        or not isinstance(location, str) or not Path(location).is_absolute()
        for commit, location in refs.items()
    ):
        raise ReleaseError("invalid-release-registry")
    return dict(refs)


def _same_transport(current: WorkerConfig, retained: WorkerConfig) -> None:
    if any(getattr(current, name) != getattr(retained, name)
           for name in ("api_url", "worker_id", "token_file", "state_dir")):
        raise ReleaseError("retained-release-authority-mismatch")


def resolve_release_config(current: WorkerConfig, commit: str) -> WorkerConfig:
    """Resolve only an exact operator-registered release, never search or fetch."""
    if current.company_commit == commit:
        verify_company_pin(current)
        return current
    if current.release_registry_file is None:
        raise ReleaseError("retained-release-not-registered")
    try:
        registry = canonical_path(current.release_registry_file)
        location = _read_registry(registry).get(commit)
        if location is None:
            raise ReleaseError("retained-release-not-registered")
        path = canonical_path(Path(location))
        config = WorkerConfig.from_file(path)
        if config.company_commit != commit:
            raise ReleaseError("retained-release-pin-mismatch")
        _same_transport(current, config)
        verify_company_pin(config)
        receipt = read_json(path.parent / "release.json")
        if (receipt["commit"] != commit or receipt["config_sha256"] != sha_file(path)
                or receipt["code_files"] != snapshot_files(config.company_repo, commit)):
            raise ReleaseError("retained-release-receipt-mismatch")
        return config
    except (WorkspaceError, OSError, KeyError, TypeError):
        raise ReleaseError("retained-release-unavailable") from None


def rollback_release(*, active_config: Path) -> WorkerConfig:
    """Select the previous verified config. Service restart remains an operator action."""
    try:
        active = canonical_path(active_config)
        previous_path = canonical_path(active.with_name(active.name + ".previous"))
        current = WorkerConfig.from_file(active)
        previous = WorkerConfig.from_file(previous_path)
        _same_transport(current, previous)
        restored = resolve_release_config(current, previous.company_commit)
        if restored != previous:
            raise ReleaseError("previous-config-differs-from-retained-release")
        registry = canonical_path(current.release_registry_file)
        with exclusive_lock(registry.with_name(registry.name + ".lock")):
            # Detect an intervening operator swap instead of rolling it back.
            if WorkerConfig.from_file(active) != current or WorkerConfig.from_file(previous_path) != previous:
                raise ReleaseError("active-config-changed-before-rollback")
            atomic_json(active, previous.model_dump(mode="json"))
            atomic_json(previous_path, current.model_dump(mode="json"))
        return previous
    except (WorkspaceError, OSError, KeyError, TypeError):
        raise ReleaseError("release-rollback-failed") from None
