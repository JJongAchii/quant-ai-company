import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def release():
    path = Path(__file__).resolve().parents[1] / "deploy/quant_feed_quality_release.py"
    spec = importlib.util.spec_from_file_location("quant_feed_quality_release_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_activity_uses_database_container_when_api_is_stopped(release, monkeypatch):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return json.dumps({"running_quant_calls": 0, "pending_quant_outbox": 0, "sending_outbox": 0})

    monkeypatch.setattr(release, "run", run)
    assert release.activity()["running_quant_calls"] == 0
    assert commands[0][:5] == ["docker", "exec", "-u", "postgres", "quant-company-postgres-1"]
    assert "quant_feed_calls" in commands[0][-1]


def test_cutover_recreates_only_selected_services_and_preserves_publication_pause(
    release, monkeypatch, tmp_path
):
    base, target, state = (tmp_path / name for name in ("base", "target", "state"))
    for path in (base, target, state / "config"):
        path.mkdir(parents=True)
    monkeypatch.setattr(release, "STATE", state)
    env = state / "config/runtime.env"
    env.write_text(
        "RELEASE_COMMIT=" + "a" * 40 + "\nQUANT_FEED_ENABLED=true\nQUANT_FEED_PUBLISH_ENABLED=false\n"
    )
    journal = state / "journal.json"
    commit = "b" * 40
    journal.write_text(
        json.dumps(
            {
                "phase": "staged",
                "commit": commit,
                "previous": str(base),
                "qdata_commit": "qdata",
                "images": [{"target": "app", "id": "new-image"}],
            }
        )
    )
    selected = {"quant-company-" + name + "-1" for name in release.SELECTED}
    rows = {
        name: {
            "id": name,
            "image_id": "old-image",
            "running": True,
            "oom": False,
            "restarts": 0,
            "health": "healthy",
            "image_revision": "a" * 40,
            "quant_publish": "false",
        }
        for name in [
            *selected,
            "quant-company-news-worker-1",
            "quant-company-worker-1",
            "quant-company-postgres-1",
        ]
    }
    commands = []
    linked = []

    def run(command, **kwargs):
        commands.append(command)
        return ""

    def compose(helper, root, *command):
        commands.append(["compose", str(root), *command])
        if root == target:
            for name in selected:
                rows[name].update(image_id="new-image", image_revision=commit)

    def setenv(helper, updates):
        current = env.read_text()
        env.write_text(
            current.replace("RELEASE_COMMIT=" + "a" * 40, "RELEASE_COMMIT=" + updates["RELEASE_COMMIT"])
        )

    helper = SimpleNamespace(atomic=lambda path, value: path.write_bytes(value), link=linked.append)
    monkeypatch.setattr(release, "run", run)
    monkeypatch.setattr(release, "compose", compose)
    monkeypatch.setattr(release, "module", lambda path: SimpleNamespace(setenv=setenv))
    monkeypatch.setattr(release, "inventory", lambda: {name: row.copy() for name, row in rows.items()})
    monkeypatch.setattr(
        release,
        "activity",
        lambda: {"running_quant_calls": 0, "pending_quant_outbox": 0, "sending_outbox": 0},
    )
    monkeypatch.setattr(release.time, "sleep", lambda seconds: None)
    release.cutover(SimpleNamespace(base="a" * 40, commit=commit), base, target, journal, helper)
    assert [command for command in commands if command[0] == "compose"] == [
        [
            "compose",
            str(target),
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "--wait",
            "--wait-timeout",
            "120",
            *release.SELECTED,
        ]
    ]
    assert [command[2:] for command in commands if command[:2] == ["docker", "stop"]] == [
        ["--time", "900", "quant-company-quant-feed-worker-1"],
        ["--time", "360", "quant-company-dispatch-1", "quant-company-api-1"],
    ]
    assert linked == [target]
    assert "QUANT_FEED_PUBLISH_ENABLED=false" in env.read_text()
    assert json.loads(journal.read_text())["phase"] == "preview_active"
