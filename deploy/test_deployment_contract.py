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


def compose_config(*profiles, extra_env=None, overlays=()):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is not installed")
    command = ["docker", "compose", "--env-file", str(DEPLOY / ".env.example"), "-f",
               str(DEPLOY / "compose.yaml")]
    if extra_env:
        command += ["--env-file", str(extra_env)]
    for profile in profiles:
        command += ["--profile", profile]
    for overlay in overlays:
        command += ["-f", str(DEPLOY / overlay)]
    result = subprocess.run(command + ["config", "--format", "json"],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_autonomous_overlay_preserves_boundaries_and_requires_explicit_activation(tmp_path):
    env = tmp_path / "runtime.env"
    env.write_text("RESEARCH_REPORT_BUCKET=synthetic-qualification-bucket\n")
    services = compose_config(extra_env=env,
        overlays=("research.compose.yaml", "autonomous-research.compose.yaml"))["services"]
    assert services["worker"]["build"]["target"] == "autonomous-research"
    for name in ("api", "slack-socket", "worker", "dispatch"):
        assert services[name]["environment"]["COMPANY_AUTONOMOUS_RESEARCH_ENABLED"] == "false"
    for name, value in services.items():
        mounts = {mount["target"]: mount for mount in value.get("volumes", [])}
        assert ("/opt/research-audit/qlab" in mounts) == (name == "worker")
        if name in ("api", "slack-socket", "worker"):
            assert mounts["/etc/quant-company/research-profiles.json"]["read_only"]
        if name == "codex-runtime":
            assert not any("research" in target for target in mounts)
    env.write_text(env.read_text() + "COMPANY_AUTONOMOUS_RESEARCH_ENABLED=true\n")
    enabled = compose_config(extra_env=env,
        overlays=("research.compose.yaml", "autonomous-research.compose.yaml"))["services"]
    assert enabled["worker"]["environment"]["COMPANY_AUTONOMOUS_RESEARCH_ENABLED"] == "true"


def test_research_backup_keeps_durable_files_and_rejects_host_links(tmp_path):
    state, staging = tmp_path / "state", tmp_path / "staging"
    staging.mkdir()
    for name in ("research/missions/context.json", "research-audit/qlab/core/src/qlab/record.py",
                 "config/research-profiles.json", "config/research-qlab.json", "secrets/token"):
        path = state / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}' if name.endswith('.json') else 'fixture')
    cfg = {"COMPANY_RESEARCH_ENABLED": "true", "COMPANY_AUTONOMOUS_RESEARCH_ENABLED": "true"}
    backup.copy_research_state(state, staging, cfg)
    assert (staging / "research/missions/context.json").read_bytes() == b'{}'
    assert (staging / "research-audit/qlab/core/src/qlab/record.py").is_file()
    assert not (staging / "secrets").exists()
    (state / "research/unsafe").symlink_to(state / "secrets/token")
    newer = tmp_path / "newer"
    newer.mkdir()
    with pytest.raises(ValueError, match="unsafe"):
        backup.copy_research_state(state, newer, cfg)


def test_compose_model_boundary_and_published_ports():
    services = compose_config()["services"]
    model = services["codex-runtime"]
    assert "caddy" not in services
    assert set(model["networks"]) & set(services["postgres"]["networks"]) == set()
    assert [item["source"] for item in model["secrets"]] == ["model_runtime_token"]
    assert not services["postgres"].get("ports") and not model.get("ports")
    assert services["api"]["ports"][0]["host_ip"] == "127.0.0.1"
    for service in services.values():
        assert not service.get("privileged", False)
        assert all("docker.sock" not in mount.get("source", "") for mount in service.get("volumes", []))
        assert all(port["host_ip"] == "127.0.0.1" for port in service.get("ports", []))
    for name in ("api", "dispatch", "worker", "news-worker", "slack-socket", "codex-runtime"):
        assert services[name]["read_only"] and services[name]["user"] == "10001:10001"
    for name in ("api", "dispatch", "slack-socket", "postgres"):
        assert "model_runtime_token" not in [item["source"] for item in services[name].get("secrets", [])]
    receiver = services["slack-socket"]
    assert receiver["command"] == ["quant-company", "slack-socket"]
    assert set(receiver["networks"]) == {"core", "service_egress"}
    assert {item["source"] for item in receiver["secrets"]} == {"database_password", "slack_credentials"}
    assert not any(key.startswith(("TEMPORAL_", "OPERATOR_", "AWS_", "MODEL_RUNTIME_"))
                   for key in receiver["environment"])
    assert 192 * 1024 * 1024 <= int(receiver["mem_limit"]) <= 256 * 1024 * 1024


