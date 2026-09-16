"""Configuration and failure-path tests; these do not stand in for a deployed system."""

import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

DEPLOY = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("state_backup", DEPLOY / "state_backup.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


def test_compose_model_boundary_and_published_ports():
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is not installed")
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(DEPLOY / ".env.example"), "-f",
         str(DEPLOY / "compose.yaml"), "config", "--format", "json"],
        check=True, capture_output=True, text=True,
    )
    config = json.loads(result.stdout)
    services = config["services"]
    model = services["codex-runtime"]
    assert set(model["networks"]) & set(services["postgres"]["networks"]) == set()
    assert [item["source"] for item in model["secrets"]] == ["model_runtime_token"]
    assert not services["postgres"].get("ports") and not model.get("ports")
    assert services["api"]["ports"][0]["host_ip"] == "127.0.0.1"
    assert {port["published"] for port in services["caddy"]["ports"]} == {"80", "443"}
    for service in services.values():
        assert not service.get("privileged", False)
        assert all("docker.sock" not in mount.get("source", "") for mount in service.get("volumes", []))
    for name in ("api", "dispatch", "worker", "codex-runtime"):
        assert services[name]["read_only"] and services[name]["user"] == "10001:10001"
    for name in ("api", "dispatch", "postgres"):
        assert "model_runtime_token" not in [item["source"] for item in services[name].get("secrets", [])]


def test_cloud_template_db_not_public_and_ssh_restricted():
    template = json.loads((DEPLOY / "lightsail.json").read_text())
    ports = template["Resources"]["CompanyHost"]["Properties"]["Networking"]["Ports"]
    assert {port["FromPort"] for port in ports} == {22, 80, 443}
    assert next(port for port in ports if port["FromPort"] == 22)["Cidrs"] == [{"Ref": "SshCidr"}]
    bucket = template["Resources"]["BackupBucket"]["Properties"]
    assert all(bucket["PublicAccessBlockConfiguration"].values())
    assert bucket["VersioningConfiguration"]["Status"] == "Enabled"


def test_provision_preview_never_calls_aws(tmp_path):
    # No AWS executable on PATH. Preview must still work without touching an account.
    result = subprocess.run(
        [sys.executable, str(DEPLOY / "provision.py"), "--zone", "ap-northeast-2a", "--account-id",
         "123456789012", "--key-pair", "example", "--ssh-cidr", "192.0.2.1/32", "--bucket", "example-bucket"],
        env={"PATH": str(tmp_path)}, capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["mode"] == "preview-only"


def test_restore_refuses_live_database_before_external_calls(tmp_path):
    result = subprocess.run(
        [sys.executable, str(DEPLOY / "state_backup.py"), "restore", "--env-file",
         str(DEPLOY / ".env.example"), "--archive", str(tmp_path / "backup.tar.gz"),
         "--database", "quant_company", "--apply"],
        env={"PATH": str(tmp_path)}, capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "new --database restore_*" in result.stderr


def bundle_fixture(tmp_path, malicious_name=None):
    payloads = {"database.dump": b"PGDMP representative test bytes", "roles.json": b"[]",
                "runtime.env": b"DATABASE_NAME=quant_company\n", "jobs/sample/manifest.json": b"{}"}
    manifest = {"schema_version": 1, "release_commit": "a" * 40,
                "files": {name: hashlib.sha256(value).hexdigest() for name, value in payloads.items()}}
    payloads["manifest.json"] = json.dumps(manifest).encode()
    if malicious_name:
        payloads[malicious_name] = b"should not extract"
    archive = tmp_path / "backup.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name, value in payloads.items():
            item = tarfile.TarInfo(name)
            item.size = len(value)
            bundle.addfile(item, io.BytesIO(value))
    archive.with_suffix(".gz.sha256").write_text(backup.sha256(archive) + " backup.tar.gz\n")
    return archive


def test_backup_bundle_round_trip_and_corruption(tmp_path):
    archive = bundle_fixture(tmp_path)
    output = tmp_path / "restored"
    output.mkdir()
    manifest = backup.unpack(archive, output)
    assert manifest["release_commit"] == "a" * 40
    archive.write_bytes(archive.read_bytes() + b"corruption")
    with pytest.raises(ValueError, match="checksum mismatch"):
        backup.unpack(archive, tmp_path / "not-created")


def test_backup_bundle_rejects_path_traversal(tmp_path):
    archive = bundle_fixture(tmp_path, "jobs/../../outside")
    output = tmp_path / "restored"
    output.mkdir()
    with pytest.raises(ValueError, match="unsafe archive"):
        backup.unpack(archive, output)
    assert not (tmp_path / "outside").exists()


def test_restore_refuses_existing_target_without_mutation(tmp_path, monkeypatch):
    args = SimpleNamespace(database="restore_existing", archive=bundle_fixture(tmp_path))
    monkeypatch.setattr(backup.subprocess, "check_output", lambda *args, **kwargs: "1\n")
    mutations = []
    monkeypatch.setattr(backup, "run", lambda *args, **kwargs: mutations.append(args))
    with pytest.raises(ValueError, match="already exists"):
        backup.restore(args, {"DATABASE_NAME": "quant_company"}, ["not-executed"], tmp_path)
    assert mutations == []


def test_config_refuses_secrets_that_would_enter_backup(tmp_path):
    config = tmp_path / "runtime.env"
    config.write_text("OPERATOR_TOKEN=example\n")
    with pytest.raises(ValueError, match="must not contain secrets"):
        backup.config_values(config)
