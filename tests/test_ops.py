"""Real PostgreSQL; synthetic host snapshots and simulated Slack receipts. No production Slack writes."""

import json
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from psycopg.types.json import Jsonb

from quant_company.company import PolicyError, load_roles
from quant_company.config import Settings
from quant_company.contracts import Role, ToolRequest
from quant_company.ops.status import live_status, status_text, thread_text
from quant_company.ops.watch import OpsWatch
from quant_company.slack import SlackOutbox


@pytest.fixture
def ops(company):
    settings = company.settings
    settings.ops_status_enabled = True
    settings.ops_watch_enabled = True
    settings.ops_alert_channel_id = "COPS"
    settings.ops_watch_owner_user = "UHUMAN"
    settings.briefing_channel_id = "CBRIEF"
    settings.briefing_owner_user = "UHUMAN"
    settings.slack_allowed_channels += ["COPS", "CBRIEF"]
    company.roles["director"].tools.append("ops_status")
    company.roles["market_brief"] = Role(id="market_brief", name="Analyst", mission="Integration fixture",
                                         model="gpt-5.6-luna", instructions="Synthetic fixture only",
                                         tools=["ops_status"], can_delegate_to=[], active=True)
    return company


def now():
    return datetime.now(UTC)


def container(service, status="running", **extra):
    return {"name": f"quant-company-{service}-1", "service": service, "image": "img", "status": status,
            "health": None, "oom_killed": False, "exit_code": 0, "started_at": "2026-10-01T00:00:00Z",
            "finished_at": "0001-01-01T00:00:00Z", "restart_count": 0, **extra}


def snapshot(company, *, containers=None, drift=None, at=None, **extra):
    at = at or now()
    payload = {"format": 1, "collected_at": at.isoformat(), "probe_errors": [], "units": {},
               "host": {"disk_free_gb": 62.0, "mem_available_mb": 5400, "swap_used_mb": 40, "load_1m": 0.5, "cpus": 2},
               "containers": containers if containers is not None else [container("postgres"), container("dispatch")],
               "drift": drift or {}, "deploy_window": None, **extra}
    with company.db.transaction() as conn:
        conn.execute("""INSERT INTO ops_host_snapshots(id,collected_at,payload) VALUES (1,%s,%s)
            ON CONFLICT (id) DO UPDATE SET collected_at=EXCLUDED.collected_at,payload=EXCLUDED.payload""",
                     (at, Jsonb(payload)))


def project(company, channel, thread_ts=None):
    with company.db.transaction() as conn:
        return conn.execute("""INSERT INTO projects(id,title,instruction,owner_user,channel,thread_ts)
            VALUES (%s,'t','i','UHUMAN',%s,%s) RETURNING *""", (str(uuid4()), channel, thread_ts)).fetchone()


def edition(company, *, state="committed", due=None, day=None, kind="am", publish=True, project_id=None, error=None):
    due = due or now() - timedelta(hours=1)
    identity = str(uuid4())
    with company.db.transaction() as conn:
        conn.execute("""INSERT INTO brief_editions(id,day,kind,channel,owner_user,definition,due_at,cutoff,expires_at,
            policy_digest,publish,state,project_id,error,committed_at) VALUES (%s,%s,%s,'CBRIEF','UHUMAN','{}',%s,%s,%s,
            'p',%s,%s,%s,%s,%s)""", (identity, day or date(2026, 10, 14), kind, due, due, due + timedelta(hours=2),
                                    publish, state, project_id, error, due if state == "committed" else None))
    return identity


def video(company, edition_id, *, state="synthesizing", deadline=None, version=1, error=None):
    with company.db.transaction() as conn:
        conn.execute("""INSERT INTO video_jobs(id,edition_id,version,source_digest,source,policy,state,publish_deadline,error)
            VALUES (%s,%s,%s,'s','{}','{}',%s,%s,%s)""",
                     (str(uuid4()), edition_id, version, state, deadline or now() + timedelta(hours=1), error))


