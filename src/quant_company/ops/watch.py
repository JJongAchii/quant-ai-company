"""Durable ops alerts in the dedicated channel. The dispatcher evaluates the rules once a minute so the
watch does not depend on Temporal or a model; the host probe covers the dispatcher and PostgreSQL."""

import logging
import time
from datetime import timedelta

from psycopg.types.json import Jsonb

from ..company import as_json, stable
from .status import KST, VIDEO_LIVE, edition_name, kst, stamp, utcnow

LOCK = 71350301
TICK_SECONDS = 60
CONFIRM = {"critical": 1, "warning": 2}
CLEAR_TICKS = 2
MAX_ROOTS_PER_TICK = 5
SNAPSHOT_STALE = timedelta(minutes=5)
RECENT_START = timedelta(minutes=30)
CORE = {"postgres", "dispatch", "slack-socket", "worker"}
FLEET = 3
VIDEO_BAD = ("blocked", "uncertain", "delivery_failed", "delivery_uncertain")
BRIEF_LIVE = ("collecting", "planning", "inventorying", "writing", "reviewing", "revising", "final_reviewing", "ready")
EXPECTED_TURN_ERRORS = ("quota", "budget", "limit")
PUBLICATION_HOURS = 6
# docs/adr/0045 §8: release reports that need a person; waiting for a window, quiet or the lock is routine.
RELEASE_ALERTS = {"failed": "릴리스 실패", "error": "릴리스 점검 오류", "render_required": "렌더 이미지 교체 필요",
                  "operator_required": "운영자 조치 필요", "held_for_drift": "운영이 릴리스와 달라 반영 보류",
                  "drift": "운영이 릴리스와 다름", "refused": "main이 운영 커밋의 후손이 아님",
                  "no_green_main": "CI를 통과한 main을 찾지 못함"}
RELEASE_STALE = timedelta(minutes=45)
RELEASE_INCOMPLETE = timedelta(hours=1)
RELEASE_RUNNING = timedelta(hours=2)
RELEASE_STEP = timedelta(minutes=60)  # build ~10 min; each apply group waits at most 10 min for quiet
RELEASE_BEHIND = timedelta(hours=24)
log = logging.getLogger(__name__)


def finding(key, kind, severity, title, *lines, once=False):
    """ONCE: announce the key at most once ever, not once per episode."""
    item = {"key": key, "kind": kind, "severity": severity, "title": title[:200], "lines": [line[:300] for line in lines]}
    return {**item, "once": True} if once else item


def in_window(snapshot, service, now):
    window = snapshot.get("deploy_window")
    until = stamp(window.get("until")) if window else None
    return bool(until and until > now and ("*" in window["services"] or service in window["services"]))


