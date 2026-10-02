import importlib.util
import subprocess
import tarfile
from pathlib import Path

import pytest


def test_runtime_archive_keeps_committed_build_inputs_and_excludes_evidence(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("brief_release_audit", Path("scripts/audit_briefing_release_gate.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    repository = tmp_path / "repository"
    repository.mkdir()
    inputs = {"src/quant_company/example.py": "committed source", "deploy/compose.yaml": "secret references only",
              "slack-apps/market_brief.json": "app manifest", "pyproject.toml": "project", "uv.lock": "lock",
              "README.md": "readme", "AGENTS.md": "instructions",
              "docs/project/evidence/snapshot.json": "large historical evidence"}
    for name, text in inputs.items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "-qm", "fixture"], cwd=repository, check=True)
    monkeypatch.setattr(module, "ROOT", repository)
    commit = module.git("rev-parse", "HEAD")
    (repository / "src/quant_company/example.py").write_text("uncommitted change")
    output = tmp_path / "runtime.tar.gz"
    receipt = module.runtime_archive(commit, output)
    assert not receipt["host_modified"] and receipt["commit"] == commit
    assert "docs/project/evidence/snapshot.json" not in receipt["files"]
    with tarfile.open(output, "r:gz") as archive:
        assert archive.extractfile("company/src/quant_company/example.py").read() == b"committed source"
    assert receipt["uncompressed_bytes"] < 32 * 1024 * 1024
    with pytest.raises(ValueError, match="runtime_archive_exists"):
        module.runtime_archive(commit, output)


def test_runtime_archive_refuses_committed_symlink(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("brief_release_audit", Path("scripts/audit_briefing_release_gate.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    repository = tmp_path / "repository"
    repository.mkdir()
    for name in module.RUNTIME_PATHS:
        path = repository / name
        if name in {"src", "deploy", "slack-apps"}:
            path.mkdir()
            (path / "example.txt").write_text("fixture")
        else:
            path.write_text("fixture")
    (repository / "src/external.py").symlink_to("/outside/secret")
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "-qm", "fixture"], cwd=repository, check=True)
    monkeypatch.setattr(module, "ROOT", repository)
    output = tmp_path / "runtime.tar.gz"
    with pytest.raises(ValueError, match="runtime_archive_nonregular_file"):
        module.runtime_archive(module.git("rev-parse", "HEAD"), output)
    assert not output.exists()
