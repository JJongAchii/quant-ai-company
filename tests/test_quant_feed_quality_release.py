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


@pytest.mark.parametrize("installed", [False, True])
def test_stage_builds_only_dependency_identical_app_and_dedicated_runtime(release, monkeypatch, tmp_path, installed):
    import hashlib

    base, target = tmp_path / "base", tmp_path / "target"
    runtime_commit, app_commit, commit = "c" * 40, "d" * 40, "b" * 40
    runtime_source = tmp_path / "releases" / runtime_commit
    app_source = tmp_path / "releases" / app_commit
    fixed = ("pyproject.toml", "uv.lock", "deploy/Dockerfile", "deploy/Dockerfile.code-update",
             "deploy/entrypoint.py", "deploy/qdata-source.json")
    for root in (base, app_source, runtime_source):
        for name in fixed:
            file = root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('{"commit":"qdata"}' if name.endswith("qdata-source.json") else "unchanged")
    (base / "qdata").mkdir()
    (base / "qdata/marker").write_text("qdata")
    monkeypatch.setattr(release, "CURRENT", tmp_path / "current")
    monkeypatch.setattr(release.shutil, "disk_usage", lambda path: SimpleNamespace(free=4 * 1024**3))
    archive = tmp_path / "source.tar.gz"
    archive.write_bytes(b"exact archived source")
    args = SimpleNamespace(base="a" * 40, commit=commit, archive=str(archive),
                           archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                           qdata_tree_sha256="qtree")
    journal = tmp_path / "journal.json"
    before = {"quant-company-api-1": {"image_id": "sha256:app"},
              "quant-company-codex-runtime-1": {"image_id": "sha256:runtime"}}
    runtime_name = "quant-company-quant-codex-runtime-1" if installed else "quant-company-codex-runtime-1"
    if installed:
        before[runtime_name] = {"image_id": "sha256:runtime"}
    commands = []
    expected = {"quant_company/sample.py": "d" * 64}

    def imported(path):
        if path.name == "housing_feed_release.py":
            def unpack(data, destination):
                assert data == archive.read_bytes()
                for name in fixed:
                    file = destination / name
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_bytes((base / name).read_bytes())
                (destination / "src/quant_company").mkdir(parents=True)
            return SimpleNamespace(unpack=unpack)
        return SimpleNamespace(qdata_tree_digest=lambda root: "qtree", source_inventory=lambda root: expected)

    def run(command, **kwargs):
        commands.append(command)
        if command[:3] == ["docker", "inspect", runtime_name]:
            return json.dumps([{"Image": "sha256:runtime", "Config": {"Labels": {
                "org.opencontainers.image.revision": runtime_commit}}}])
        if command[:3] == ["docker", "inspect", "quant-company-api-1"]:
            return json.dumps([{"Image": "sha256:app", "Config": {"Labels": {
                "org.opencontainers.image.revision": app_commit}}}])
        if command[:2] == ["docker", "run"]:
            return json.dumps(expected)
        if command[:3] == ["docker", "image", "inspect"]:
            return json.dumps([{"Id": "sha256:" + command[-1].split(":")[0], "Config": {"Labels": {
                "org.opencontainers.image.revision": commit,
                "org.quant-company.qdata-revision": "qdata"}}}])
        return ""

    monkeypatch.setattr(release, "run", run)
    monkeypatch.setattr(release, "module", imported)
    monkeypatch.setattr(release, "inventory", lambda: before.copy())
    helper = SimpleNamespace(atomic=lambda path, value: path.write_bytes(value))
    release.stage(args, base, target, journal, helper)
    result = json.loads(journal.read_text())
    assert result["phase"] == "staged"
    assert result["base_app_commit"] == app_commit
    assert result["base_runtime_service"] == ("quant-codex-runtime" if installed else "codex-runtime")
    assert [image["target"] for image in result["images"]] == ["app", "codex"]
    builds = [command for command in commands if command[:2] == ["docker", "build"]]
    assert len(builds) == 2 and all("--network=none" in command for command in builds)
    assert all(any(str(item).endswith("Dockerfile.quant-code-update") for item in command) for command in builds)
    assert not any(command[:2] == ["docker", "stop"] for command in commands)