def outbox(company, kind="ops_alert"):
    with company.db.transaction() as conn:
        return conn.execute("""SELECT o.*,m.kind FROM outbox o JOIN messages m ON m.id=o.id WHERE m.kind=%s
            ORDER BY o.created_at,o.id""", (kind,)).fetchall()


def sender(company, credentials):
    calls = []

    def send(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json={"ok": True, "ts": f"90.{len(calls):06d}", "channel": body["channel"]})

    return SlackOutbox(company, credentials, httpx.MockTransport(send)), calls


async def drain(box):
    for _ in range(20):
        if not await box.send_one():
            return
    pytest.fail("outbox did not drain")


def test_ops_watch_requires_a_dedicated_allowed_channel_and_owner():
    with pytest.raises(ValueError, match="dedicated allowed"):
        Settings(ops_watch_enabled=True)
    with pytest.raises(ValueError, match="dedicated allowed"):
        Settings(ops_watch_enabled=True, ops_alert_channel_id="CBRIEF", briefing_channel_id="CBRIEF",
                 ops_watch_owner_user="U1", slack_allowed_channels=["CBRIEF"], slack_allowed_users=["U1"])
    Settings(ops_watch_enabled=True, ops_alert_channel_id="COPS", ops_watch_owner_user="U1",
             slack_allowed_channels=["COPS"], slack_allowed_users=["U1"])


def test_ops_status_is_granted_only_when_enabled():
    assert "ops_status" not in load_roles(Settings())["director"].tools
    roles = load_roles(Settings(ops_status_enabled=True))
    assert "ops_status" in roles["director"].tools and "ops_status" in roles["market_brief"].tools
    assert "ops_status" not in roles["data"].tools


def test_live_status_is_compact_puts_the_thread_first_and_never_carries_digests(ops):
    snapshot(ops, containers=[container("postgres"), container(
        "dispatch", restart_count=1, started_at=(now() - timedelta(minutes=3)).isoformat(),
        logs={"tracebacks": 2, "exceptions": ["DeadlockDetected"]})],
        drift={"SLACK_ALLOWED_CHANNELS": [{"digest": "abcdef0123456789", "services": ["api", "dispatch"]},
                                          {"digest": "fedcba9876543210", "services": ["slack-socket", "worker"]}]})
    thread = project(ops, "CBRIEF", "1.1")
    for index in range(8):
        identity = edition(ops, day=date(2026, 10, 1 + index), error="x" * 400)
        video(ops, identity, error="y" * 400, state="published")
    asked = edition(ops, day=date(2026, 10, 14), kind="pm", state="writing", due=now() + timedelta(hours=2),
                    project_id=thread["id"])
    video(ops, asked, state="rendering")
    with ops.db.transaction() as conn:
        value = live_status(conn, thread)
    text = json.dumps(value, ensure_ascii=False)
    assert len(text) <= 6000 and "abcdef0123456789" not in text
    assert value["this_thread"]["briefing"]["id"] == asked
    assert value["this_thread"]["videos"][0]["state"] == "rendering"
    assert value["host"]["containers"]["attention"] == [
        {"service": "dispatch", "why": "auto-restarted 1x; 2 tracebacks/2m DeadlockDetected"}]
    assert value["host"]["config_drift"] == {"SLACK_ALLOWED_CHANNELS": [["api", "dispatch"], ["slack-socket", "worker"]]}
    assert "🎥 렌더링 중" in thread_text(value) and "dispatch auto-restarted" in thread_text(value)
    summary = status_text(value)
    assert "컨테이너 2/2 실행" in summary and "환경값 불일치 SLACK_ALLOWED_CHANNELS" in summary


def test_live_status_marks_missing_and_stale_host_facts(ops):
    with ops.db.transaction() as conn:
        assert live_status(conn)["host"] == {"available": False, "reason": "host_probe_not_reporting"}
    snapshot(ops, at=now() - timedelta(minutes=9))
    with ops.db.transaction() as conn:
        value = live_status(conn)
    assert value["host"]["stale"] and "컨테이너 상태는 확인하지 못했습니다" not in status_text(value)
    assert "오래된 기록" in status_text(value)


