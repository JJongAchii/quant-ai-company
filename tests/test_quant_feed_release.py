import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def release():
    spec = importlib.util.spec_from_file_location("quant_release_test", ROOT / "deploy/quant_feed_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_running_inventory_and_worker_state_are_independent(release, monkeypatch):
    rows = [{"State": {"Running": name != "maintenance"}} for name in release.SERVICES]
    monkeypatch.setattr(release, "inspect", lambda names: rows)
    assert "maintenance" not in release.active_services()
    assert "worker" not in release.active_services()
    worker = {"Id": "worker", "State": {"Running": True, "OOMKilled": False}, "RestartCount": 0}
    monkeypatch.setattr(release, "inspect", lambda names: [worker])
    assert release.worker_state() == {"id": "worker", "running": True, "oom_killed": False, "restarts": 0}


def test_health_requires_same_stopped_worker_and_database(release, monkeypatch):
    rows = [{"Id": "pg", "Name": "/quant-company-postgres-1", "State": {"Running": True, "OOMKilled": False}},
            {"Id": "worker", "State": {"Running": True, "OOMKilled": False}, "RestartCount": 0},
            {"Name": "/quant-company-api-1", "State": {"Running": True, "OOMKilled": False},
             "Config": {"Labels": {"org.opencontainers.image.revision": "commit"}}}]
    monkeypatch.setattr(release, "inspect", lambda names: rows)
    monkeypatch.setattr(release, "memory_available_mib", lambda: 600)
    before = {"id": "worker", "running": True, "oom_killed": False, "restarts": 0}
    assert release.health("commit", ["api"], "pg", before)["research_worker_preserved"] == before
    rows[2]["Config"]["Labels"]["org.opencontainers.image.revision"] = "previous"
    assert release.health("commit", ["api"], "pg", before, {"api": "previous"})["research_worker_preserved"] == before
    rows[1]["Id"] = "recreated"
    with pytest.raises(ValueError, match="database_or_research_worker_changed"):
        release.health("commit", ["api"], "pg", before)


@pytest.mark.parametrize("fail_migration", [False, True])
@pytest.mark.parametrize("preconfigured", [False, True])
def test_cutover_and_rollback_preserve_independent_worker(release, tmp_path, monkeypatch,
                                                          fail_migration, preconfigured):
    state, previous, target = tmp_path / "state", tmp_path / "base", tmp_path / "candidate"
    for path in (state / "config", state / "secrets", state / "releases", state / "codex/jobs", state / "claude/jobs",
                 target / "src/quant_company", previous / "src/quant_company"):
        path.mkdir(parents=True)
    monkeypatch.setattr(release, "STATE", state)
    selected = list(release.SERVICES)
    monkeypatch.setattr(release, "active_services", lambda: selected)
    worker_before = {"id": "worker", "running": True, "oom_killed": False, "restarts": 0}
    monkeypatch.setattr(release, "worker_state", lambda: worker_before)
    monkeypatch.setattr(release, "memory_available_mib", lambda: 600)
    monkeypatch.setattr(release, "inspect", lambda names: [{"Id": "pg"}, {"Id": "worker"}])
    monkeypatch.setattr(release, "health", lambda *args: {"research_worker_preserved": worker_before})
    monkeypatch.setattr(release.os, "fchown", lambda *args: None)
    monkeypatch.setattr(release.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(release, "run", lambda *args, **kwargs: b'{"research_jobs":0,"sending_outbox":0}')
    candidate_role = {"id": "quant_scout", "active": False}
    roles = [{"id": "reporter", "active": True, "model": "existing"}]
    if preconfigured:
        roles.append(candidate_role)
    (state / "config/roles.json").write_text(json.dumps(roles))
    (target / "src/quant_company/roles.json").write_text(json.dumps([candidate_role]))
    (previous / "src/quant_company/roles.json").write_text(json.dumps([candidate_role]))
    env = 'SLACK_ALLOWED_USERS=["UOWNER"]\nSLACK_ALLOWED_CHANNELS=["CNEWS"]\nNEWS_CHANNEL_ID=CNEWS\nNEWS_PUBLISH_ENABLED=true\n'
    (state / "config/runtime.env").write_text(env)
    credential = {"app_id": release.APP_ID, "bot_user_id": release.BOT_USER_ID, "bot_token": "fixture"}
    (state / "secrets/slack-credentials.json").write_text(json.dumps({"quant_scout": credential}))
    for name in ("research-profiles.json", "research-qlab.json"):
        (state / "config" / name).write_text("{}")
    journal = state / "releases/journal.json"
    journal.write_text(json.dumps({"phase": "staged", "previous": str(previous), "running_before": selected,
                                   "worker_at_stage": {**worker_before, "running": False}, "qdata_commit": "pin"}))
    commands = []

    def compose(root, *command):
        commands.append((root, command))
        if fail_migration and command[0] == "run":
            raise RuntimeError("synthetic migration failure")

    module = SimpleNamespace(atomic=lambda path, data: path.write_bytes(data), compose=compose, link=Mock(),
                             compose_command=lambda root: ["compose"])
    monkeypatch.setattr(release, "helper", lambda root: module)
    monkeypatch.setattr(release, "take_backup_preserving_worker",
                        lambda *args: print('{"backup":"synthetic-only"}'))
    args = SimpleNamespace(commit="candidate", channel="CQUANT")
    if fail_migration:
        with pytest.raises(RuntimeError, match="synthetic"):
            release.cutover(args, previous, target, journal, module)
        assert (state / "config/runtime.env").read_text() == env
        assert json.loads((state / "config/roles.json").read_text()) == roles
    else:
        release.cutover(args, previous, target, journal, module)
        assert "QUANT_FEED_PUBLISH_ENABLED=false" in (state / "config/runtime.env").read_text()
        deployed = json.loads((state / "config/roles.json").read_text())
        assert deployed[0] == roles[0]
        assert [role for role in deployed if role["id"] == "quant_scout"] == [candidate_role]
    assert all("worker" not in command for root, command in commands)
    assert all("quant-feed-worker" not in command for root, command in commands if root == previous)
    receipt = json.loads(journal.read_text())
    assert receipt["phase"] == ("rolled_back" if fail_migration else "preview_active")
    assert receipt["worker_at_cutover"] == worker_before


@pytest.mark.parametrize("fail_recreate", [False, True])
def test_activation_requires_preview_and_preserves_independent_worker(release, tmp_path, monkeypatch,
                                                                      fail_recreate):
    state, current = tmp_path / "state", tmp_path / ("a" * 40)
    for path in (state / "config", state / "secrets", state / "releases", state / "codex/jobs", current):
        path.mkdir(parents=True)
    monkeypatch.setattr(release, "STATE", state)
    selected = list(release.SERVICES)
    monkeypatch.setattr(release, "active_services", lambda: selected)
    worker_before = {"id": "worker", "running": True, "oom_killed": False, "restarts": 0}
    monkeypatch.setattr(release, "worker_state", lambda: worker_before)

    def inspect(names):
        if names == ["quant-feed-worker"]:
            return [{"State": {"Running": True}}]
        if names == ["postgres"]:
            return [{"Id": "pg"}]
        raise AssertionError(names)

    monkeypatch.setattr(release, "inspect", inspect)
    monkeypatch.setattr(release, "health", lambda *args: {"research_worker_preserved": worker_before})
    monkeypatch.setattr(release.os, "fchown", lambda *args: None)
    commit = current.name
    env = (f"RELEASE_COMMIT={commit}\nQUANT_FEED_ENABLED=true\nQUANT_FEED_PUBLISH_ENABLED=false\n"
           "QUANT_FEED_CHANNEL_ID=CQUANT\nSLACK_TEAM_ID=TTEAM\n")
    (state / "config/runtime.env").write_text(env)
    credential = {"app_id": release.APP_ID, "bot_user_id": release.BOT_USER_ID, "bot_token": "fixture"}
    (state / "secrets/slack-credentials.json").write_text(json.dumps({"quant_scout": credential}))
    for name in ("roles.json", "research-profiles.json", "research-qlab.json"):
        (state / "config" / name).write_text("{}")
    journal = state / "releases/journal.json"
    journal.write_text(json.dumps({"phase": "preview_active", "commit": commit, "publication_enabled": False}))
    commands = []

    def compose(root, *command):
        commands.append(command)
        if fail_recreate and "quant-feed-worker" in command:
            raise RuntimeError("synthetic recreate failure")

    module = SimpleNamespace(atomic=lambda path, data: path.write_bytes(data), compose=compose)

    def run(command, **kwargs):
        body = kwargs.get("input", b"")
        if b"previews" in body:
            return b'{"previews":1,"publications":0,"running_quant_calls":0,"sending_outbox":0}'
        if b"auth.test" in body:
            return json.dumps({"ok": True, "user_id": release.BOT_USER_ID, "team_id": "TTEAM"}).encode()
        if b"publish_enabled" in body:
            return b'{"publish_enabled":true,"authorized":true}'
        raise AssertionError(command)

    monkeypatch.setattr(release, "run", run)
    args = SimpleNamespace(commit=commit, channel="CQUANT")
    if fail_recreate:
        with pytest.raises(RuntimeError, match="synthetic"):
            release.activate(args, current, current, journal, module)
        assert (state / "config/runtime.env").read_text() == env
    else:
        release.activate(args, current, current, journal, module)
        assert "QUANT_FEED_PUBLISH_ENABLED=true" in (state / "config/runtime.env").read_text()
    receipt = json.loads(journal.read_text())
    assert receipt["phase"] == ("preview_active" if fail_recreate else "live_active")
    assert receipt["worker_at_activation"] == worker_before
    assert all("worker" not in command for command in commands)


@pytest.mark.parametrize("handoff_state", ["catalog_preview_active", "cutover_started"])
def test_reactivation_requires_prior_live_receipt_and_exact_handoff(release, tmp_path, monkeypatch,
                                                                    handoff_state):
    state = tmp_path / "state"
    (state / "releases").mkdir(parents=True)
    monkeypatch.setattr(release, "STATE", state)
    old_commit, new_commit = "a" * 40, "b" * 40
    old = {"commit": old_commit, "phase": "live_active", "publication_enabled": True}
    (state / "releases" / f"quant-feed-{old_commit}.json").write_text(json.dumps(old))
    handoff = {"previous": old_commit, "candidate": new_commit, "state": handoff_state,
               "publication_enabled": False}
    (state / "releases/handoff.json").write_text(json.dumps(handoff))
    preserved = {"news-worker": {"id": "news", "restarts": 0},
                 "data-watch-worker": {"id": "watch", "restarts": 0},
                 "slack-socket": {"id": "socket", "restarts": 0},
                 "codex-runtime": {"id": "codex", "restarts": 0},
                 "claude-runtime": {"id": "claude", "restarts": 0}}
    monkeypatch.setattr(release, "service_identity", lambda names: preserved)
    monkeypatch.setattr(release, "inspect", lambda names: [
        {"Config": {"Labels": {"org.opencontainers.image.revision": old_commit}}},
        {"Config": {"Labels": {"org.opencontainers.image.revision": old_commit}}},
    ])
    calls = []
    monkeypatch.setattr(release, "activate", lambda *args: calls.append(args))
    module = SimpleNamespace(atomic=lambda path, data: path.write_bytes(data),
                             compose_command=lambda root: ["docker", "compose"])
    args = SimpleNamespace(base=old_commit, commit=new_commit, channel="CQUANT", handoff_journal="handoff.json")
    journal = state / "releases" / f"quant-feed-{new_commit}.json"
    current = tmp_path / new_commit
    (current / "deploy").mkdir(parents=True)
    (current / "deploy/data-watch.compose.yaml").write_text("services: {}\n")
    if handoff_state != "catalog_preview_active":
        with pytest.raises(ValueError, match="reactivation_handoff_precondition"):
            release.reactivate(args, current, current, journal, module)
        assert not journal.exists()
        assert not calls
    else:
        release.reactivate(args, current, current, journal, module)
        assert len(calls) == 1
        assert calls[0][-2] == preserved
        assert calls[0][-1] == {"codex-runtime": old_commit, "claude-runtime": old_commit}
        commands = []
        monkeypatch.setattr(release, "run", lambda command: commands.append(command))
        calls[0][4].compose(current, "up", "-d", "api")
        assert commands == [["docker", "compose", "--profile", "data-watch", "-f",
                             str(current / "deploy/data-watch.compose.yaml"), "up", "-d", "api"]]
        receipt = json.loads(journal.read_text())
        assert receipt["reactivation_from"] == old_commit
        assert receipt["publication_enabled"] is False


def test_reactivation_rejects_handoff_path_traversal(release, tmp_path):
    args = SimpleNamespace(base="a" * 40, commit="b" * 40, handoff_journal="../other.json")
    current = tmp_path / args.commit
    with pytest.raises(ValueError, match="reactivation_target_or_handoff_invalid"):
        release.reactivate(args, current, current, tmp_path / "journal.json", None)


def test_reactivation_recovers_interrupted_false_gate(release, tmp_path, monkeypatch):
    state = tmp_path / "state"
    (state / "releases").mkdir(parents=True)
    (state / "config").mkdir()
    monkeypatch.setattr(release, "STATE", state)
    old_commit, new_commit = "a" * 40, "b" * 40
    (state / "releases" / f"quant-feed-{old_commit}.json").write_text(json.dumps(
        {"commit": old_commit, "phase": "live_active", "publication_enabled": True}))
    (state / "releases/handoff.json").write_text(json.dumps(
        {"previous": old_commit, "candidate": new_commit, "state": "catalog_preview_active",
         "publication_enabled": False}))
    (state / "config/runtime.env").write_text("QUANT_FEED_PUBLISH_ENABLED=false\n")
    journal = state / "releases" / f"quant-feed-{new_commit}.json"
    journal.write_text(json.dumps({"phase": "activating", "commit": new_commit,
                                   "reactivation_from": old_commit, "publication_enabled": False}))
    current = tmp_path / new_commit
    (current / "deploy").mkdir(parents=True)
    (current / "deploy/data-watch.compose.yaml").write_text("services: {}\n")
    monkeypatch.setattr(release, "service_identity", lambda names: {name: {"id": name} for name in names})
    monkeypatch.setattr(release, "inspect", lambda names: [
        {"Config": {"Labels": {"org.opencontainers.image.revision": old_commit}}} for _ in names])
    commands = []
    monkeypatch.setattr(release, "run", lambda command, **kwargs: commands.append(command)
                        or b'{"publish_enabled":false}')
    activated = []
    monkeypatch.setattr(release, "activate", lambda *args: activated.append(args))
    module = SimpleNamespace(atomic=lambda path, data: path.write_bytes(data),
                             compose_command=lambda root: ["docker", "compose"])
    args = SimpleNamespace(base=old_commit, commit=new_commit, channel="CQUANT", handoff_journal="handoff.json")
    release.reactivate(args, current, current, journal, module)
    assert len(commands) == 2
    assert len(activated) == 1
    receipt = json.loads(journal.read_text())
    assert receipt["phase"] == "preview_active"
    assert receipt["activation_last_error_code"] == "interrupted_activation_restored"


def test_release_inventory_includes_quant_and_account_gateway_together(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("maintenance_release_test", ROOT / "deploy/maintenance_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state, root = tmp_path / "state", tmp_path / "release"
    (state / "config").mkdir(parents=True)
    (state / "config/runtime.env").write_text("QUANT_FEED_ENABLED=true\nMODEL_ACCOUNTS_ENABLED=true\nHOUSING_FEED_ENABLED=true\n")
    (root / "src/quant_company/quant_feed").mkdir(parents=True)
    housing = root / "src/quant_company/housing_feed/workflow.py"
    housing.parent.mkdir()
    housing.touch()
    monkeypatch.setattr(module, "STATE", state)
    names = module.services(root)
    assert "quant-feed-worker" in names
    assert "account-gateway" in names
    assert "housing-feed-worker" in names
    assert len(names) == len(set(names))
