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
    rows[1]["Id"] = "recreated"
    with pytest.raises(ValueError, match="database_or_research_worker_changed"):
        release.health("commit", ["api"], "pg", before)


@pytest.mark.parametrize("fail_migration", [False, True])
def test_cutover_and_rollback_never_start_stopped_worker(release, tmp_path, monkeypatch, fail_migration):
    state, previous, target = tmp_path / "state", tmp_path / "base", tmp_path / "candidate"
    for path in (state / "config", state / "secrets", state / "releases", state / "codex/jobs", state / "claude/jobs",
                 target / "src/quant_company"):
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
    roles = [{"id": "reporter", "active": True, "model": "existing"}]
    (state / "config/roles.json").write_text(json.dumps(roles))
    (target / "src/quant_company/roles.json").write_text(json.dumps([{"id": "quant_scout", "active": False}]))
    env = 'SLACK_ALLOWED_USERS=["UOWNER"]\nSLACK_ALLOWED_CHANNELS=["CNEWS"]\nNEWS_CHANNEL_ID=CNEWS\nNEWS_PUBLISH_ENABLED=true\n'
    (state / "config/runtime.env").write_text(env)
    credential = {"app_id": release.APP_ID, "bot_user_id": release.BOT_USER_ID, "bot_token": "fixture"}
    (state / "secrets/slack-credentials.json").write_text(json.dumps({"quant_scout": credential}))
    for name in ("research-profiles.json", "research-qlab.json"):
        (state / "config" / name).write_text("{}")
    journal = state / "releases/journal.json"
    journal.write_text(json.dumps({"phase": "staged", "previous": str(previous), "running_before": selected,
                                   "worker_before": worker_before, "qdata_commit": "pin"}))
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
        assert json.loads((state / "config/roles.json").read_text())[:-1] == roles
    assert all("worker" not in command for root, command in commands)
    assert all("quant-feed-worker" not in command for root, command in commands if root == previous)
    assert json.loads(journal.read_text())["phase"] == ("rolled_back" if fail_migration else "preview_active")