def test_ops_status_tool_is_read_only_and_scoped(ops):
    request = ToolRequest(name="ops_status", arguments={})
    ops_thread, brief_thread = project(ops, "COPS", "1.1"), project(ops, "CBRIEF", "2.2")
    with ops.db.transaction() as conn:
        result = ops._tool(conn, ops_thread["id"], request, task={"agent": "director"})
        assert result["source_id"].startswith("company:") and result["host"]["available"] is False
        assert ops._tool(conn, brief_thread["id"], request, task={"agent": "market_brief"})["checked_at"]
        with pytest.raises(PolicyError, match="ops_status"):
            ops._tool(conn, ops_thread["id"], request, task={"agent": "market_brief"})
        with pytest.raises(PolicyError, match="ops_status"):
            ops._tool(conn, ops_thread["id"], ToolRequest(name="ops_status", arguments={"restart": "dispatch"}),
                      task={"agent": "director"})
        with pytest.raises(PolicyError, match="ops_status"):
            ops._tool(conn, ops_thread["id"], request, task={"agent": "data"})
    ops.settings.ops_status_enabled = False
    with ops.db.transaction() as conn, pytest.raises(PolicyError, match="ops_status"):
        ops._tool(conn, ops_thread["id"], request, task={"agent": "director"})


async def test_warning_needs_two_sightings_and_recovers_once_in_its_thread(ops, credentials):
    drift = {"SLACK_ALLOWED_CHANNELS": [{"digest": "a", "services": ["api"]}, {"digest": "b", "services": ["worker"]}]}
    watch, (box, calls) = OpsWatch(ops), sender(ops, credentials)
    snapshot(ops, drift=drift)
    assert watch.tick()["announced"] == 0 and not outbox(ops)
    assert watch.tick()["announced"] == 1
    await drain(box)
    root = calls[0]
    assert root["channel"] == "COPS" and "thread_ts" not in root and "<@UHUMAN>" not in root["text"]
    assert root["text"].startswith("🟠 *환경값 불일치: SLACK_ALLOWED_CHANNELS*")
    watch.tick()
    assert len(outbox(ops)) == 1
    snapshot(ops)
    watch.tick()
    assert len(outbox(ops)) == 1  # one clean sighting is not recovery
    assert watch.tick()["closed"] == 1
    await drain(box)
    assert len(calls) == 2 and calls[1]["thread_ts"] == "90.000001" and calls[1]["text"].startswith("✅ *해소*")
    watch.tick()
    assert len(outbox(ops)) == 2
    with ops.db.transaction() as conn:
        state = conn.execute("SELECT summary FROM ops_watch_state WHERE id=1").fetchone()["summary"]
    assert state["findings"] == []


async def test_critical_event_mentions_the_owner_once_and_closes_without_recovery(ops, credentials):
    watch, (box, calls) = OpsWatch(ops), sender(ops, credentials)
    snapshot(ops)
    missed = edition(ops, state="missed", error="delivery_window_expired")
    assert watch.tick()["announced"] == 1
    watch.tick()
    await drain(box)
    assert len(calls) == 1 and calls[0]["text"].startswith("<@UHUMAN>\n🔴 *10/14 아침 브리핑 마감 놓침*")
    with ops.db.transaction() as conn:
        conn.execute("UPDATE brief_editions SET state='committed' WHERE id=%s", (missed,))
    watch.tick()
    assert watch.tick()["closed"] == 1
    await drain(box)
    assert len(calls) == 1


def test_many_stopped_containers_are_one_alert_and_a_deploy_window_suppresses_them(ops):
    stopped = [container(name, "exited", exit_code=137) for name in ("api", "worker", "dispatch", "news-worker", "slack-socket")]
    snapshot(ops, containers=[container("postgres"), *stopped])
    watch = OpsWatch(ops)
    assert watch.tick()["findings"] == ["down:fleet"]
    until = (now() + timedelta(minutes=10)).isoformat()
    snapshot(ops, containers=[container("postgres"), *stopped],
             deploy_window={"until": until, "services": ["*"], "reason": "test"})
    assert watch.tick()["findings"] == []