def test_compose_https_is_explicit_opt_in():
    caddy = compose_config("https")["services"]["caddy"]
    assert caddy["profiles"] == ["https"]
    assert {port["published"] for port in caddy["ports"]} == {"80", "443"}


def test_account_selection_reaches_all_consumers_without_sharing_auth_with_slack(tmp_path):
    env = tmp_path / "accounts.env"
    env.write_text('MODEL_ACCOUNTS_ENABLED=true\nMODEL_ACCOUNTS_OWNER_USER=UOWNER\nSLACK_ALLOWED_USERS=["UOWNER"]\n')
    services = compose_config("maintenance", extra_env=env)["services"]
    for name in ("api", "slack-socket", "dispatch", "worker", "news-worker", "maintenance"):
        assert services[name]["environment"]["MODEL_ACCOUNTS_ENABLED"] == "true"
        assert services[name]["environment"]["MODEL_ACCOUNTS_OWNER_USER"] == "UOWNER"
        assert not any("/codex/" in mount["source"] for mount in services[name].get("volumes", []))
    runtime = services["codex-runtime"]
    mounts = {mount["target"]: mount["source"] for mount in runtime["volumes"]}
    assert mounts["/state/auth"] != mounts["/state/backup-auth"]
    assert runtime["environment"]["CODEX_BACKUP_HOME"] == "/state/backup-auth"
    assert "/state/jobs" in mounts


def test_account_gateway_overlay_preserves_research_code_pin_and_auth_boundary(tmp_path):
    env = tmp_path / "pinned.env"
    pin = "quant-company-autonomous:" + "3" * 40
    env.write_text('MODEL_ACCOUNTS_ENABLED=true\nMODEL_ACCOUNTS_OWNER_USER=UOWNER\n'
                   'SLACK_ALLOWED_USERS=["UOWNER"]\nRESEARCH_REPORT_BUCKET=synthetic-backup-bucket\n'
                   'PINNED_COMPANY_WORKER_IMAGE=' + pin + '\n')
    services = compose_config(extra_env=env, overlays=("research.compose.yaml", "autonomous-research.compose.yaml",
                                                      "model-accounts.compose.yaml"))["services"]
    worker, gateway, model = (services[name] for name in ("worker", "account-gateway", "codex-runtime"))
    assert worker["image"] == pin
    assert worker["environment"]["MODEL_RUNTIME_URL"] == "http://account-gateway:8080"
    assert "COMPANY_CODE_COMMIT" not in worker["environment"]  # Inherited truthfully from the pinned image.
    assert gateway["environment"]["MODEL_RUNTIME_URL"] == "http://codex-runtime:8080"
    assert {s["source"] for s in gateway["secrets"]} == {"database_password", "temporal_api_key", "model_runtime_token"}
    assert all("/codex/" not in v["source"] for v in gateway.get("volumes", []))
    assert {s["source"] for s in model["secrets"]} == {"model_runtime_token"}
    assert not gateway.get("ports") and not model.get("ports")


def test_tech_feed_defaults_off_and_stays_out_of_model_container():
    services = compose_config()["services"]
    for name in ("api", "worker", "dispatch", "slack-socket"):
        environment = services[name]["environment"]
        assert environment["TECH_FEED_ENABLED"] == "false"
        assert environment["TECH_FEED_PUBLISH_ENABLED"] == "false"
        assert "TECH_FEED_CHANNEL_ID" in environment
    assert not any(key.startswith("TECH_FEED_") for key in services["codex-runtime"]["environment"])


def test_claude_profile_is_private_separately_authenticated_and_off_by_default():
    assert "claude-runtime" not in compose_config()["services"]
    services = compose_config("claude")["services"]
    claude = services["claude-runtime"]
    assert not claude.get("ports") and claude["read_only"] and claude["user"] == "10001:10001"
    assert set(claude["networks"]) == {"model", "model_egress"}
    assert {s["source"] for s in claude["secrets"]} == {"model_runtime_token"}
    assert all("/claude/" in v["source"] for v in claude["volumes"])
    assert claude["environment"]["CLAUDE_USAGE_CREDITS_DISABLED_CONFIRMED"] == "false"
    assert services["worker"]["environment"]["COMPANY_STAFF_REVIEW_ENABLED"] == "false"
    assert not any(k.startswith(("ANTHROPIC_", "SLACK_", "DATABASE_", "AWS_", "TEMPORAL_"))
                   for k in claude["environment"])


