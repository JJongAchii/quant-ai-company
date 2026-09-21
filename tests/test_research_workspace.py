"""Actual temporary Git repositories, synthetic code/data only; no economic run."""

import json
import os
import subprocess

import pytest

from quant_company.research.workspace import (
    TextPatch,
    WorkspaceError,
    content_digest,
    prepare_workspace,
    snapshot_files,
)


def git(repo, *arguments):
    return subprocess.check_output(
        ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false", "-C", str(repo), *arguments],
        env={"PATH": "/usr/bin:/bin", "HOME": str(repo), "GIT_CONFIG_NOSYSTEM": "1",
             "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@localhost",
             "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@localhost"},
        stderr=subprocess.PIPE,
    ).decode().strip()


def make_snapshot(tmp_path, files=None):
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init", "-b", "main", "--template=")
    for name, content in (files or {"src/entry.py": "VALUE = 1\n", "src/evaluator.py": "CRITERIA = 'frozen'\n"}).items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Synthetic approved base")
    commit = git(repo, "rev-parse", "HEAD")
    bundle = tmp_path / "source.bundle"
    git(repo, "bundle", "create", str(bundle), "HEAD")
    return repo, bundle, commit


def prepare(tmp_path, bundle, commit, patches=()):
    return prepare_workspace(bundle, content_digest(bundle.read_bytes()), commit, tmp_path / "job",
                             ("src",), ("src/evaluator.py",), tuple(patches))


def test_git_producer_bundle_patch_commit_consumer_qualification(tmp_path):
    repo, bundle, base = make_snapshot(tmp_path)
    marker = tmp_path / "generated-code-must-not-execute"
    replacement = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\nVALUE = 2\n"
    original_branch = git(repo, "symbolic-ref", "HEAD")
    prepared = prepare(tmp_path, bundle, base, [TextPatch("src/entry.py", "VALUE = 1\n", replacement),
                                                TextPatch("src/new.py", None, "HELPER = 3\n")])
    assert not marker.exists()
    assert prepared.commit != base and git(prepared.worktree, "rev-parse", "HEAD") == prepared.commit
    assert git(prepared.worktree, "branch", "--show-current") == ""
    assert git(repo, "symbolic-ref", "HEAD") == original_branch == "refs/heads/main"
    assert git(repo, "rev-parse", "HEAD") == base
    assert (repo / "src/entry.py").read_text() == "VALUE = 1\n"
    assert prepared.manifest["files"] == snapshot_files(prepared.worktree, prepared.commit)
    assert prepared.bundle_sha256 == content_digest(prepared.bundle_path.read_bytes())
    assert prepared.manifest_sha256 == content_digest(prepared.manifest_path.read_bytes())
    assert json.loads(prepared.manifest_path.read_text()) == prepared.manifest
    assert prepared.bundle_path.stat().st_mode & 0o222 == 0
    assert prepared.manifest_path.stat().st_mode & 0o222 == 0
    assert not (prepared.worktree.parent / "repository.git/objects/info/alternates").exists()
    consumer = prepare_workspace(prepared.bundle_path, prepared.bundle_sha256, prepared.commit,
                                 tmp_path / "consumer", (), (), ())
    assert consumer.commit == prepared.commit
    assert consumer.manifest["files"] == prepared.manifest["files"]
    assert (consumer.worktree / "src/entry.py").read_text() == replacement
    assert not marker.exists()


def test_source_and_global_git_configuration_hooks_filters_and_credentials_are_not_inherited(tmp_path, monkeypatch):
    repo, bundle, base = make_snapshot(tmp_path, {"src/entry.py": "VALUE = 1\n", ".gitattributes": "*.py filter=evil\n"})
    marker = tmp_path / "helper-executed"
    helper = tmp_path / "evil.sh"
    helper.write_text(f"#!/bin/sh\ntouch '{marker}'\ncat\n")
    helper.chmod(0o700)
    git(repo, "config", "filter.evil.clean", str(helper))
    git(repo, "config", "filter.evil.smudge", str(helper))
    git(repo, "config", "core.fsmonitor", str(helper))
    global_config = tmp_path / "global.gitconfig"
    global_config.write_text(f"[core]\n hooksPath = {tmp_path}\n fsmonitor = {helper}\n"
                             f"[filter \"evil\"]\n clean = {helper}\n smudge = {helper}\n"
                             f"[credential]\n helper = !{helper}\n")
    (tmp_path / "post-checkout").write_text(helper.read_text())
    (tmp_path / "post-checkout").chmod(0o700)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(tmp_path))
    monkeypatch.setenv("GIT_ALTERNATE_OBJECT_DIRECTORIES", str(repo / ".git/objects"))
    monkeypatch.setenv("GIT_SSH_COMMAND", str(helper))
    result = prepare(tmp_path, bundle, base, [TextPatch("src/entry.py", "VALUE = 1\n", "VALUE = 2\n")])
    assert not marker.exists()
    assert result.commit != base
    config = (result.worktree.parent / "repository.git/config").read_text()
    assert "helper" not in config and "filter" not in config and "remote" not in config


