"""Prepare committed research code without importing or executing proposed Python.

Input authority is a caller-approved, self-contained local Git bundle and its digest.
No source checkout, source Git configuration, network remote, or credentials are used.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
MAX_PATCH_BYTES = 16 * 1024 * 1024
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class WorkspaceError(ValueError):
    """Stable public failure code; command output and proposed source remain local."""


@dataclass(frozen=True)
class TextPatch:
    path: str
    expected_text: str | None
    replacement_text: str


@dataclass(frozen=True)
class PreparedWorkspace:
    worktree: Path
    commit: str
    bundle_path: Path
    bundle_sha256: str
    manifest: dict[str, Any]
    manifest_path: Path
    manifest_sha256: str


def content_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def canonical_digest(value: Any) -> str:
    return content_digest(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode())


def require(condition: bool, code: str) -> None:
    if not condition:
        raise WorkspaceError(code)


def safe_relative_path(value: str) -> str:
    require(isinstance(value, str), "invalid-relative-path")
    path = PurePosixPath(value)
    require(bool(value) and value != "." and not path.is_absolute() and str(path) == value
            and ".." not in path.parts and "\\" not in value and ":" not in value
            and all(32 <= ord(char) != 127 for char in value), "invalid-relative-path")
    require(not any(part.casefold() == ".git" for part in path.parts), "git-metadata-path")
    return value


def canonical_path(path: Path, *, exists: bool = True) -> Path:
    """Reject symlinks in every existing component, including mount/workspace parents."""
    require(isinstance(path, Path), "invalid-local-path")
    absolute = path.absolute()
    require(".." not in absolute.parts, "invalid-local-path")
    for component in (absolute, *absolute.parents):
        require(not component.is_symlink(), "symlink-local-path")
    if exists:
        require(absolute.exists(), "missing-local-path")
    require(absolute.resolve(strict=exists) == absolute, "noncanonical-local-path")
    return absolute


def _git(directory: Path, *arguments: str) -> bytes:
    # Fresh repositories also use explicit overrides: no inherited configuration,
    # hooks, replacements, lazy fetch, credentials, signing, filters or pager.
    environment = {
        "PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/usr/bin/false", "SSH_ASKPASS": "/usr/bin/false",
        "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1", "GIT_LITERAL_PATHSPECS": "1",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_AUTHOR_NAME": "Research Builder",
        "GIT_AUTHOR_EMAIL": "research-builder@localhost", "GIT_COMMITTER_NAME": "Research Builder",
        "GIT_COMMITTER_EMAIL": "research-builder@localhost",
    }
    command = [
        "/usr/bin/git", "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false",
        "-c", f"core.attributesFile={os.devnull}", "-c", "core.autocrlf=false",
        "-c", "credential.helper=", "-c", "commit.gpgSign=false", "-c", "tag.gpgSign=false",
        "-c", "protocol.allow=never", "-c", "protocol.file.allow=always",
        "-c", "submodule.recurse=false", "-c", "fetch.recurseSubmodules=false",
        "-c", "core.quotePath=false", "-c", "core.pager=cat", "-C", str(directory), *arguments,
    ]
    try:
        result = subprocess.run(command, env=environment, stdin=subprocess.DEVNULL, capture_output=True,
                                check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise WorkspaceError("git-command-unavailable") from None
    require(result.returncode == 0, "git-command-failed")
    return result.stdout


def _tree(worktree: Path, commit: str) -> dict[str, tuple[str, str]]:
    require(bool(COMMIT_PATTERN.fullmatch(commit)), "invalid-base-commit")
    entries = {}
    try:
        for entry in _git(worktree, "ls-tree", "-r", "-z", "--full-tree", commit).split(b"\0"):
            if not entry:
                continue
            metadata, raw_name = entry.split(b"\t", 1)
            mode, kind, blob = metadata.decode("ascii").split()
            name = safe_relative_path(raw_name.decode("utf-8"))
            require(mode in ("100644", "100755") and kind == "blob", "unsupported-git-tree-entry")
            require(PurePosixPath(name).name.casefold() != ".gitmodules", "submodule-metadata")
            entries[name] = (mode, blob)
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, WorkspaceError):
            raise
        raise WorkspaceError("invalid-git-tree") from None
    require(bool(entries), "empty-code-snapshot")
    return entries


def snapshot_files(worktree: Path, commit: str) -> dict[str, str]:
    """Verify exact committed tracked bytes and return the consumer's SHA-256 map."""
    root = canonical_path(worktree)
    require(_git(root, "rev-parse", "HEAD").decode().strip() == commit, "worktree-commit-mismatch")
    result = {}
    for name, (mode, blob) in _tree(root, commit).items():
        path = canonical_path(root / name)
        require(path.is_file(), "non-regular-code-file")
        data = path.read_bytes()
        expected_blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        require(expected_blob == blob, "worktree-content-mismatch")
        require(bool(path.stat().st_mode & stat.S_IXUSR) == (mode == "100755"), "worktree-mode-mismatch")
        result[name] = content_digest(data)
    require(not _git(root, "status", "--porcelain", "--untracked-files=all"), "worktree-not-clean")
    return result


def _within(name: str, scopes: tuple[str, ...]) -> bool:
    return any(name == scope or name.startswith(scope + "/") for scope in scopes)


def _protected(name: str, protected_paths: tuple[str, ...]) -> bool:
    parts = PurePosixPath(name).parts
    reserved = ("prereg", "objective", "audit", "ledger", "brief")
    return (
        _within(name, protected_paths)
        or any(part.casefold().startswith(".git") for part in parts)
        or any(part.casefold() in {"docs", "audits", "sealed", "reference"} for part in parts)
        or PurePosixPath(name).name.casefold() in {"agents.md", "claude.md", "search.jsonl"}
        or PurePosixPath(name).name.casefold().startswith(reserved)
    )