def test_maintenance_is_opt_in_and_keeps_git_credentials_out_of_models():
    assert "maintenance" not in compose_config()["services"]
    services = compose_config("maintenance")["services"]
    maintenance = services["maintenance"]
    assert maintenance["read_only"] and maintenance["user"] == "10001:10001"
    assert not maintenance.get("ports") and not maintenance.get("privileged", False)
    assert set(maintenance["networks"]) == {"core", "model", "service_egress"}
    assert "lake_read_credentials" not in {s["source"] for s in maintenance["secrets"]}
    assert "slack_credentials" not in {s["source"] for s in maintenance["secrets"]}
    assert all("docker.sock" not in mount["source"] for mount in maintenance["volumes"])
    for name, service in services.items():
        secrets = {s["source"] for s in service.get("secrets", [])}
        assert ("maintenance_github_key" in secrets) == (name == "maintenance")


def test_lake_credentials_are_mounted_only_in_worker():
    services = compose_config()["services"]
    for name, service in services.items():
        mounted = {item["source"] for item in service.get("secrets", [])}
        assert ("lake_read_credentials" in mounted) == (name == "worker")
    worker = services["worker"]
    assert worker["environment"]["AWS_SHARED_CREDENTIALS_FILE"] == "/run/secrets/lake_read_credentials"
    assert "AWS_SECRET_ACCESS_KEY" not in worker["environment"]
    policy = json.loads((DEPLOY / "lake-read-policy.json").read_text())
    actions = {action for item in policy["Statement"] for action in
               (item["Action"] if isinstance(item["Action"], list) else [item["Action"]])}
    assert actions == {"s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation"}


def test_two_gib_candidate_leaves_host_memory_without_changing_service_boundaries():
    base = compose_config()["services"]
    candidate = compose_config(extra_env=DEPLOY / "lightsail-2gb.env.example")["services"]
    assert set(candidate) == set(base)
    # Static admission check only; real RSS/OOM and execution checks are still required.
    assert sum(int(service["mem_limit"]) for service in candidate.values()) <= 1856 * 1024 * 1024
    for name, service in candidate.items():
        for boundary in ("networks", "secrets", "volumes", "ports", "read_only", "security_opt"):
            assert service.get(boundary) == base[name].get(boundary)


def test_news_worker_is_independent_and_has_only_news_runtime_credentials():
    services = compose_config()["services"]
    news = services["news-worker"]
    assert news["command"] == ["quant-company", "news-worker"]
    assert "worker" not in news["depends_on"]
    assert {item["source"] for item in news["secrets"]} == {
        "database_password", "temporal_api_key", "model_runtime_token"}
    assert int(news["mem_limit"]) == 192 * 1024 * 1024
    assert set(news["networks"]) == {"core", "model", "service_egress"}
    assert "news-worker" in backup.APP_SERVICES


def test_quant_worker_is_opt_in_and_has_no_slack_or_lake_secrets():
    assert "quant-feed-worker" not in compose_config()["services"]
    service = compose_config("quant-feed")["services"]["quant-feed-worker"]
    assert service["command"] == ["quant-company", "quant-feed-worker"]
    assert int(service["mem_limit"]) == 256 * 1024 * 1024
    assert {item["source"] for item in service["secrets"]} == {
        "database_password", "temporal_api_key", "model_runtime_token"}
    assert "worker" not in service["depends_on"]
    assert "quant-feed-worker" in backup.APP_SERVICES


def resolve_cf(node, parameters, conditions):
    # Evaluate the small intrinsic subset used by the actual ingress property.
    if isinstance(node, dict):
        if set(node) == {"Ref"}:
            return None if node["Ref"] == "AWS::NoValue" else parameters[node["Ref"]]
        if set(node) == {"Fn::Equals"}:
            left, right = node["Fn::Equals"]
            return resolve_cf(left, parameters, conditions) == resolve_cf(right, parameters, conditions)
        if set(node) == {"Fn::If"}:
            condition, yes, no = node["Fn::If"]
            selected = yes if resolve_cf(conditions[condition], parameters, conditions) else no
            return resolve_cf(selected, parameters, conditions)
        return {key: resolve_cf(value, parameters, conditions) for key, value in node.items()}
    if isinstance(node, list):
        return [value for item in node if (value := resolve_cf(item, parameters, conditions)) is not None]
    return node