def host_findings(row, now):
    if not row:
        return [finding("collector", "condition", "warning", "호스트 수집기 기록 없음",
                        "ops_probe 타이머가 아직 설치되지 않았거나 PostgreSQL에 쓰지 못하고 있습니다.",
                        "컨테이너·디스크 상태는 알 수 없습니다.")]
    age = now - row["collected_at"]
    if age > SNAPSHOT_STALE:
        return [finding("collector", "condition", "warning", f"호스트 수집기 {int(age.total_seconds() // 60)}분째 멈춤",
                        f"마지막 수집 {kst(row['collected_at'], True)} KST. 컨테이너·디스크 상태는 알 수 없습니다.")]
    snapshot, found = row["payload"], []
    if snapshot.get("synthetic_incident"):
        found.append(finding("synthetic", "condition", "warning", "테스트 경고",
                             "ops_probe --synthetic-incident 로 만든 확인용 경고입니다. 다음 수집에서 해소됩니다."))
    down = []
    for item in snapshot.get("containers") or []:
        service = item.get("service") or item.get("name")
        if in_window(snapshot, service, now):
            continue
        if item.get("status") != "running":
            down.append(item)
            continue
        if item.get("health") == "unhealthy":
            found.append(finding("unhealthy:" + service, "condition", "warning", f"{service} 상태 점검 실패(unhealthy)",
                                 "컨테이너는 실행 중이지만 healthcheck가 실패하고 있습니다."))
        if item.get("oom_killed"):
            found.append(finding("oom:" + service, "condition", "critical", f"{service} 메모리 부족으로 종료됨(OOM)",
                                 "메모리 한도를 넘어 강제 종료된 뒤 다시 시작됐습니다."))
        started = stamp(item.get("started_at"))
        count = item.get("restart_count") or 0
        if count and started and now - started < RECENT_START:
            found.append(finding("restart:" + service, "condition", "critical" if count >= 3 else "warning",
                                 f"{service} 자동 재시작 {count}회",
                                 f"마지막 시작 {kst(started)} KST. 프로세스가 오류로 종료돼 Docker가 다시 켰습니다."))
        logs = item.get("logs") or {}
        if (logs.get("tracebacks") or 0) >= 3:
            found.append(finding("errors:" + service, "condition", "warning", f"{service} 오류 반복",
                                 f"최근 2분 Traceback {logs['tracebacks']}건: {', '.join(logs.get('exceptions') or []) or '종류 미상'}"))
    if len(down) > FLEET:
        names = ", ".join(sorted(item.get("service") or item.get("name") for item in down))
        found.append(finding("down:fleet", "condition", "critical", f"컨테이너 {len(down)}개 중지",
                             f"중지된 서비스: {names}", "배포 중이라면 배포 창(deploy-window) 없이 진행된 것입니다."))
    else:
        for item in down:
            service = item.get("service") or item.get("name")
            found.append(finding("down:" + service, "condition", "critical" if service in CORE else "warning",
                                 f"{service} 중지", f"상태 {item.get('status')} · 종료 코드 {item.get('exit_code')}"
                                 f" · 종료 {kst(stamp(item.get('finished_at')), True)} KST"))
    for key, groups in (snapshot.get("drift") or {}).items():
        found.append(finding("drift:" + key, "condition", "warning", f"환경값 불일치: {key}",
                             "서비스마다 값이 다릅니다: " + " / ".join(",".join(group["services"]) for group in groups)))
    machine = snapshot.get("host") or {}
    disk = machine.get("disk_free_gb")
    if disk is not None and disk < 20:
        found.append(finding("disk", "condition", "critical" if disk < 10 else "warning", f"디스크 여유 {disk}GB",
                             "영상 작업은 10GB 미만에서 멈춥니다."))
    if machine.get("mem_available_mb") is not None and machine["mem_available_mb"] < 400:
        found.append(finding("memory", "condition", "warning", f"메모리 여유 {machine['mem_available_mb']}MB"))
    if (machine.get("swap_used_mb") or 0) > 1024:
        found.append(finding("swap", "condition", "warning", f"스왑 사용 {machine['swap_used_mb']}MB"))
    for name, unit in (snapshot.get("units") or {}).items():
        # exec-condition: the unit's own guard skipped the run (the release hold), which is not a failure.
        if unit.get("result") not in (None, "", "success", "exec-condition"):
            found.append(finding("unit:" + name, "condition", "warning", f"{name} 실패({unit['result']})",
                                 f"마지막 종료 {unit.get('exited_at') or '미상'}"))
    for error in snapshot.get("probe_errors") or []:
        found.append(finding("probe:" + error.split(":")[0], "condition", "warning", "호스트 수집 일부 실패", error))
    return found + release_findings(snapshot.get("release"), now)


