"""Real local Git release/config preflights. No service is deployed or restarted."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from quant_company.research.releases import (
    ReleaseError,
    activate_release,
    prepare_release,
    verify_company_pin,
)
from quant_company.research.worker import WorkerConfig, atomic_json, read_json, sha_file
from tests.test_research_worker import pinned_company  # noqa: F401


@pytest.fixture
def release_source(tmp_path, pinned_company):  # noqa: F811 -- imported pytest fixture
    source, commit = pinned_company
    bundle = tmp_path / "company.bundle"
    subprocess.run(["git", "-C", str(source), "bundle", "create", str(bundle), "HEAD"], check=True)
    releases = tmp_path / "releases"
    releases.mkdir()
    config = WorkerConfig(
        api_url="http://127.0.0.1:18764", token_file=tmp_path / "token", state_dir=tmp_path / "state",
        repo_source=tmp_path / "research", input_source=tmp_path / "inputs", evidence_repo=tmp_path / "evidence",
        research_python=Path(sys.executable), company_repo=source, company_commit=commit,
    )
    return config, bundle, releases


def prepare(release_source):
    config, bundle, root = release_source
    return prepare_release(source_snapshot=bundle, snapshot_sha256=sha_file(bundle),
                           commit=config.company_commit, release_root=root, config=config)


def test_release_checkout_and_config_atomically_agree(release_source, tmp_path):
    prepared = prepare(release_source)
    active = tmp_path / "active-worker.json"
    active.write_text('{"old":"config"}\n')
    config = activate_release(prepared, active_config=active)
    assert WorkerConfig.from_file(active) == config == WorkerConfig.from_file(prepared.config_path)
    assert config.company_commit == prepared.commit
    assert subprocess.check_output(["git", "-C", str(config.company_repo), "rev-parse", "HEAD"],
                                   text=True).strip() == prepared.commit
    assert subprocess.run(["git", "-C", str(config.company_repo), "symbolic-ref", "-q", "HEAD"],
                          capture_output=True).returncode == 1
    assert subprocess.check_output(["git", "-C", str(config.company_repo), "status", "--porcelain"], text=True) == ""
    assert read_json(prepared.directory / "release.json")["preflight_exit_code"] == 0
    assert active.stat().st_mode & 0o077 == 0
    verify_company_pin(config)


def test_tampered_config_fails_before_active_pointer_changes(release_source, tmp_path):
    prepared = prepare(release_source)
    active = tmp_path / "active-worker.json"
    before = b'{"old":"config"}\n'
    active.write_bytes(before)
    prepared.config_path.chmod(0o600)
    value = read_json(prepared.config_path)
    value["company_commit"] = "a" * 40
    prepared.config_path.write_text(json.dumps(value))
    with pytest.raises(ReleaseError, match="identity-mismatch"):
        activate_release(prepared, active_config=active)
    assert active.read_bytes() == before


def test_dirty_release_cannot_be_selected(release_source, tmp_path):
    prepared = prepare(release_source)
    config = WorkerConfig.from_file(prepared.config_path)
    (config.company_repo / "src/quant_company/research/worker.py").write_text("# changed\n")
    active = tmp_path / "active-worker.json"
    with pytest.raises(ReleaseError, match="pin-mismatch"):
        activate_release(prepared, active_config=active)
    assert not active.exists()


def test_existing_release_never_overwritten_and_inflight_config_stays_valid(release_source, tmp_path):
    first = prepare(release_source)
    active = tmp_path / "active-worker.json"
    original = activate_release(first, active_config=active)
    frozen = tmp_path / "inflight-execution-config.json"
    atomic_json(frozen, original.model_dump(mode="json"))
    frozen_bytes = frozen.read_bytes()
    with pytest.raises(ReleaseError, match="already-exists"):
        prepare(release_source)
    config, _, releases = release_source
    source = tmp_path / "next-source"
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(config.company_repo), str(source)], check=True)
    (source / "version.txt").write_text("next operator-reviewed release\n")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                    "-c", "core.hooksPath=/dev/null", "commit", "-qm", "Second immutable release"], check=True)
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    bundle = tmp_path / "second.bundle"
    subprocess.run(["git", "-C", str(source), "bundle", "create", str(bundle), "HEAD"], check=True)
    second = prepare_release(source_snapshot=bundle, snapshot_sha256=sha_file(bundle), commit=commit,
                             release_root=releases, config=config)
    current = activate_release(second, active_config=active)
    assert current.company_commit == commit != original.company_commit
    assert frozen.read_bytes() == frozen_bytes and first.config_path.exists()
    verify_company_pin(WorkerConfig.from_file(frozen))


def test_invalid_pin_or_bundle_is_rejected_before_activation(release_source, tmp_path):
    config, bundle, releases = release_source
    with pytest.raises(ReleaseError):
        prepare_release(source_snapshot=bundle, snapshot_sha256="a" * 64, commit=config.company_commit,
                        release_root=releases, config=config)
    with pytest.raises(ReleaseError):
        verify_company_pin(config.model_copy(update={"company_commit": "a" * 40}))
    assert not (tmp_path / "active-worker.json").exists()