def test_unit_failures_are_findings_but_a_guarded_skip_is_not(ops):
    snapshot(ops, units={"quant-company-backup": {"result": "exit-code", "exited_at": "Tue 2026-10-13 18:17:57 UTC"},
                         "quant-company-release": {"result": "exec-condition", "exited_at": None}})
    assert OpsWatch(ops).tick()["findings"] == ["unit:quant-company-backup"]


def test_stale_collector_and_database_conditions_are_findings(ops):
    snapshot(ops, at=now() - timedelta(minutes=12))
    with ops.db.transaction() as conn:
        conn.execute("UPDATE runtime_control SET paused_until=now()+interval '1 hour',reason='quota' WHERE id=1")
    late = edition(ops, state="writing", due=now() - timedelta(minutes=20))
    video(ops, late, state="rendering", deadline=now() - timedelta(minutes=5))
    findings = OpsWatch(ops).tick()["findings"]
    assert "collector" in findings and "model_pause" in findings
    assert f"brief_late:{late}" in findings and any(key.startswith("video_late:") for key in findings)


async def test_alerts_go_stale_when_the_watch_is_disabled(ops, credentials):
    snapshot(ops)
    edition(ops, state="blocked")
    OpsWatch(ops).tick()
    ops.settings.ops_watch_enabled = False
    box, calls = sender(ops, credentials)
    await drain(box)
    assert not calls and outbox(ops)[0]["status"] == "stale"


def test_tick_is_throttled_to_once_a_minute(ops):
    watch = OpsWatch(ops)
    assert watch.tick_if_due() is not None and watch.tick_if_due() is None


def test_status_command_answers_from_live_state_without_a_model(ops):
    snapshot(ops)
    ops.ingest(event_key="slack:T:COPS:1.1:director", text="상태", owner="UHUMAN", agent="director",
               channel="COPS", thread_ts="1.1", interpret=True, control_action="status")
    thread = project(ops, "CBRIEF", "2.2")
    asked = edition(ops, state="writing", due=now() + timedelta(hours=1), project_id=thread["id"])
    video(ops, asked, state="queued")
    ops.ingest(event_key="slack:T:CBRIEF:2.3:market_brief", text="상태", owner="UHUMAN", agent="market_brief",
               channel="CBRIEF", thread_ts="2.2", status_only=True)
    texts = [row["text"] for row in outbox(ops, "status")]
    assert any(text.startswith("*서버 현황*") for text in texts)
    assert any("진행 현황" in text and "🎬 영상 v1: ⏳ 시작 대기" in text for text in texts)
    with ops.db.transaction() as conn:
        assert not conn.execute("SELECT 1 FROM turns").fetchone()


def release(action, *, target="a" * 40, checked=None, **extra):
    return {"checked_at": (checked or now()).isoformat(), "mode": "report", "action": action, "current": "c" * 40,
            "target": target, "detail": None, "drift": [], "operator_files": [], "render_files": 0, **extra}


async def test_release_report_alerts_once_per_action_and_target(ops, credentials):
    watch, (box, calls) = OpsWatch(ops), sender(ops, credentials)
    snapshot(ops, release=release("render_required", render_files=3, detail="3 render files differ"))
    watch.tick()
    assert watch.tick()["announced"] == 1
    await drain(box)
    assert calls[0]["text"].startswith("🟠 *렌더 이미지 교체 필요 · aaaaaaa*") and "렌더 입력 3개" in calls[0]["text"]
    snapshot(ops, release=release("waiting_for_window"))
    watch.tick()
    assert watch.tick()["closed"] == 1
    snapshot(ops, release=release("render_required", render_files=3))
    watch.tick()
    assert watch.tick()["announced"] == 0  # the same action and commit is not announced again
    snapshot(ops, release=release("render_required", target="b" * 40, operator_files=["deploy/x.service"]))
    watch.tick()
    assert watch.tick()["announced"] == 2
    await drain(box)
    assert len(calls) == 3 and "deploy/x.service" in calls[2]["text"]
    assert not any("해소" in call["text"] for call in calls)