def release_findings(release, now):
    """The release tool's report (/var/lib/quant-company/release-status.json, docs/adr/0045); nothing until installed."""
    if not release:
        return []
    found, action = [], release.get("action")
    current, target = release.get("current"), release.get("target") or release.get("current")
    checked, started = stamp(release.get("checked_at")), stamp(release.get("started_at"))
    where = f"대상 {(target or '?')[:7]} · 운영 {(current or '?')[:7]} · 모드 {release.get('mode') or '?'}"
    step = release.get("step") or {}
    step_at = stamp(step.get("at"))
    if action == "releasing":
        # Only the release itself rewrites the report (at each step); the timer does not start while it runs.
        if started and now - started > RELEASE_RUNNING:
            found.append(finding("release_running", "condition", "warning",
                                 f"릴리스 {(target or '?')[:7]} {int((now - started).total_seconds() // 3600)}시간째 진행 중",
                                 f"시작 {kst(started, True)} KST · 단계 {step.get('name') or '?'}", where))
        if step_at and now - step_at > RELEASE_STEP:
            found.append(finding("release_step", "condition", "warning",
                                 f"릴리스 단계 {int((now - step_at).total_seconds() // 60)}분째 그대로",
                                 f"단계 {step.get('name') or '?'} · 시작 {kst(step_at, True)} KST", where))
    elif not checked or now - checked > RELEASE_STALE:
        found.append(finding("release_stale", "condition", "warning", "자동 릴리스 점검 멈춤",
                             f"마지막 점검 {kst(checked, True) if checked else '기록 없음'} KST. "
                             "quant-company-release-auto.timer가 15분마다 갱신해야 합니다."))
    if action in RELEASE_ALERTS:
        lines = [where, *([f"사유: {release['detail']}"] if release.get("detail") else []),
                 *([f"멈춘 단계: {step['name']}"] if step.get("name") else [])]
        restored = release.get("restored")
        if action == "failed" and restored:
            moved = ", ".join(restored.get("services") or []) or "옮긴 서비스 없음"
            lines.append(("되돌리기 완료: " if restored.get("ok") else "되돌리기 미완료(다음 실행이 계속 시도): ") + moved)
        if release.get("drift"):
            lines.append("릴리스와 다른 서비스: " + ", ".join(f"{row['service']}({row['problem']})"
                                                         for row in release["drift"][:8]))
        if action == "render_required":
            lines.append(f"렌더 입력 {release.get('render_files') or '?'}개가 렌더 이미지 커밋과 다릅니다. "
                         "렌더 이미지와 작업 정의를 먼저 바꿔야 이어서 반영됩니다.")
        severity = "critical" if action == "failed" and restored and not restored.get("ok") else "warning"
        # Each attempt at a commit (a --retry included) has its own start, so each failed attempt is reported.
        attempt = f":{release.get('started_at') or 'restore'}" if action == "failed" else ""
        found.append(finding(f"release:{action}:{target}{attempt}", "event", severity,
                             f"{RELEASE_ALERTS[action]} · {(target or '?')[:7]}", *lines, once=True))
    if action == "incomplete" and started and now - started > RELEASE_INCOMPLETE:
        found.append(finding("release_incomplete", "condition", "warning",
                             f"릴리스 {(target or '?')[:7]} 1시간 넘게 미완료",
                             f"시작 {kst(started, True)} KST · 멈춘 단계 {step.get('name') or '?'}", where,
                             *([f"사유: {release['detail']}"] if release.get("detail") else [])))
    green = release.get("latest_green") or {}
    passed = stamp(green.get("completed_at"))
    if green.get("sha") and current and green["sha"] != current and passed and now - passed > RELEASE_BEHIND:
        found.append(finding("release_behind", "condition", "warning",
                             f"CI 통과 main {green['sha'][:7]} {int((now - passed).total_seconds() // 3600)}시간째 미반영",
                             f"운영 {current[:7]} · main보다 {release.get('behind_by') or '?'}커밋 뒤 · "
                             f"지금 판단 {action or '?'}"))
    if release.get("operator_files"):
        found.append(finding(f"release_files:{target}", "event", "warning",
                             f"운영자가 설치할 호스트 파일 변경 · {(target or '?')[:7]}",
                             "릴리스는 이 파일들을 설치하지 않습니다: " + ", ".join(release["operator_files"][:10]),
                             once=True))
    return found