@pytest.mark.parametrize("failure", [None, "pinned", "environment", "base_image", "setenv", "protocol", "calendar"])
@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize("publication", [False, True])
def test_cutover_recreates_only_selected_services_and_preserves_publication_pause(
    release, monkeypatch, tmp_path, failure, installed, publication
):
    fail_setenv = failure == "setenv"
    old_app_commit = "d" * 40 if failure == "pinned" else "a" * 40
    env_commit = "c" * 40 if failure == "environment" else old_app_commit
    base, target, state = (tmp_path / name for name in ("base", "target", "state"))
    for path in (base, target, state / "config"):
        path.mkdir(parents=True)
    monkeypatch.setattr(release, "STATE", state)
    env = state / "config/runtime.env"
    env.write_text(
        "RELEASE_COMMIT=" + env_commit + "\nQUANT_FEED_ENABLED=true\nQUANT_FEED_PUBLISH_ENABLED="
        + str(publication).lower() + "\n"
    )
    (state / "config/data-watch-contracts.json").write_text("[]\n")
    if failure == "calendar":
        (state / "config/data-watch-contracts.json").unlink()
        (state / "config/data-watch-contracts.json").mkdir()
    journal = state / "journal.json"
    commit = "b" * 40
    journal.write_text(
        json.dumps(
            {
                "phase": "staged",
                "commit": commit,
                "previous": str(base),
                "qdata_commit": "qdata",
                "base_runtime_service": "quant-codex-runtime" if installed else "codex-runtime",
                "images": [{"target": "app", "id": "new-image", "base_id":
                            "wrong-image" if failure == "base_image" else "old-image"},
                           {"target": "codex", "id": "new-runtime-image", "base_id": "old-runtime-image"}],
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
            "quant_publish": str(publication).lower(),
        }
        for name in [
            *selected,
            "quant-company-news-worker-1",
            "quant-company-worker-1",
            "quant-company-postgres-1",
            "quant-company-codex-runtime-1",
        ]
    }
    rows["quant-company-api-1"]["image_revision"] = old_app_commit
    rows["quant-company-codex-runtime-1"]["image_id"] = "old-runtime-image"
    if installed:
        rows["quant-company-quant-codex-runtime-1"] = {
            **rows["quant-company-codex-runtime-1"], "id": "old-dedicated-runtime",
            "image_revision": old_app_commit,
        }
    commands = []
    linked = []

    def run(command, **kwargs):
        commands.append(command)
        if command[:3] == ["docker", "exec", "quant-company-quant-codex-runtime-1"]:
            return json.dumps({"quant_brief_v4": "a" * 64, "quant_critique_v2": "b" * 64, "quant_search_v1": "c" * 64})
        return ""

    def compose(helper, root, *command, image_overrides=None):
        commands.append(["compose", str(root), *command])
        if image_overrides:
            pinned = json.loads(image_overrides.read_text())["services"]
            assert pinned["quant-feed-worker"]["image"] == "old-image"
            if installed:
                assert pinned[release.DEDICATED_RUNTIME]["image"] == "old-runtime-image"
        if root == target:
            if command[-1] == release.DEDICATED_RUNTIME and command[0] == "up":
                rows["quant-company-quant-codex-runtime-1"] = {
                    **next(iter(rows.values())), "id": "new-runtime", "image_id": "new-runtime-image",
                    "image_revision": commit,
                }
            elif command[0] == "up":
                for name in selected:
                    rows[name].update(image_id="new-image", image_revision=commit)

    def setenv(helper, updates):
        current = env.read_text()
        env.write_text(
            current.replace("RELEASE_COMMIT=" + env_commit, "RELEASE_COMMIT=" + updates["RELEASE_COMMIT"])
        )
        if fail_setenv:
            raise RuntimeError("synthetic environment write failure")

    helper = SimpleNamespace(atomic=lambda path, value: path.write_bytes(value), link=linked.append)
    probed = []

    def protocol(image, runtime):
        probed.append((image, runtime))
        assert not commands and not linked
        if failure == "protocol":
            raise ValueError("runtime_protocol_mismatch")
        return {"quant_brief_v4": "a" * 64, "quant_critique_v2": "b" * 64, "quant_search_v1": "c" * 64}

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
    args = SimpleNamespace(base="a" * 40, commit=commit, preserve_publication=publication)
    if failure in ("environment", "base_image", "calendar"):
        original = env.read_bytes()
        code = "data_watch_calendar_must_be_a_file" if failure == "calendar" else "quality_release_" + failure + "_changed"
        with pytest.raises(ValueError, match=code):
            release.cutover(args, base, target, journal, helper)
        assert not probed and not commands and not linked
        assert env.read_bytes() == original and json.loads(journal.read_text())["phase"] == "staged"
        return
    if failure == "protocol":
        original = env.read_bytes()
        with pytest.raises(ValueError, match="runtime_protocol_mismatch"):
            release.cutover(args, base, target, journal, helper)
        assert probed == [("new-image", "new-runtime-image")] and not commands and not linked
        assert env.read_bytes() == original and json.loads(journal.read_text())["phase"] == "staged"
        return
    if fail_setenv:
        with pytest.raises(RuntimeError, match="synthetic"):
            release.cutover(args, base, target, journal, helper)
    else:
        release.cutover(args, base, target, journal, helper)
    compositions = [command for command in commands if command[0] == "compose"]
    if fail_setenv:
        assert compositions == [
            ["compose", str(target), "stop", release.DEDICATED_RUNTIME],
            *([["compose", str(base), "up", "-d", "--no-deps", "--force-recreate", "--wait",
                "--wait-timeout", "120", release.DEDICATED_RUNTIME]] if installed else []),
            ["compose", str(base), "up", "-d", "--no-deps", "--force-recreate", "--wait",
             "--wait-timeout", "120", *release.SELECTED],
        ]
    else:
        assert compositions == [
            ["compose", str(target), "up", "-d", "--no-deps", "--force-recreate", "--wait",
             "--wait-timeout", "120", release.DEDICATED_RUNTIME],
            ["compose", str(target), "up", "-d", "--no-deps", "--force-recreate", "--wait",
             "--wait-timeout", "120", *release.SELECTED],
        ]
    assert [command[2:] for command in commands if command[:2] == ["docker", "stop"]] == [
        ["--time", "900", "quant-company-quant-feed-worker-1"],
        ["--time", "360", "quant-company-dispatch-1", "quant-company-api-1"],
    ]
    assert linked == ([base] if fail_setenv else [target])
    assert "QUANT_FEED_PUBLISH_ENABLED=" + str(publication).lower() in env.read_text()
    assert json.loads(journal.read_text())["phase"] == (
        "rolled_back" if fail_setenv else "live_active" if publication else "preview_active")
    assert probed == [("new-image", "new-runtime-image")]


@pytest.mark.parametrize("runtime", ["matching", "different", "legacy", "empty"])
def test_native_protocol_probes_both_staged_images_without_model_calls(release, monkeypatch, runtime):
    schema = {"quant_brief_v4": "a" * 64, "quant_critique_v2": "b" * 64, "quant_search_v1": "c" * 64}
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[command.index("--entrypoint") + 2] == "sha256:runtime":
            if runtime == "legacy":
                raise RuntimeError("legacy runtime cannot import native contract")
            return json.dumps(schema if runtime == "matching" else {} if runtime == "empty"
                              else {**schema, "quant_brief_v4": "c" * 64})
        return json.dumps(schema)

    monkeypatch.setattr(release, "run", run)
    if runtime == "matching":
        assert release.protocol_preflight("sha256:target", "sha256:runtime") == schema
    else:
        with pytest.raises(ValueError, match="matching_staged_images"):
            release.protocol_preflight("sha256:target", "sha256:runtime")
    assert commands[0][:5] == ["docker", "run", "--rm", "--network", "none"]
    assert commands[1][:5] == ["docker", "run", "--rm", "--network", "none"]
    assert all(command[-1] == release.PROTOCOL_PROBE for command in commands)
    assert "CodexRunner" not in release.PROTOCOL_PROBE
    assert not any("--mount" in command or "--volume" in command for command in commands)


@pytest.mark.parametrize("failure", [None, "digest", "policy", "identity", "busy", "race",
                                     "unsettled", "propagation", "independent", "ambiguous"])
def test_resume_preserves_historic_posts_and_only_recreates_selected_services(
    release, monkeypatch, tmp_path, failure
):
    import hashlib

    state, current = tmp_path / "state", tmp_path / "release"
    commit, old_policy, live_policy = "a" * 40, "b" * 64, "c" * 64
    for directory in (state / "config", state / "secrets", state / "releases", state / "operations", current):
        directory.mkdir(parents=True)
    monkeypatch.setattr(release, "STATE", state)
    env = state / "config/runtime.env"
    original = ("RELEASE_COMMIT=" + commit + "\nQUANT_FEED_ENABLED=true\n"
                "QUANT_FEED_PUBLISH_ENABLED=false\nQUANT_FEED_CHANNEL_ID=CQUANT\nSLACK_TEAM_ID=TTEST\n")
    env.write_text(original)
    for name in ("secrets/slack-credentials.json", "config/roles.json", "config/research-profiles.json",
                 "config/research-qlab.json", "config/data-watch-contracts.json"):
        (state / name).write_text("{}")
    deployed = state / "deployment.json"
    deployed.write_text(json.dumps({"commit": commit, "phase": "preview_active"}))
    specifications = []
    for number in range(2):
        proof = state / "operations" / (str(number) + ".json")
        proof.write_text(json.dumps({"state": "passed", "slack_writes": False,
            "policy": ("d" * 64 if failure == "policy" else old_policy) if number == 0 else None,
            "case_results": {"positive": {"state": "passed"}, "negative": {"state": "passed"}},
            "case_number": number}))
        digest = hashlib.sha256(proof.read_bytes()).hexdigest()
        specifications.append(str(proof) + ":" + ("e" * 64 if failure == "digest" else digest))
    args = SimpleNamespace(base=commit, commit=commit, channel="CQUANT", qualification=specifications)
    selected = {"quant-company-" + name + "-1" for name in release.SELECTED}
    rows = {name: {"id": name, "image_id": "fixed-image", "running": True, "oom": False,
                   "restarts": 0, "health": "healthy", "image_revision": commit, "quant_publish": "false"}
            for name in selected | {"quant-company-quant-codex-runtime-1", "quant-company-news-worker-1",
                                    "quant-company-worker-1", "quant-company-postgres-1"}}
    commands, activity_calls = [], []
    interrupted = False

    def run(command, **kwargs):
        commands.append(command)
        if command[:2] == ["docker", "stop"]:
            rows["quant-company-quant-feed-worker-1"]["running"] = False
        if command[:2] == ["docker", "start"]:
            rows["quant-company-quant-feed-worker-1"]["running"] = True
        if "psql" in command:
            # A delivered historic publication is allowed. Only unresolved
            # statuses are counted, rather than imposing first-activation zero.
            assert "NOT IN ('delivered','stale')" in command[-1]
            return "1" if failure == "unsettled" else "0"
        if command[-1] == release.SLACK_PROBE:
            return json.dumps({"ok": True, "user_id": "WRONG" if failure == "identity" else "UBOT",
                               "app_id": "AAPP", "team_id": "TTEST"})
        return ""

    def activity():
        activity_calls.append(True)
        busy = (failure == "busy" or failure == "race" and len(activity_calls) > 1 or interrupted)
        return {"running_quant_calls": int(busy), "pending_quant_outbox": 0, "sending_outbox": 0}

    def probe():
        enabled = "QUANT_FEED_PUBLISH_ENABLED=true" in env.read_text()
        return {"authorized": True, "channel": "CQUANT", "publish_enabled":
                enabled and failure != "propagation", "policy": live_policy if enabled else old_policy,
                "live_policy": live_policy}

    def compose(helper, root, *command):
        nonlocal interrupted
        commands.append(["compose", *command])
        assert root == current
        if failure == "ambiguous":
            interrupted = True
            raise RuntimeError("possible new frozen call; do not replay")
        enabled = "QUANT_FEED_PUBLISH_ENABLED=true" in env.read_text()
        for service in command[command.index("120") + 1:]:
            assert service in release.SELECTED
            rows["quant-company-" + service + "-1"].update(
                id="recreated-" + service, running=True, quant_publish="true" if enabled else "false")
        if failure == "independent":
            rows["quant-company-news-worker-1"]["id"] = "changed-by-another-owner"

    helper = SimpleNamespace(atomic=lambda path, value: path.write_bytes(value))

    def setenv(helper, updates):
        assert updates == {"QUANT_FEED_PUBLISH_ENABLED": "true"}
        helper.atomic(env, env.read_bytes().replace(b"QUANT_FEED_PUBLISH_ENABLED=false",
                                                  b"QUANT_FEED_PUBLISH_ENABLED=true"))

    monkeypatch.setattr(release, "run", run)
    monkeypatch.setattr(release, "activity", activity)
    monkeypatch.setattr(release, "publication_probe", probe)
    monkeypatch.setattr(release, "compose", compose)
    monkeypatch.setattr(release, "inventory", lambda: {name: row.copy() for name, row in rows.items()})
    monkeypatch.setattr(release, "module", lambda path: SimpleNamespace(
        setenv=setenv, APP_ID="AAPP", BOT_USER_ID="UBOT"))
    journal = state / "releases" / ("quant-feed-resume-" + commit + ".json")
    if failure:
        with pytest.raises((ValueError, RuntimeError)):
            release.resume(args, current, deployed, helper)
        if failure == "ambiguous":
            assert "QUANT_FEED_PUBLISH_ENABLED=true" in env.read_text()
            assert json.loads(journal.read_text())["phase"] == "intervention_required"
        else:
            assert env.read_text() == original
        if failure in {"digest", "policy", "identity", "busy"}:
            assert not journal.exists()
            assert not any(command[:2] == ["docker", "stop"] for command in commands)
        elif failure != "ambiguous":
            assert json.loads(journal.read_text())["phase"] == "rolled_back"
            assert rows["quant-company-quant-feed-worker-1"]["running"]
    else:
        release.resume(args, current, deployed, helper)
        result = json.loads(journal.read_text())
        assert result["phase"] == "live_active" and result["old_documents_untouched"]
        assert result["independent_services_preserved"]
        assert "QUANT_FEED_PUBLISH_ENABLED=true" in env.read_text()
        assert len([command for command in commands if command[0] == "compose"]) == 2
        before_retry = len(commands)
        release.resume(args, current, deployed, helper)
        assert len(commands) == before_retry
    assert all(command[-1] == "quant-company-quant-feed-worker-1" for command in commands
               if command[:2] in (["docker", "stop"], ["docker", "start"]))
    assert (state / "secrets/slack-credentials.json").read_text() == "{}"
    assert not any("UPDATE quant_feed_documents" in str(command) or "INSERT INTO quant_feed_calls" in str(command)
                   for command in commands)
