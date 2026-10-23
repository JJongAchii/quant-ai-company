"""Host probe on synthetic docker/systemd output; Slack sends are captured, never made."""

import importlib.util
import json
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("ops_probe", Path(__file__).parents[1] / "deploy/ops_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)

NOW = datetime(2026, 10, 14, 1, 0, tzinfo=UTC)


def inspected(service, env, status="running", **state):
    return {"Name": f"/quant-company-{service}-1", "RestartCount": state.pop("restarts", 0),
            "Config": {"Image": "quant-company:abc", "Env": env, "Labels": {"com.docker.compose.service": service}},
            "State": {"Status": status, "OOMKilled": False, "ExitCode": 0, "StartedAt": "2026-10-14T00:27:50.3Z",
                      "FinishedAt": "0001-01-01T00:00:00Z", **state}}


def test_drift_reports_service_groups_with_salted_digests_only():
    secret_value = '["C0C2E59B1AA","CSECRETVALUE"]'
    rows = [inspected("api", ["SLACK_ALLOWED_CHANNELS=" + secret_value, "PATH=/a", "MODEL_RUNTIME_URL=x"]),
            inspected("dispatch", ["SLACK_ALLOWED_CHANNELS=" + secret_value, "PATH=/b"]),
            inspected("slack-socket", ['SLACK_ALLOWED_CHANNELS=["C1"]']),
            inspected("caddy", [])]
    found = probe.drift(rows, "salt")
    assert list(found) == ["SLACK_ALLOWED_CHANNELS"]
    assert [group["services"] for group in found["SLACK_ALLOWED_CHANNELS"]] == [["api", "dispatch"], ["slack-socket"]]
    assert "CSECRETVALUE" not in json.dumps(found) and "C0C2E59B1AA" not in json.dumps(found)
    assert found != probe.drift(rows, "another-salt")  # digests never compare across runs


def test_container_summary_and_log_errors_keep_no_log_text():
    item = probe.container(inspected("dispatch", [], restarts=1, Health={"Status": "unhealthy"}))
    assert item == {"name": "quant-company-dispatch-1", "service": "dispatch", "image": "quant-company:abc",
                    "status": "running", "health": "unhealthy", "oom_killed": False, "exit_code": 0,
                    "started_at": "2026-10-14T00:27:50.3Z", "finished_at": "0001-01-01T00:00:00Z", "restart_count": 1}
    text = "\n".join(["Traceback (most recent call last):", '  File "x.py", line 1',
                      "psycopg.errors.DeadlockDetected: deadlock detected owner@example.com",
                      "Traceback (most recent call last):", "ValueError: token xoxb-123",
                      "INFO: GET /healthz 200", "ValueError: again"])
    errors = probe.log_errors(text)
    assert errors == {"tracebacks": 2, "exceptions": ["ValueError", "DeadlockDetected"]}


def test_deploy_window_rejects_malformed_files(tmp_path):
    path = tmp_path / "window.json"
    assert probe.deploy_window(NOW, path) is None
    path.write_text(json.dumps({"until": "2026-10-14T01:20:00+00:00", "services": ["api", "worker"], "reason": "r"}))
    assert probe.deploy_window(NOW, path)["services"] == ["api", "worker"]
    path.write_text(json.dumps({"until": "2026-10-14T01:20:00", "services": ["api"]}))
    assert probe.deploy_window(NOW, path) is None
    path.write_text(json.dumps({"until": "2026-10-14T01:20:00+00:00", "services": ["api; rm -rf /"]}))
    assert probe.deploy_window(NOW, path) is None
    # release.py apply (#152) marks each recreation this way
    path.write_text(json.dumps({"commit": "f" * 40, "services": ["dispatch"], "pid": 4242,
                                "started_at": "2026-10-14T00:59:00+00:00", "expires_at": "2026-10-14T01:09:00+00:00"}))
    assert probe.deploy_window(NOW, path) == {"until": "2026-10-14T01:09:00+00:00", "services": ["dispatch"],
                                              "reason": "release ffffffffffff"}


def value(*services, window=None):
    return {"containers": [probe.container(inspected(name, [], status)) for name, status in services],
            "deploy_window": window}


def test_silencers_cover_only_what_stops_in_app_alerts():
    healthy = value(("postgres", "running"), ("dispatch", "running"), ("api", "exited"))
    assert probe.silencers(healthy, NOW, timedelta(minutes=2)) == {}
    assert list(probe.silencers(healthy, NOW, timedelta(minutes=11))) == ["watch"]
    assert probe.silencers(healthy, NOW, None) == {}
    broken = value(("postgres", "exited"), ("dispatch", "exited"))
    assert sorted(probe.silencers(broken, NOW, timedelta(hours=1))) == ["dispatch", "postgres"]
    window = {"until": (NOW + timedelta(minutes=5)).isoformat(), "services": ["dispatch"]}
    assert list(probe.silencers(value(("postgres", "running"), ("dispatch", "exited"), window=window), NOW, None)) == []
    releasing = value(("postgres", "running"), ("dispatch", "exited"))
    releasing["release"] = {"action": "releasing"}
    assert probe.silencers(releasing, NOW, None) == {}


def test_a_running_backup_covers_the_services_it_stops_and_nothing_else():
    backup_spec = importlib.util.spec_from_file_location("state_backup", Path(__file__).parents[1] / "deploy/state_backup.py")
    state_backup = importlib.util.module_from_spec(backup_spec)
    backup_spec.loader.exec_module(state_backup)
    assert probe.BACKUP_SERVICES == state_backup.APP_SERVICES
    assert probe.backup_window({"quant-company-backup": {"active": "inactive", "result": "success"}}) is None
    assert probe.backup_window({}) is None
    window = probe.backup_window({"quant-company-backup": {"active": "activating", "result": "success"}})
    assert window == {"services": list(state_backup.APP_SERVICES)}
    backing_up = value(("postgres", "running"), ("dispatch", "exited"))
    backing_up["backup"] = window
    assert probe.silencers(backing_up, NOW, None) == {}
    # The backup needs PostgreSQL running for its dump, so a stopped database is still an alert.
    broken = value(("postgres", "exited"), ("dispatch", "exited"))
    broken["backup"] = window
    assert list(probe.silencers(broken, NOW, None)) == ["postgres"]


@pytest.fixture
def fallback_env(tmp_path, monkeypatch):
    credentials = tmp_path / "slack.json"
    credentials.write_text(json.dumps({"director": {"bot_token": "fake-director-token"}}))
    monkeypatch.setattr(probe, "watch_age", lambda: None)
    config = {"OPS_WATCH_ENABLED": "true", "OPS_FALLBACK_ENABLED": "true", "OPS_ALERT_CHANNEL_ID": "COPS",
              "OPS_WATCH_OWNER_USER": "UOWNER"}
    return {"config": config, "credentials_path": credentials, "state_path": tmp_path / "ops/fallback-state.json"}


def test_fallback_announces_once_recovers_once_and_never_replays_an_ambiguous_send(fallback_env):
    sent = []

    def sender(token, channel, text):
        sent.append((token, channel, text))
        return {"ok": True}

    down = value(("postgres", "running"), ("dispatch", "exited"))
    up = value(("postgres", "running"), ("dispatch", "running"))
    receipt = probe.fallback(down, NOW, sender=sender, **fallback_env)
    assert receipt["notices"] == [{"key": "dispatch", "opening": True, "outcome": "delivered"}]
    assert sent[0][:2] == ("fake-director-token", "COPS") and sent[0][2].startswith("<@UOWNER>\n🔴")
    probe.fallback(down, NOW, sender=sender, **fallback_env)
    assert len(sent) == 1
    probe.fallback(up, NOW, sender=sender, **fallback_env)
    assert len(sent) == 2 and sent[1][2].startswith("✅") and "<@" not in sent[1][2]
    probe.fallback(up, NOW, sender=sender, **fallback_env)
    assert len(sent) == 2

    def ambiguous(token, channel, text):
        sent.append(text)
        raise urllib.error.URLError("timeout")

    assert probe.fallback(down, NOW, sender=ambiguous, **fallback_env)["notices"][0]["outcome"] == "uncertain"
    probe.fallback(down, NOW, sender=ambiguous, **fallback_env)
    assert len(sent) == 3
    assert "fake-director-token" not in fallback_env["state_path"].read_text()


def test_fallback_retries_a_rejected_opening_and_stays_off_until_configured(fallback_env):
    attempts = []

    def rejected(token, channel, text):
        attempts.append(text)
        return {"ok": False, "error": "not_in_channel"}

    down = value(("postgres", "running"), ("dispatch", "exited"))
    assert probe.fallback(down, NOW, sender=rejected, **fallback_env)["notices"][0]["outcome"] == "rejected:not_in_channel"
    probe.fallback(down, NOW, sender=rejected, **fallback_env)
    assert len(attempts) == 2
    fallback_env["config"]["OPS_FALLBACK_ENABLED"] = "false"
    assert probe.fallback(down, NOW, sender=rejected, **fallback_env) == {"state": "disabled"}
    fallback_env["config"].update(OPS_FALLBACK_ENABLED="true", OPS_WATCH_ENABLED="false")
    assert probe.fallback(down, NOW, sender=rejected, **fallback_env) == {"state": "disabled"}
    assert len(attempts) == 2


def test_print_previews_without_writing_and_refuses_write_flags(monkeypatch, capsys):
    monkeypatch.setattr(probe, "snapshot", lambda now, synthetic=False: {"collected_at": "t", "containers": [],
                                                                         "probe_errors": []})
    monkeypatch.setattr(probe, "publish", lambda value: pytest.fail("preview must not publish"))
    probe.main(["--print"])
    assert json.loads(capsys.readouterr().out)["containers"] == []
    with pytest.raises(SystemExit):
        probe.main(["--print", "--publish"])


def test_release_status_keeps_only_checked_fields_and_cuts_credentials(tmp_path):
    path = tmp_path / "release-status.json"
    assert probe.release_status(path) is None
    path.write_text(json.dumps({
        "checked_at": "2026-10-14T05:26:01+00:00", "mode": "report", "action": "failed", "current": "5454e3d" + "0" * 33,
        "target": "not a commit; rm -rf /", "detail": "HTTPError: https://x-access-token:ghs_abcdefghijklmnopqrstuv@github.com",
        "latest_green": {"sha": "17d7693", "completed_at": "2026-10-14T05:13:43Z", "run": "https://example"},
        "drift": [{"service": "api", "ok": False, "problem": "config differs"}, {"service": "worker", "ok": True}],
        "operator_files": ["deploy/quant-company-ops-probe.service", "../../etc/passwd`"], "render_files": ["a", "b"],
        "restored": {"ok": True, "services": ["api", "$(id)"]}, "behind_by": True,
        "step": {"name": "apply 3/7: codex-runtime video-image-runtime", "at": "2026-10-14T10:41:00Z"}}))
    value = probe.release_status(path)
    assert value["target"] is None and value["current"].startswith("5454e3d") and value["behind_by"] is None
    assert "ghs_" not in value["detail"] and "[redacted]" in value["detail"]
    assert value["latest_green"] == {"sha": "17d7693", "completed_at": "2026-10-14T05:13:43+00:00"}
    assert value["drift"] == [{"service": "api", "problem": "config differs"}]
    assert value["operator_files"] == ["deploy/quant-company-ops-probe.service"] and value["render_files"] == 2
    assert value["restored"] == {"ok": True, "services": ["api"]}
    assert value["step"] == {"name": "apply 3/7: codex-runtime video-image-runtime", "at": "2026-10-14T10:41:00+00:00"}
    path.write_text("[]")
    with pytest.raises(ValueError):
        probe.release_status(path)


def test_release_status_names_the_release_that_runs_now(tmp_path):
    path, link = tmp_path / "release-status.json", tmp_path / "current"
    path.write_text(json.dumps({"action": "render_required", "current": "e" * 40}))
    assert probe.release_status(path, link)["active"] is None  # no activated release
    (tmp_path / "releases" / ("2" * 40)).mkdir(parents=True)
    link.symlink_to(tmp_path / "releases" / ("2" * 40))
    assert probe.release_status(path, link)["active"] == "2" * 40
    link.unlink()
    link.symlink_to(tmp_path / "releases")
    assert probe.release_status(path, link)["active"] is None


def test_a_synthetic_incident_outlasts_the_minutely_timer_and_then_lapses(tmp_path, monkeypatch):
    path = tmp_path / "ops/synthetic-incident.json"
    monkeypatch.setattr(probe, "SYNTHETIC", path)
    assert probe.synthetic_until(NOW, path) is None
    probe.start_synthetic(NOW, path)
    assert probe.synthetic_until(NOW + timedelta(minutes=2), path) == NOW + timedelta(minutes=3)
    assert probe.synthetic_until(NOW + timedelta(minutes=3, seconds=1), path) is None
    for name in ("host_facts", "inspect_containers", "unit_results"):
        monkeypatch.setattr(probe, name, lambda: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(probe, "release_status", lambda: None)
    monkeypatch.setattr(probe, "deploy_window", lambda now: None)
    assert probe.snapshot(NOW + timedelta(minutes=1)).get("synthetic_incident") is True  # a plain timer run
    assert "synthetic_incident" not in probe.snapshot(NOW + timedelta(minutes=4))


def test_the_claude_login_keeps_only_whether_it_is_logged_in_and_its_expiry(tmp_path):
    # 2026-10-19 00:01 KST: the CLI emptied the login after its refresh expiry (10-18 01:03) and the 07:05 video waited.
    auth = tmp_path / "claude/auth"
    assert probe.claude_login(auth) is None  # Claude is not set up on this host
    auth.mkdir(parents=True)
    assert probe.claude_login(auth) == {"logged_in": False, "refresh_expires_at": None}
    (auth / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "sk-ant-oat01-secret", "refreshToken": "sk-ant-ort01-secret", "expiresAt": 1792395428000,
        "refreshTokenExpiresAt": 1794724500000, "scopes": ["user:inference"], "subscriptionType": "max"}}))
    value = probe.claude_login(auth)
    assert value == {"logged_in": True, "refresh_expires_at": "2026-11-15T06:35:00+00:00"}
    assert "secret" not in json.dumps(value)
    (auth / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "", "refreshToken": "", "expiresAt": 0, "refreshTokenExpiresAt": 1792253009958}}))
    assert probe.claude_login(auth) == {"logged_in": False, "refresh_expires_at": "2026-10-17T16:03:29+00:00"}
    (auth / ".credentials.json").write_text("[]")
    with pytest.raises(ValueError):
        probe.claude_login(auth)


def test_probe_reads_the_lane_status_and_tolerates_absence(tmp_path):
    path = tmp_path / "claude/jobs/.api/status.json"
    assert probe.claude_api(path) is None
    path.parent.mkdir(parents=True)
    status = {"state": "on", "enabled": True, "cap_usd": 190.0, "spent_31d_usd": 12.5,
              "calls_31d": {"api_first": 3}, "last_fallback_at": None, "paused": None, "updated_at": 1.0}
    path.write_text(json.dumps(status))
    assert probe.claude_api(path) == status
    path.write_text("[]")
    with pytest.raises(ValueError):
        probe.claude_api(path)
    path.write_text("{")
    with pytest.raises(ValueError):
        probe.claude_api(path)