def database_findings(conn, now):
    found = []
    day = now.astimezone(KST).date().isoformat()
    for row in conn.execute("""SELECT id,day,kind,state,error FROM brief_editions WHERE publish
            AND state IN ('missed','blocked') AND due_at>now()-interval '24 hours'""").fetchall():
        found.append(finding(f"brief:{row['id']}:{row['state']}", "event", "critical",
                             f"{edition_name(str(row['day']), row['kind'])} 브리핑 "
                             + ("마감 놓침" if row["state"] == "missed" else "막힘"), f"사유: {row['error'] or '기록 없음'}"))
    for row in conn.execute("""SELECT id,day,kind,state,due_at FROM brief_editions WHERE publish
            AND state = ANY(%s) AND due_at<now()-interval '10 minutes' AND expires_at>now()""",
                            (list(BRIEF_LIVE),)).fetchall():
        found.append(finding(f"brief_late:{row['id']}", "event", "critical",
                             f"{edition_name(str(row['day']), row['kind'])} 브리핑 마감 지남",
                             f"마감 {kst(row['due_at'])} KST · 현재 단계 {row['state']}"))
    for row in conn.execute("""SELECT j.id,j.version,j.state,j.error,j.publish_deadline,e.day,e.kind FROM video_jobs j
            JOIN brief_editions e ON e.id=j.edition_id WHERE j.updated_at>now()-interval '24 hours'
            AND (j.state = ANY(%s) OR (j.state = ANY(%s) AND j.publish_deadline<now()))""",
                            (list(VIDEO_BAD), sorted(VIDEO_LIVE))).fetchall():
        late = row["state"] not in VIDEO_BAD
        found.append(finding(f"video_late:{row['id']}" if late else f"video:{row['id']}:{row['state']}", "event", "critical",
                             f"{edition_name(str(row['day']), row['kind'])} 영상 v{row['version']} "
                             + ("게시 마감 지남" if late else row["state"]),
                             f"게시 마감 {kst(row['publish_deadline'])} KST · 상태 {row['state']}",
                             *([f"사유: {row['error'][:200]}"] if row["error"] else [])))
    for row in conn.execute("""SELECT o.status,COALESCE(o.error,'') AS error,count(*) AS n,
            array_agg(DISTINCT m.kind) AS kinds FROM outbox o JOIN messages m ON m.id=o.id
            WHERE o.status IN ('uncertain','blocked') AND o.created_at>now()-interval '6 hours'
            GROUP BY o.status,o.error""").fetchall():
        found.append(finding(f"outbox:{row['status']}:{row['error']}:{day}", "event", "warning",
                             f"Slack 발송 {'결과 불명' if row['status'] == 'uncertain' else '막힘'} {row['n']}건",
                             f"사유 {row['error'] or '미상'} · 종류 {', '.join(sorted(row['kinds']))}",
                             "결과 불명 메시지는 자동으로 다시 보내지 않습니다."))
    backlog = conn.execute("""SELECT count(*) AS n,min(next_at) AS oldest FROM outbox
        WHERE status='pending' AND next_at<now()-interval '10 minutes'""").fetchone()
    if backlog["n"]:
        found.append(finding("outbox_backlog", "condition", "warning", f"Slack 발송 대기 {backlog['n']}건 밀림",
                             f"가장 오래된 예정 시각 {kst(backlog['oldest'], True)} KST"))
    for row in conn.execute("""SELECT COALESCE(error,'') AS error,count(*) AS n FROM turns
            WHERE status='blocked' AND updated_at>now()-interval '6 hours' GROUP BY error""").fetchall():
        if not any(marker in row["error"] for marker in EXPECTED_TURN_ERRORS):
            found.append(finding(f"turns:{row['error'][:80]}:{day}", "event", "warning",
                                 f"모델 작업 막힘 {row['n']}건", f"사유: {row['error'] or '미상'}"))
    pause = conn.execute("SELECT paused_until,reason FROM runtime_control WHERE id=1").fetchone()
    if pause and pause["paused_until"] and pause["paused_until"] > now:
        found.append(finding("model_pause", "condition", "warning", "모델 작업 일시정지",
                             f"사유 {pause['reason']} · {kst(pause['paused_until'], True)} KST까지"))
    for row in conn.execute("""SELECT id,state,error FROM research_jobs WHERE state IN ('failed','uncertain')
            AND updated_at>now()-interval '24 hours'""").fetchall():
        found.append(finding(f"research:{row['id']}:{row['state']}", "event", "warning",
                             f"연구 실행 {row['state']}", f"작업 {str(row['id'])[:8]} · 사유 {(row['error'] or '미상')[:200]}"))
    stale = conn.execute("""SELECT count(*) AS n,min(heartbeat_at) AS oldest FROM research_jobs
        WHERE state IN ('claimed','running') AND COALESCE(heartbeat_at,claimed_at)<now()-interval '10 minutes'""").fetchone()
    if stale["n"]:
        found.append(finding("research_worker", "condition", "warning", "연구 워커 응답 없음",
                             f"진행 중 작업 {stale['n']}건의 마지막 신호 {kst(stale['oldest'], True)} KST"))
    for row in conn.execute("SELECT profile FROM model_accounts WHERE authentication='needs_login'").fetchall():
        found.append(finding("account:" + row["profile"], "condition", "warning", f"모델 계정 {row['profile']} 로그인 필요"))
    if conn.execute("SELECT to_regclass('maintenance_control') AS name").fetchone()["name"]:
        runtime = conn.execute("SELECT to_jsonb(c)->'runtime' AS runtime FROM maintenance_control c WHERE id=1").fetchone()
        runtime = (runtime or {}).get("runtime") or {}
        beat = stamp(runtime.get("heartbeat_at"))
        limit = timedelta(seconds=max(1200, (runtime.get("poll_seconds") or 300) * 3))
        if runtime.get("enabled") and beat and now - beat > limit:
            found.append(finding("maintenance", "condition", "warning", "개선봇(maintenance) 신호 끊김",
                                 f"마지막 신호 {kst(beat, True)} KST"))
    return found