def prepare_workspace(
    source_snapshot: Path,
    expected_snapshot_sha256: str,
    base_commit: str,
    destination: Path,
    allowed_paths: tuple[str, ...],
    protected_paths: tuple[str, ...],
    patches: tuple[TextPatch, ...],
) -> PreparedWorkspace:
    """Apply exact *whole-file* UTF-8 replacements, syntax-check, commit and bundle.

    ``expected_text=None`` creates a previously absent file. Existing files must
    match the complete expected text. Empty patches only materialize/verify a bundle
    for a consumer; they do not manufacture an additional commit.
    """
    require(bool(COMMIT_PATTERN.fullmatch(base_commit)), "invalid-base-commit")
    require(bool(DIGEST_PATTERN.fullmatch(expected_snapshot_sha256)), "invalid-snapshot-digest")
    source = canonical_path(source_snapshot)
    require(source.is_file() and source.stat().st_size <= MAX_SNAPSHOT_BYTES, "invalid-source-snapshot")
    data = source.read_bytes()
    require(len(data) <= MAX_SNAPSHOT_BYTES and content_digest(data) == expected_snapshot_sha256,
            "snapshot-digest-mismatch")
    require(data.startswith((b"# v2 git bundle\n", b"# v3 git bundle\n")), "not-a-local-git-bundle")
    allowed = tuple(safe_relative_path(scope.rstrip("/")) for scope in allowed_paths)
    protected = tuple(safe_relative_path(scope.rstrip("/")) for scope in protected_paths)
    names = []
    patch_bytes = 0
    for patch in patches:
        require(isinstance(patch, TextPatch), "untyped-patch")
        name = safe_relative_path(patch.path)
        require(name not in names, "duplicate-patch-path")
        require(_within(name, allowed) and not _protected(name, protected), "patch-outside-approved-scope")
        require(patch.expected_text is None or isinstance(patch.expected_text, str), "invalid-expected-text")
        require(isinstance(patch.replacement_text, str), "invalid-replacement-text")
        try:
            patch_bytes += len((patch.expected_text or "").encode()) + len(patch.replacement_text.encode())
        except UnicodeError:
            raise WorkspaceError("invalid-patch-encoding") from None
        require(patch_bytes <= MAX_PATCH_BYTES, "patch-too-large")
        names.append(name)
    target = canonical_path(destination, exists=False)
    require(not target.exists(), "workspace-already-exists")
    require(target.parent.is_dir(), "workspace-parent-missing")
    target.mkdir(mode=0o700)
    copied_snapshot = target / "source.bundle"
    copied_snapshot.write_bytes(data)
    copied_snapshot.chmod(0o400)
    repository = target / "repository.git"
    _git(target, "init", "--bare", "--template=", str(repository))
    # Verification in an empty object database rejects incremental bundles. Only
    # this local bundle protocol is allowed, and only the exact requested commit
    # is imported; source refs/replace and alternate object stores are not inherited.
    _git(repository, "bundle", "verify", str(copied_snapshot))
    _git(repository, "fetch", "--no-tags", "--no-write-fetch-head", "--no-recurse-submodules",
         str(copied_snapshot), f"{base_commit}:refs/heads/snapshot")
    require(_git(repository, "cat-file", "-t", base_commit).strip() == b"commit", "base-is-not-commit")
    _tree(repository, base_commit)
    worktree = target / "code"
    _git(repository, "worktree", "add", "--detach", str(worktree), base_commit)
    patch_manifest = []
    for patch in patches:
        path = canonical_path(worktree / patch.path, exists=False)
        require(path.is_relative_to(worktree), "patch-escapes-worktree")
        before = path.read_bytes() if path.is_file() else None
        require((before is None and patch.expected_text is None and not path.exists())
                or (before is not None and patch.expected_text is not None
                    and before == patch.expected_text.encode()), "patch-preimage-mismatch")
        after = patch.replacement_text.encode()
        require(before != after, "empty-patch")
        if path.suffix == ".py":
            try:
                ast.parse(patch.replacement_text, filename=patch.path)
            except (SyntaxError, ValueError, RecursionError):
                raise WorkspaceError("python-syntax-invalid") from None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(after)
        patch_manifest.append({"path": patch.path, "before_sha256": content_digest(before) if before is not None else None,
                               "after_sha256": content_digest(after)})
    if names:
        _git(worktree, "add", "--", *names)
        actual = {name.decode() for name in _git(worktree, "diff", "--cached", "--name-only", "-z",
                                               "--no-ext-diff", "--no-textconv", base_commit, "--").split(b"\0") if name}
        require(actual == set(names), "changed-paths-mismatch")
        _git(worktree, "diff", "--cached", "--check")
        _git(worktree, "commit", "--no-gpg-sign", "-m", "Apply approved research patch")
    commit = _git(worktree, "rev-parse", "HEAD").decode().strip()
    files = snapshot_files(worktree, commit)
    _git(repository, "update-ref", "refs/heads/prepared", commit)
    bundle = target / "prepared.bundle"
    _git(repository, "bundle", "create", str(bundle), "refs/heads/prepared")
    digest = content_digest(bundle.read_bytes())
    bundle.chmod(0o400)
    manifest = {
        "schema_version": 1, "base_commit": base_commit, "commit": commit,
        "source_snapshot_sha256": expected_snapshot_sha256, "bundle_sha256": digest,
        "files": files, "patches": patch_manifest,
    }
    manifest_path = target / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n")
    manifest_path.chmod(0o400)
    return PreparedWorkspace(worktree, commit, bundle, digest, manifest, manifest_path, content_digest(manifest_path.read_bytes()))