def test_release_waits_are_quiet_and_stalls_lag_and_failed_restores_are_findings(ops):
    watch = OpsWatch(ops)
    snapshot(ops, release=release("waiting_for_quiet", detail="worker turn", latest_green={
        "sha": "a" * 40, "completed_at": (now() - timedelta(hours=2)).isoformat()}))
    assert watch.tick()["findings"] == []
    started = (now() - timedelta(minutes=50)).isoformat()
    snapshot(ops, release=release("releasing", checked=now() - timedelta(hours=1), started_at=started,
                                  step={"name": "apply 3/7: codex-runtime", "at": (now() - timedelta(minutes=5)).isoformat()}))
    assert watch.tick()["findings"] == []  # a running release rewrites the report only at each step
    snapshot(ops, release=release("releasing", checked=now() - timedelta(hours=1), started_at=started,
                                  step={"name": "build", "at": started}))
    assert watch.tick()["findings"] == []
    snapshot(ops, release=release("releasing", checked=now() - timedelta(hours=2), started_at=started,
                                  step={"name": "build", "at": (now() - timedelta(minutes=61)).isoformat()}))
    assert watch.tick()["findings"] == ["release_step"]
    old_green = {"sha": "a" * 40, "completed_at": (now() - timedelta(hours=25)).isoformat()}
    snapshot(ops, release=release("up_to_date", target=None, latest_green=old_green,
                                  detail="main aaaaaaaaaaaa changes no file a release deploys"))
    assert watch.tick()["findings"] == []
    snapshot(ops, release=release("waiting_for_window", checked=now() - timedelta(minutes=50), latest_green=old_green))
    assert watch.tick()["findings"] == ["release_behind", "release_stale"]
    snapshot(ops, release=release("incomplete", started_at=(now() - timedelta(hours=2)).isoformat()))
    assert watch.tick()["findings"] == ["release_incomplete"]
    first = (now() - timedelta(hours=3)).isoformat()
    snapshot(ops, release=release("failed", started_at=first, restored={"ok": False, "services": ["api"]}))
    summary = watch.tick()
    assert summary["findings"] == [f"release:failed:{'a' * 40}:{first}"] and summary["announced"] == 1  # critical
    snapshot(ops, release=release("held_after_failure"))
    watch.tick()
    watch.tick()
    retried = (now() - timedelta(minutes=20)).isoformat()
    snapshot(ops, release=release("failed", started_at=retried, restored={"ok": True, "services": []}))
    watch.tick()
    assert watch.tick()["announced"] == 1  # a failed --retry of the same commit is its own report


def test_status_reply_names_the_running_release(ops):
    snapshot(ops, release=release("would_release", behind_by=2))
    with ops.db.transaction() as conn:
        text = status_text(live_status(conn, now=now()))
    assert "🚀 릴리스 ccccccc · main보다 2커밋 뒤 · 판단 would_release (report," in text


def test_a_running_release_is_a_deploy_window_and_a_new_watch_waits_for_the_probe(ops):
    watch = OpsWatch(ops)
    assert watch.tick()["findings"] == []  # the probe timer is installed after the release that enabled the watch
    with ops.db.transaction() as conn:
        conn.execute("UPDATE ops_watch_state SET summary=jsonb_set(summary,'{since}',to_jsonb(%s::text))",
                     ((now() - timedelta(minutes=11)).isoformat(),))
    assert watch.tick()["findings"] == ["collector"]
    stopped = [container("postgres"), container("dispatch", "exited"), container("worker", "exited")]
    snapshot(ops, containers=stopped, release=release("releasing", started_at=now().isoformat()))
    assert watch.tick()["findings"] == []
    snapshot(ops, containers=[container("postgres", "exited")], release=release("releasing", started_at=now().isoformat()))
    assert watch.tick()["findings"] == ["down:postgres"]