def root_text(item, first_seen):
    icon = "🔴" if item["severity"] == "critical" else "🟠"
    return "\n".join([f"{icon} *{item['title']}*", *item["lines"],
                      f"처음 확인 {kst(first_seen, True)} KST · 이 스레드에 질문하면 Director가 현재 상태를 확인해 답합니다."])


class OpsWatch:
    def __init__(self, company):
        self.company = company
        self.settings = company.settings
        self.next_at = 0.0

    def tick_if_due(self):
        if time.monotonic() < self.next_at:
            return None
        self.next_at = time.monotonic() + TICK_SECONDS
        return self.tick()

    def tick(self, now=None):
        now = now or utcnow()
        with self.company.db.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            snapshot = conn.execute("SELECT collected_at,payload FROM ops_host_snapshots WHERE id=1").fetchone()
            current = {}
            for item in host_findings(snapshot, now) + database_findings(conn, now):
                current.setdefault(item["key"], item)
            summary = self.reconcile(conn, current, now)
            conn.execute("""INSERT INTO ops_watch_state(id,ticked_at,summary) VALUES (1,%s,%s)
                ON CONFLICT (id) DO UPDATE SET ticked_at=EXCLUDED.ticked_at,summary=EXCLUDED.summary""",
                         (now, Jsonb(summary)))
            return summary

    def reconcile(self, conn, current, now):
        rows = {row["key"]: row for row in conn.execute(
            "SELECT * FROM ops_incidents WHERE state<>'closed' ORDER BY first_seen FOR UPDATE").fetchall()}
        announced, closed = 0, 0
        for key, item in current.items():
            row = rows.get(key)
            if not row:
                if item.get("once") and conn.execute("SELECT 1 FROM ops_incidents WHERE key=%s", (key,)).fetchone():
                    continue  # Already reported for this action and commit (docs/adr/0045 §8).
                episode = conn.execute("SELECT COALESCE(max(episode),0)+1 AS n FROM ops_incidents WHERE key=%s",
                                       (key,)).fetchone()["n"]
                row = conn.execute("""INSERT INTO ops_incidents(id,key,episode,kind,severity,title,detail,first_seen,last_seen)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                                   (stable(f"ops-incident:{key}:{episode}"), key, episode, item["kind"], item["severity"],
                                    item["title"], Jsonb(item), now, now)).fetchone()
            else:
                severity = "critical" if "critical" in (row["severity"], item["severity"]) else "warning"
                row = conn.execute("""UPDATE ops_incidents SET sightings=sightings+1,clear_ticks=0,last_seen=%s,
                    severity=%s,title=%s,detail=%s WHERE id=%s RETURNING *""",
                                   (now, severity, item["title"], Jsonb(item), row["id"])).fetchone()
            if row["state"] == "pending" and row["sightings"] >= CONFIRM[row["severity"]]:
                if announced >= MAX_ROOTS_PER_TICK:
                    continue  # Remaining alerts open on the next tick instead of flooding the channel.
                self.open(conn, row, item)
                announced += 1
            elif row["state"] == "active" and row["severity"] == "critical" and row["announced_severity"] == "warning":
                self.reply(conn, row, "escalation", "🔴 *심각도 상승*\n" + "\n".join([item["title"], *item["lines"]]))
                conn.execute("UPDATE ops_incidents SET announced_severity='critical' WHERE id=%s", (row["id"],))
        for key, row in rows.items():
            if key in current:
                continue
            if row["state"] == "pending":
                conn.execute("UPDATE ops_incidents SET state='closed',closed_at=%s WHERE id=%s", (now, row["id"]))
                continue
            ticks = row["clear_ticks"] + 1
            if ticks < CLEAR_TICKS:
                conn.execute("UPDATE ops_incidents SET clear_ticks=%s WHERE id=%s", (ticks, row["id"]))
                continue
            conn.execute("UPDATE ops_incidents SET state='closed',clear_ticks=%s,closed_at=%s WHERE id=%s",
                         (ticks, now, row["id"]))
            closed += 1
            # An event (a missed edition, an uncertain send) is reported once; leaving the lookback is not recovery.
            if row["kind"] == "condition":
                self.reply(conn, row, "recovery", f"✅ *해소* · {kst(now, True)} KST 확인\n{row['title']}")
        return as_json({"findings": sorted(current), "announced": announced, "closed": closed})

    def open(self, conn, row, item):
        settings = self.settings
        project = conn.execute("""INSERT INTO projects(id,title,instruction,owner_user,channel)
            VALUES (%s,%s,'Read-only ops alert. Changes to services require a separately authorized task.',%s,%s)
            ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title RETURNING *""",
                               (stable(f"ops-project:{row['id']}"), row["title"][:120], settings.ops_watch_owner_user,
                                settings.ops_alert_channel_id)).fetchone()
        message_id = stable(f"ops-message:{row['id']}:root")
        if not conn.execute("SELECT 1 FROM messages WHERE id=%s", (message_id,)).fetchone():
            self.company._message(conn, project, None, "director", "ops_alert", root_text(item, row["first_seen"]),
                                  message_id=message_id, notify_owner=row["severity"] == "critical")
            conn.execute("INSERT INTO ops_publications(id,incident_id,kind,expires_at) VALUES (%s,%s,'root',%s)",
                         (message_id, row["id"], utcnow() + timedelta(hours=PUBLICATION_HOURS)))
        conn.execute("""UPDATE ops_incidents SET state='active',project_id=%s,root_message_id=%s,announced_severity=%s
            WHERE id=%s""", (project["id"], message_id, row["severity"], row["id"]))

    def reply(self, conn, row, kind, text):
        """A thread reply needs a delivered root; an undelivered or ambiguous root is never chased."""
        root = conn.execute("SELECT status,sent_ts FROM outbox WHERE id=%s", (row["root_message_id"],)).fetchone()
        if not root or root["status"] != "delivered" or not root["sent_ts"]:
            return None
        message_id = stable(f"ops-message:{row['id']}:{kind}:{row['sightings']}")
        if conn.execute("SELECT 1 FROM messages WHERE id=%s", (message_id,)).fetchone():
            return message_id
        project = conn.execute("SELECT * FROM projects WHERE id=%s", (row["project_id"],)).fetchone()
        project["thread_ts"] = root["sent_ts"]
        self.company._message(conn, project, None, "director", "ops_alert", text, message_id=message_id)
        conn.execute("INSERT INTO ops_publications(id,incident_id,kind,expires_at) VALUES (%s,%s,%s,%s)",
                     (message_id, row["id"], kind, utcnow() + timedelta(hours=PUBLICATION_HOURS)))
        return message_id


def gate(conn, company, row):
    settings = company.settings
    publication = conn.execute("SELECT * FROM ops_publications WHERE id=%s", (row["id"],)).fetchone()
    project = conn.execute("SELECT * FROM projects WHERE id=%s", (row["project_id"],)).fetchone()
    permitted = bool(publication and project and settings.ops_watch_enabled and publication["expires_at"] > utcnow()
                     and row["agent"] == "director" and row["channel"] == settings.ops_alert_channel_id
                     and len(row["text"]) <= 3000 and project["owner_user"] == settings.ops_watch_owner_user
                     and project["channel"] == row["channel"] and project["revision"] == row["revision"])
    if permitted and publication["kind"] != "root":
        incident = conn.execute("SELECT root_message_id FROM ops_incidents WHERE id=%s",
                                (publication["incident_id"],)).fetchone()
        root = conn.execute("SELECT status,sent_ts FROM outbox WHERE id=%s", (incident["root_message_id"],)).fetchone()
        permitted = bool(root and root["status"] == "delivered" and root["sent_ts"] and row["thread_ts"] == root["sent_ts"])
    if not permitted:
        conn.execute("UPDATE outbox SET status='stale',error='ops_alert_policy_changed' WHERE id=%s", (row["id"],))
    return permitted