@pytest.mark.parametrize("https_enabled,expected_ports", [(False, {22}), (True, {22, 80, 443})])
def test_cloud_template_only_opens_https_on_request(https_enabled, expected_ports):
    template = json.loads((DEPLOY / "lightsail.json").read_text())
    parameters = {name: value["Default"] for name, value in template["Parameters"].items()
                  if "Default" in value}
    assert parameters["EnableHttpsIngress"] == "false"
    parameters.update(EnableHttpsIngress=str(https_enabled).lower(), SshCidr="192.0.2.1/32")
    ports = resolve_cf(template["Resources"]["CompanyHost"]["Properties"]["Networking"]["Ports"],
                       parameters, template["Conditions"])
    assert {port["FromPort"] for port in ports} == expected_ports
    assert next(port for port in ports if port["FromPort"] == 22)["Cidrs"] == ["192.0.2.1/32"]
    bucket = template["Resources"]["BackupBucket"]["Properties"]
    assert all(bucket["PublicAccessBlockConfiguration"].values())
    assert bucket["VersioningConfiguration"]["Status"] == "Enabled"


@pytest.mark.parametrize("https_enabled", [False, True])
def test_provision_preview_never_calls_aws(tmp_path, https_enabled):
    # No AWS executable on PATH. Preview must still work without touching an account.
    command = [sys.executable, str(DEPLOY / "provision.py"), "--zone", "ap-northeast-2a", "--account-id",
               "123456789012", "--key-pair", "example", "--ssh-cidr", "192.0.2.1/32",
               "--bucket", "example-bucket"]
    if https_enabled:
        command.append("--enable-https-ingress")
    result = subprocess.run(command, env={"PATH": str(tmp_path)}, capture_output=True, text=True)
    assert result.returncode == 0
    plan = json.loads(result.stdout)
    assert plan["mode"] == "preview-only" and plan["https_ingress_enabled"] == https_enabled
    assert f"EnableHttpsIngress={str(https_enabled).lower()}" in plan["command_argv"]
    assert plan["exposes"] == ["22/tcp only from operator /32"] + (
        ["80/tcp and 443/tcp public"] if https_enabled else []
    )


def test_apply_refuses_to_create_resources_without_registered_ssh_key(monkeypatch):
    spec = importlib.util.spec_from_file_location("company_provision", DEPLOY / "provision.py")
    provision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provision)
    monkeypatch.setattr(sys, "argv", ["provision.py", "--apply", "--zone", "ap-northeast-2a",
        "--account-id", "123456789012", "--key-pair", "missing-key", "--ssh-cidr", "192.0.2.1/32",
        "--bucket", "example-bucket"])
    calls = []

    def read(command, **kwargs):
        calls.append(command)
        if "sts" in command:
            return "123456789012\n"
        raise subprocess.CalledProcessError(254, command)

    monkeypatch.setattr(provision.subprocess, "check_output", read)
    monkeypatch.setattr(provision.subprocess, "run", lambda command, **kwargs: calls.append(command))
    with pytest.raises(subprocess.CalledProcessError):
        provision.main()
    assert any("get-key-pair" in command for command in calls)
    assert not any("cloudformation" in command for command in calls)


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


