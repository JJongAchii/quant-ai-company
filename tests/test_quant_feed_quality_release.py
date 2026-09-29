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


@pytest.mark.parametrize("failure", [None, "setenv", "protocol"])
def test_cutover_recreates_only_selected_services_and_preserves_publication_pause(
    release, monkeypatch, tmp_path, failure
):
    fail_setenv = failure == "setenv"
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
        if fail_setenv:
            raise RuntimeError("synthetic environment write failure")

    helper = SimpleNamespace(atomic=lambda path, value: path.write_bytes(value), link=linked.append)
    probed = []

    def protocol(image):
        probed.append(image)
        assert not commands and not linked
        if failure == "protocol":
            raise ValueError("runtime_protocol_mismatch")
        return {"quant_brief_v3": "a" * 64, "quant_critique_v2": "b" * 64}

    monkeypatch.setattr(release, "protocol_preflight", protocol)
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
    args = SimpleNamespace(base="a" * 40, commit=commit)
    if failure == "protocol":
        original = env.read_bytes()
        with pytest.raises(ValueError, match="runtime_protocol_mismatch"):
            release.cutover(args, base, target, journal, helper)
        assert probed == ["new-image"] and not commands and not linked
        assert env.read_bytes() == original and json.loads(journal.read_text())["phase"] == "staged"
        return
    if fail_setenv:
        with pytest.raises(RuntimeError, match="synthetic"):
            release.cutover(args, base, target, journal, helper)
    else:
        release.cutover(args, base, target, journal, helper)
    assert [command for command in commands if command[0] == "compose"] == [
        [
            "compose",
            str(base if fail_setenv else target),
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
    assert linked == ([base] if fail_setenv else [target])
    assert "QUANT_FEED_PUBLISH_ENABLED=false" in env.read_text()
    assert json.loads(journal.read_text())["phase"] == ("rolled_back" if fail_setenv else "preview_active")
    assert probed == ["new-image"]


@pytest.mark.parametrize("runtime", ["matching", "different", "legacy", "empty"])
def test_native_protocol_probes_both_installed_schemas_without_model_calls(release, monkeypatch, runtime):
    schema = {"quant_brief_v3": "a" * 64, "quant_critique_v2": "b" * 64}
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[1] == "exec":
            if runtime == "legacy":
                raise RuntimeError("legacy runtime cannot import native contract")
            return json.dumps(schema if runtime == "matching" else {} if runtime == "empty"
                              else {**schema, "quant_brief_v3": "c" * 64})
        return json.dumps(schema)

    monkeypatch.setattr(release, "run", run)
    if runtime == "matching":
        assert release.protocol_preflight("sha256:target") == schema
    else:
        with pytest.raises(ValueError, match="matching_runtime_before_cutover"):
            release.protocol_preflight("sha256:target")
    assert commands[0][:5] == ["docker", "run", "--rm", "--network", "none"]
    assert commands[1][:3] == ["docker", "exec", "quant-company-codex-runtime-1"]
    assert all(command[-1] == release.PROTOCOL_PROBE for command in commands)
    assert "CodexRunner" not in release.PROTOCOL_PROBE
    assert not any("--mount" in command or "--volume" in command for command in commands)