def test_a_login_flag_from_before_a_successful_call_is_not_an_alert(ops):
    snapshot(ops)
    watch = OpsWatch(ops)

    def accounts(**columns):
        with ops.db.transaction() as conn:
            conn.execute("""UPDATE model_accounts SET authentication='needs_login',checked_at=%(checked_at)s,
                last_success_at=%(last_success_at)s,last_fault=%(last_fault)s WHERE profile='primary'""",
                         {"checked_at": None, "last_success_at": None, "last_fault": None, **columns})
            return status_text(live_status(conn, now=now()))

    # 2026-10-15: a 09-28 login check said needs_login, and the account answered calls ever since
    text = accounts(checked_at=now() - timedelta(days=17), last_success_at=now() - timedelta(minutes=1))
    assert "account:primary" not in watch.tick()["findings"] and "🔑" not in text
    text = accounts(checked_at=now() - timedelta(minutes=1), last_success_at=now() - timedelta(days=1))
    assert "account:primary" in watch.tick()["findings"] and "🔑 모델 계정 primary: 로그인 필요" in text
    # an auth fault does not stamp checked_at; a later success clears last_fault
    accounts(checked_at=now() - timedelta(days=17), last_success_at=now() - timedelta(minutes=5), last_fault="auth")
    assert "account:primary" in watch.tick()["findings"]
    accounts(last_success_at=now() - timedelta(minutes=1))
    assert "account:primary" not in watch.tick()["findings"]


def test_a_report_from_before_the_running_release_was_activated_is_not_alerted(ops):
    # 2026-10-15 08:40 KST: release by hand at 08:37; the timer's 08:33 report still said render_required for ed77918
    report = release("render_required", render_files=1, operator_files=["deploy/x.timer"], active="a" * 40)
    snapshot(ops, release=report)
    watch = OpsWatch(ops)
    assert watch.tick()["findings"] == []
    with ops.db.transaction() as conn:
        text = status_text(live_status(conn, now=now()))
    assert "🚀 릴리스 aaaaaaa · 마지막 점검(" in text and "이전 ccccccc 기준, 다음 점검 대기" in text
    snapshot(ops, release={**report, "checked_at": (now() - timedelta(minutes=50)).isoformat()})
    assert watch.tick()["findings"] == ["release_stale"]  # a timer that stopped is still reported
    snapshot(ops, release={**report, "active": "c" * 40})
    assert watch.tick()["findings"] == [f"release:render_required:{'a' * 40}", f"release_files:{'a' * 40}"]


def test_a_paused_release_is_quiet_until_it_has_been_paused_for_two_hours(ops):
    # RELEASE_AUTO_ENABLED=paused while an operator releases by hand (#158): the report records the commit, no verdict
    watch = OpsWatch(ops)
    old_green = {"sha": "a" * 40, "completed_at": (now() - timedelta(hours=25)).isoformat()}
    snapshot(ops, release=release("paused", target=None, mode="paused", latest_green=old_green, active="a" * 40))
    assert watch.tick()["findings"] == [] and watch.tick()["paused_since"]
    with ops.db.transaction() as conn:
        conn.execute("UPDATE ops_watch_state SET summary=jsonb_set(summary,'{paused_since}',to_jsonb(%s::text))",
                     ((now() - timedelta(hours=2, minutes=1)).isoformat(),))
    assert watch.tick()["findings"] == ["release_paused"]
    snapshot(ops, release=release("up_to_date", target=None))
    summary = watch.tick()
    assert summary["findings"] == [] and "paused_since" not in summary


def test_shared_values_that_differ_while_services_are_swapped_are_not_an_alert(ops):
    watch = OpsWatch(ops)
    drift = {"SLACK_ALLOWED_CHANNELS": [{"services": ["api", "dispatch"]}, {"services": ["worker"]}]}
    snapshot(ops, drift=drift, release=release("paused", target=None, mode="paused"))
    assert watch.tick()["findings"] == []  # 2026-10-15 11:36 KST: an operator moving 13 services one at a time
    snapshot(ops, drift=drift, release=release("releasing", started_at=now().isoformat()))
    assert watch.tick()["findings"] == []
    window = {"until": (now() + timedelta(minutes=5)).isoformat(), "services": ["worker"], "reason": "swap"}
    snapshot(ops, drift=drift, deploy_window=window, release=release("up_to_date", target=None))
    assert watch.tick()["findings"] == []
    snapshot(ops, drift=drift, release=release("up_to_date", target=None))
    assert watch.tick()["findings"] == ["drift:SLACK_ALLOWED_CHANNELS"]