@pytest.mark.parametrize("name", [
    "../escape.py", "/tmp/escape.py", "src/../escape.py", "src//entry.py", "src\\entry.py",
    "src/entry\n.py", ".git/config", "src/.git/config", "src/.gitattributes", "src/.gitmodules",
    "src/evaluator.py", "src/PREREG.md", "src/objective.json", "src/docs/criteria.py", "src/sealed/test.csv",
    "other/entry.py",
])
def test_patch_path_and_criteria_boundaries_reject_before_preparing_a_checkout(tmp_path, name):
    _, bundle, base = make_snapshot(tmp_path)
    with pytest.raises(WorkspaceError):
        prepare(tmp_path, bundle, base, [TextPatch(name, None, "x = 1\n")])
    assert not (tmp_path / "job").exists()
    assert not (tmp_path / "escape.py").exists()


@pytest.mark.parametrize("patch", [
    TextPatch("src/entry.py", "VALUE =", "VALUE = 2\n"),
    TextPatch("src/entry.py", None, "VALUE = 2\n"),
    TextPatch("src/new.py", "", "VALUE = 2\n"),
    TextPatch("src/entry.py", "VALUE = 1\n", "VALUE = 1\n"),
    TextPatch("src/entry.py", "VALUE = 1\n", "def broken(:\n"),
])
def test_nonexact_preimage_noop_and_invalid_python_cannot_create_an_execution_bundle(tmp_path, patch):
    repo, bundle, base = make_snapshot(tmp_path)
    with pytest.raises(WorkspaceError):
        prepare(tmp_path, bundle, base, [patch])
    assert not (tmp_path / "job/prepared.bundle").exists()
    assert git(repo, "rev-parse", "HEAD") == base


def test_duplicate_and_untyped_patches_are_rejected(tmp_path):
    _, bundle, base = make_snapshot(tmp_path)
    patch = TextPatch("src/entry.py", "VALUE = 1\n", "VALUE = 2\n")
    with pytest.raises(WorkspaceError, match="duplicate-patch-path"):
        prepare(tmp_path, bundle, base, [patch, patch])
    with pytest.raises(WorkspaceError, match="untyped-patch"):
        prepare(tmp_path, bundle, base, [{"path": "src/entry.py"}])


@pytest.mark.parametrize("kind", ["symlink", "submodule", "gitmodules"])
def test_unsafe_committed_tree_entries_are_rejected_before_checkout(tmp_path, kind):
    repo, bundle, base = make_snapshot(tmp_path)
    if kind == "symlink":
        (repo / "src/link").symlink_to(tmp_path)
        git(repo, "add", "src/link")
    elif kind == "submodule":
        git(repo, "update-index", "--add", "--cacheinfo", f"160000,{base},vendor/module")
    else:
        (repo / ".gitmodules").write_text('[submodule "s"]\n path = vendor\n url = ext::malicious\n')
        git(repo, "add", ".gitmodules")
    git(repo, "commit", "-m", "Malformed synthetic snapshot")
    commit = git(repo, "rev-parse", "HEAD")
    git(repo, "bundle", "create", str(bundle), "HEAD")
    with pytest.raises(WorkspaceError):
        prepare(tmp_path, bundle, commit)
    assert not (tmp_path / "job/code").exists()


def test_snapshot_digest_and_exact_commit_are_required(tmp_path):
    _, bundle, base = make_snapshot(tmp_path)
    with pytest.raises(WorkspaceError, match="snapshot-digest-mismatch"):
        prepare_workspace(bundle, "0" * 64, base, tmp_path / "job", ("src",), (), ())
    assert not (tmp_path / "job").exists()
    with pytest.raises(WorkspaceError, match="invalid-base-commit"):
        prepare(tmp_path, bundle, "main")
    with pytest.raises(WorkspaceError, match="git-command-failed"):
        prepare(tmp_path, bundle, "0" * 40)


def test_incremental_bundle_cannot_borrow_source_objects(tmp_path):
    repo, bundle, base = make_snapshot(tmp_path)
    (repo / "src/entry.py").write_text("VALUE = 2\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Second synthetic commit")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "bundle", "create", str(bundle), "HEAD", "^" + base)
    with pytest.raises(WorkspaceError, match="git-command-failed"):
        prepare(tmp_path, bundle, head)
    assert not (tmp_path / "job/code").exists()


def test_workspace_destination_and_bundle_symlinks_cannot_escape(tmp_path):
    _, bundle, base = make_snapshot(tmp_path)
    linked_bundle = tmp_path / "linked.bundle"
    linked_bundle.symlink_to(bundle)
    with pytest.raises(WorkspaceError, match="symlink-local-path"):
        prepare(tmp_path, linked_bundle, base)
    target = tmp_path / "job"
    target.mkdir()
    marker = target / "existing"
    marker.write_text("preserved")
    with pytest.raises(WorkspaceError, match="workspace-already-exists"):
        prepare(tmp_path, bundle, base)
    assert marker.read_text() == "preserved"


def test_consumer_detects_dirty_code_and_mode_changes(tmp_path):
    _, bundle, base = make_snapshot(tmp_path)
    prepared = prepare(tmp_path, bundle, base)
    entry = prepared.worktree / "src/entry.py"
    entry.write_text("VALUE = 999\n")
    with pytest.raises(WorkspaceError, match="worktree-content-mismatch"):
        snapshot_files(prepared.worktree, base)
    entry.write_text("VALUE = 1\n")
    entry.chmod(0o755)
    with pytest.raises(WorkspaceError, match="worktree-mode-mismatch"):
        snapshot_files(prepared.worktree, base)