@pytest.mark.parametrize("maintenance_running", [False, True])
@pytest.mark.parametrize("claude_running", [False, True])
def test_backup_pauses_socket_ingress_until_snapshot_is_complete(
        tmp_path, monkeypatch, maintenance_running, claude_running):
    state = tmp_path / "state"
    (state / "config").mkdir(parents=True)
    (state / "config/roles.json").write_text("[]")
    maintenance = json.loads((DEPLOY / "maintenance.example.json").read_text())
    maintenance["allowed_owners"] = ["UHUMAN"]
    maintenance["enabled"] = maintenance_running
    (state / "config/maintenance.json").write_text(json.dumps(maintenance))
    (state / "secrets").mkdir()
    (state / "secrets/maintenance_github_key").write_text("private fixture; must never enter backup")
    (state / "codex/jobs").mkdir(parents=True)
    (state / "codex/jobs/receipt.json").write_text('{"state":"completed"}')
    if claude_running:
        (state / "claude/jobs").mkdir(parents=True)
        (state / "claude/jobs/review.json").write_text('{"state":"complete"}')
        (state / "claude/auth").mkdir()
        (state / "claude/auth/credentials.json").write_text('SECRET-DO-NOT-BACKUP')
    env_file = state / "config/runtime.env"
    env_file.write_text("DATABASE_NAME=quant_company\n")
    args = SimpleNamespace(env_file=env_file, s3_uri="s3://example-bucket/company/")
    cfg = {"DATABASE_NAME": "quant_company", "RELEASE_COMMIT": "a" * 40}
    calls = []

    def record(command, **kwargs):
        calls.append(command)
        if "pg_dump" in command:
            kwargs["stdout"].write(b"PGDMP synthetic command fixture")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(backup, "run", record)
    monkeypatch.setattr(backup.subprocess, "check_output", lambda *args, **kwargs:
                        "postgres\nslack-socket\napi\nnews-worker\n" + ("maintenance\n" if maintenance_running else "")
                        + ("claude-runtime\n" if claude_running else ""))
    backup.backup(args, cfg, ["docker", "compose"], state)
    stop = next(i for i, command in enumerate(calls) if "stop" in command)
    dump = next(i for i, command in enumerate(calls) if "pg_dump" in command)
    resume = next(i for i, command in enumerate(calls) if "start" in command)
    assert stop < dump < resume
    assert "slack-socket" in calls[stop] and "slack-socket" in calls[resume]
    assert "news-worker" in calls[stop] and "news-worker" in calls[resume]
    assert "worker" not in calls[resume]  # Previously stopped processes stay stopped.
    assert ("maintenance" in calls[stop]) == ("maintenance" in calls[resume]) == maintenance_running
    assert ("claude-runtime" in calls[stop]) == ("claude-runtime" in calls[resume]) == claude_running
    recovered = tmp_path / "recovered"
    recovered.mkdir()
    manifest = backup.unpack(next((state / "backups").glob("*.tar.gz")), recovered)
    assert json.loads((recovered / "maintenance.json").read_text()) == maintenance
    assert "maintenance.json" in manifest["files"] and not any("secrets" in path for path in manifest["files"])
    assert ("claude-jobs/review.json" in manifest["files"]) == claude_running
    assert not any("auth" in path for path in manifest["files"])


def test_backup_rejects_private_material_in_maintenance_configuration(tmp_path):
    config = json.loads((DEPLOY / "maintenance.example.json").read_text())
    config["allowed_owners"] = ["UHUMAN"]
    path = tmp_path / "maintenance.json"
    config["private_key"] = "must stay in the separate secret file"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="unrecognized fields"):
        backup.maintenance_config_values(path)


def test_socket_credentials_example_covers_active_role_contract():
    credentials = json.loads((DEPLOY / "slack-credentials.example.json").read_text())
    roles = json.loads((DEPLOY.parent / "src/quant_company/roles.json").read_text())
    interactive = {role["id"] for role in roles if role["active"]}
    assert set(credentials) == interactive | {"tech_scout", "maintainer"}
    for role in interactive | {"maintainer"}:
        credential = credentials[role]
        assert set(credential) == {"app_id", "bot_user_id", "bot_token", "app_token", "signing_secret"}
        assert credential["app_token"] and credential["bot_token"]
        assert credential["app_token"] != credential["bot_token"]
        assert credential["signing_secret"] == ""  # HTTP secret is not a Socket prerequisite.
    assert set(credentials["tech_scout"]) == {"app_id", "bot_user_id", "bot_token"}
    assert not next(role for role in roles if role["id"] == "tech_scout")["active"]


def test_improvements_activation_reaches_all_company_processes_without_model_credentials(tmp_path):
    from quant_company.company import load_roles
    from quant_company.config import Settings

    env = tmp_path / "improvements.env"
    env.write_text('COMPANY_IMPROVEMENTS_ENABLED=true\nIMPROVEMENTS_CHANNEL_ID=CIMPROVE\n'
                   'SLACK_ALLOWED_CHANNELS=["CIMPROVE"]\nSLACK_ALLOWED_USERS=["UHUMAN"]\n')
    services = compose_config("maintenance", extra_env=env)["services"]
    for name in ("api", "worker", "news-worker", "dispatch", "slack-socket", "maintenance"):
        values = services[name]["environment"]
        settings = Settings(**{key.lower(): json.loads(value) if key in
                              {"SLACK_ALLOWED_USERS", "SLACK_ALLOWED_CHANNELS"} else value
                              for key, value in values.items()})
        assert settings.company_improvements_enabled and settings.improvements_channel_id == "CIMPROVE"
        # Resolve the packaged roster here, not a production bind mount on the test host.
        settings.roles_file = DEPLOY.parent / "src/quant_company/roles.json"
        assert load_roles(settings)["maintainer"].active
    assert "SLACK_CREDENTIALS_FILE" not in services["codex-runtime"]["environment"]
    assert not any("slack" in str(item) for item in services["codex-runtime"].get("secrets", []))


def test_data_watch_configuration_reaches_existing_processes_without_new_credentials(tmp_path):
    from quant_company.config import Settings

    env = tmp_path / "data-watch.env"
    env.write_text('DATA_WATCH_ENABLED=true\nDATA_WATCH_PUBLISH_ENABLED=true\nDATA_WATCH_CHANNEL_ID=CDATA\n'
                   'DATA_WATCH_OWNER_USER=UHUMAN\nSLACK_ALLOWED_CHANNELS=["CDATA"]\nSLACK_ALLOWED_USERS=["UHUMAN"]\n')
    services = compose_config("maintenance", "data-watch", extra_env=env,
                              overlays=("data-watch.compose.yaml",))["services"]
    for name in ("api", "worker", "news-worker", "dispatch", "slack-socket", "maintenance"):
        values = services[name]["environment"]
        settings = Settings(**{key.lower(): json.loads(value) if key in {"SLACK_ALLOWED_USERS", "SLACK_ALLOWED_CHANNELS"}
                               else value for key, value in values.items()})
        assert settings.data_watch_enabled and settings.data_watch_publish_enabled
        assert settings.data_watch_contracts_file.name == "data-watch-contracts.json"
        assert not settings.data_watch_core_enabled
        assert any(v["target"].endswith("data-watch-contracts.json") and v["read_only"] for v in services[name]["volumes"])
    standalone = services["data-watch-worker"]
    assert standalone["command"] == ["quant-company", "data-watch-worker"]
    assert standalone["environment"]["DATA_WATCH_ENABLED"] == "true"
    assert standalone["environment"]["AWS_SHARED_CREDENTIALS_FILE"] == "/run/secrets/lake_read_credentials"
    assert standalone["environment"]["DATA_WATCH_CONTRACTS_FILE"] == "/etc/quant-company/data-watch-contracts.json"
    assert "MODEL_RUNTIME_TOKEN_FILE" not in standalone["environment"]
    assert "SLACK_CREDENTIALS_FILE" not in standalone["environment"]
    assert not any("model_runtime_token" in str(item) or "slack_credentials" in str(item)
                   for item in standalone["secrets"])
    assert not any(key.startswith("DATA_WATCH") for key in services["codex-runtime"]["environment"])
    assert not any("data-watch" in str(v) for v in services["codex-runtime"]["volumes"])
    config = json.loads((DEPLOY / "research-worker.example.json").read_text())
    assert config["data_watch_enabled"] is False
    env.write_text(env.read_text() + 'DATA_WATCH_CORE_ENABLED=true\nRESEARCH_REPORT_BUCKET=synthetic-bucket\n')
    services = compose_config("maintenance", "data-watch", extra_env=env,
                              overlays=("research.compose.yaml", "data-watch.compose.yaml"))["services"]
    for name in ("api", "worker", "news-worker", "dispatch", "slack-socket", "maintenance"):
        values = services[name]["environment"]
        settings = Settings(**{key.lower(): json.loads(value) if key in {"SLACK_ALLOWED_USERS", "SLACK_ALLOWED_CHANNELS"}
                               else value for key, value in values.items()})
        assert settings.data_watch_core_enabled
        if name in {"api", "worker", "slack-socket", "dispatch"}:
            assert settings.company_research_enabled
    assert services["data-watch-worker"]["environment"]["COMPANY_RESEARCH_ENABLED"] == "true"
