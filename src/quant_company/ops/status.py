"""Compact live state for owner progress questions. Facts from PostgreSQL and the host probe only."""

import json
from datetime import UTC, datetime, timedelta, timezone

LIMIT = 6000
SNAPSHOT_STALE = timedelta(minutes=3)
RECENT_START = timedelta(minutes=30)
KST = timezone(timedelta(hours=9))

BRIEF_STATES = {
    "collecting": "📥 자료 수집 중", "planning": "🧭 계획 중", "inventorying": "📚 자료 정리 중",
    "writing": "📝 작성 중", "reviewing": "🔍 검토 중", "revising": "🔧 수정 중",
    "final_reviewing": "🏁 최종 검토 중", "ready": "📮 발송 대기", "committed": "✅ 발송 완료",
    "previewed": "👀 미리보기 발송", "missed": "❌ 마감 놓침", "blocked": "🚧 막힘",
    "uncertain": "❓ 결과 확인 필요", "held": "✋ 보류", "stale": "➖ 만료됨",
    "expired": "⌛ 마감 지남", "superseded": "➖ 대체됨",
}
VIDEO_STATES = {
    "queued": "⏳ 시작 대기", "reviewing": "📝 기획 검토 중", "synthesizing": "🔊 음성 합성 중",
    "rendering": "🎥 렌더링 중", "inspecting": "🔍 영상 검수 중", "uploading": "📤 업로드 중",
    "delivering": "📦 파일 전달 중", "awaiting_approval": "🙋 승인 대기", "approved": "👍 승인됨 · 게시 대기",
    "published": "📺 유튜브 게시 완료", "delivered": "✅ 파일 전달 완료", "held": "✋ 보류",
    "superseded": "➖ 새 버전으로 대체", "expired": "⌛ 게시 마감 지남", "blocked": "🚧 막힘",
    "uncertain": "❓ 결과 확인 필요", "rejected": "❌ 반려됨", "delivery_failed": "❌ 파일 전달 실패",
    "delivery_uncertain": "❓ 전달 결과 확인 필요",
}
VIDEO_LIVE = {"queued", "reviewing", "synthesizing", "rendering", "inspecting", "uploading", "delivering", "approved"}
# A login check or an auth fault sets needs_login and only a later login check clears it, so a successful call after
# both means the account works again (2026-10-15: primary still carried a 09-28 check while it answered calls).
LOGIN_NEEDED = """authentication='needs_login'
    AND (last_fault='auth' OR last_success_at IS NULL OR checked_at>=last_success_at)"""
API_PAUSES = {"key": "키 문제", "spend_limit": "Console 지출 한도", "uncertain": "결과 불확실(30분 쉼)",
              "ledger_unreadable": "장부 읽기 실패", "rate_limit": "요청 한도", "overloaded": "서버 과부하"}
RESEARCH_ACTIVE = ("queued", "claimed", "running", "cancel_requested", "received", "awaiting_audit")
NOTES = {
    "times": "UTC ISO 8601. Convert to KST (+09:00) when answering.",
    "freshness": "host.stale=true or a missing host snapshot means container/host facts are unknown, not healthy.",
    "scope": "Read-only facts. This tool cannot restart, retry or change anything; say so if asked to act.",
}


def utcnow():
    return datetime.now(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat(timespec="seconds") if isinstance(value, datetime) else value


def clip(text, limit=160):
    return text if text is None or len(text) <= limit else text[:limit - 1] + "…"


def stamp(value):
    return datetime.fromisoformat(value) if isinstance(value, str) and value[:4].isdigit() else None


def attention(item, now):
    """Why one container needs a look, or None. Uses only probe facts."""
    reasons = []
    if item.get("status") != "running":
        reasons.append(f"{item.get('status')} (exit {item.get('exit_code')})")
    if item.get("health") == "unhealthy":
        reasons.append("unhealthy")
    if item.get("oom_killed"):
        reasons.append("OOMKilled")
    started = stamp(item.get("started_at"))
    if item.get("restart_count") and started and now - started < RECENT_START:
        reasons.append(f"auto-restarted {item['restart_count']}x")
    logs = item.get("logs") or {}
    if logs.get("tracebacks"):
        reasons.append(f"{logs['tracebacks']} tracebacks/2m " + ",".join(logs.get("exceptions") or []))
    return "; ".join(reasons) or None


def host(conn, now=None):
    now = now or utcnow()
    row = conn.execute("SELECT collected_at,payload FROM ops_host_snapshots WHERE id=1").fetchone()
    if not row:
        return {"available": False, "reason": "host_probe_not_reporting"}
    payload, age = row["payload"], now - row["collected_at"]
    containers = payload.get("containers") or []
    window = payload.get("deploy_window")
    value = {
        "available": True, "collected_at": iso(row["collected_at"]), "age_seconds": int(age.total_seconds()),
        "stale": age > SNAPSHOT_STALE, "host": payload.get("host"),
        "containers": {
            "running": sum(item.get("status") == "running" for item in containers), "total": len(containers),
            "attention": [{"service": item.get("service"), "why": why} for item in containers
                          if (why := attention(item, now))][:8],
            "recently_started": sorted(item.get("service") for item in containers
                                       if (started := stamp(item.get("started_at"))) and now - started < RECENT_START),
        },
        "config_drift": {key: [group["services"] for group in groups] for key, groups in (payload.get("drift") or {}).items()},
        "units": {name: unit.get("result") for name, unit in (payload.get("units") or {}).items()},
        "probe_errors": payload.get("probe_errors") or [],
    }
    if window and stamp(window.get("until")) and stamp(window["until"]) > now:
        value["deploy_window"] = window
    if release := payload.get("release"):
        value["release"] = {key: release.get(key) for key in ("checked_at", "mode", "action", "current", "target",
                                                               "active", "behind_by", "detail")}
    if api := payload.get("claude_api"):
        value["claude_api"] = {key: api.get(key) for key in ("state", "spent_31d_usd", "cap_usd", "paused")}
    return value


def briefings(conn, where="TRUE", params=(), limit=4):
    rows = conn.execute(f"""SELECT e.id,e.day,e.kind,e.state,e.due_at,e.committed_at,e.error,e.publish,
        COALESCE((SELECT json_agg(json_build_object('phase',c.phase,'state',c.state) ORDER BY c.phase)
                  FROM brief_calls c WHERE c.edition_id=e.id),'[]') AS calls
        FROM brief_editions e WHERE {where} ORDER BY e.due_at DESC LIMIT %s""", (*params, limit)).fetchall()
    return [{"id": str(row["id"]), "day": str(row["day"]), "kind": row["kind"], "state": row["state"],
             "publish": row["publish"], "due_at": iso(row["due_at"]), "committed_at": iso(row["committed_at"]),
             "error": clip(row["error"]), "phases": {call["phase"]: call["state"] for call in row["calls"]}}
            for row in rows]


def videos(conn, where="TRUE", params=(), limit=4):
    rows = conn.execute(f"""SELECT j.id,e.day,e.kind,j.version,j.state,j.error,j.updated_at,j.publish_deadline,
        j.youtube_id IS NOT NULL AS uploaded, j.lease_until,
        COALESCE((SELECT json_object_agg(f.kind||':'||f.state,f.n) FROM
                  (SELECT kind,state,count(*) AS n FROM video_effects WHERE job_id=j.id GROUP BY kind,state) f),'{{}}')
                  AS effects
        FROM video_jobs j JOIN brief_editions e ON e.id=j.edition_id WHERE {where}
        ORDER BY j.created_at DESC LIMIT %s""", (*params, limit)).fetchall()
    return [{"day": str(row["day"]), "kind": row["kind"], "version": row["version"], "state": row["state"],
             "error": clip(row["error"]), "updated_at": iso(row["updated_at"]),
             "publish_deadline": iso(row["publish_deadline"]), "uploaded": row["uploaded"],
             "effects": row["effects"]} for row in rows]


def thread(conn, project):
    """The briefing edition behind this Slack thread, when there is one, with all its video versions."""
    if not project:
        return None
    editions = briefings(conn, "e.project_id=%s", (project["id"],), limit=1)
    if not editions:
        return None
    return {"briefing": editions[0], "videos": videos(conn, "e.id=%s", (editions[0]["id"],), limit=3)}


def research(conn):
    rows = conn.execute("""SELECT state,count(*) AS n,max(heartbeat_at) AS heartbeat FROM research_jobs
        WHERE state = ANY(%s) GROUP BY state""", (list(RESEARCH_ACTIVE),)).fetchall()
    worker = conn.execute("SELECT last_seen FROM research_workers WHERE id='worker'").fetchone()
    recent = conn.execute("""SELECT state,count(*) AS n FROM research_jobs WHERE state IN ('failed','uncertain')
        AND updated_at>now()-interval '24 hours' GROUP BY state""").fetchall()
    return {"active": {row["state"]: row["n"] for row in rows},
            "last_heartbeat": iso(max((row["heartbeat"] for row in rows if row["heartbeat"]), default=None)),
            "worker_last_seen": iso(worker["last_seen"]) if worker else None,
            "failed_or_uncertain_24h": {row["state"]: row["n"] for row in recent}}


def delivery(conn):
    rows = conn.execute("""SELECT status,COALESCE(error,'') AS error,count(*) AS n FROM outbox
        WHERE status IN ('uncertain','blocked') AND created_at>now()-interval '6 hours'
        GROUP BY status,error ORDER BY n DESC LIMIT 6""").fetchall()
    pending = conn.execute("""SELECT count(*) AS n,min(next_at) AS oldest FROM outbox
        WHERE status='pending' AND next_at<now()-interval '10 minutes'""").fetchone()
    return {"problems_6h": [{"status": row["status"], "error": clip(row["error"], 60), "count": row["n"]} for row in rows],
            "overdue_pending": pending["n"], "oldest_overdue_since": iso(pending["oldest"])}


def turns(conn):
    rows = conn.execute("""SELECT status,count(*) AS n FROM turns
        WHERE status IN ('queued','running','waiting') GROUP BY status""").fetchall()
    blocked = conn.execute("""SELECT COALESCE(error,'') AS error,count(*) AS n FROM turns
        WHERE status='blocked' AND updated_at>now()-interval '6 hours' GROUP BY error ORDER BY n DESC LIMIT 4""").fetchall()
    pause = conn.execute("SELECT paused_until,reason FROM runtime_control WHERE id=1").fetchone()
    paused = pause and pause["paused_until"] and pause["paused_until"] > utcnow()
    return {"open": {row["status"]: row["n"] for row in rows},
            "blocked_6h": [{"error": clip(row["error"], 80), "count": row["n"]} for row in blocked],
            "model_pause": {"until": iso(pause["paused_until"]), "reason": clip(pause["reason"], 80)} if paused else None}


def accounts(conn):
    rows = conn.execute(f"""SELECT profile,({LOGIN_NEEDED}) AS login_needed,
        CASE WHEN paused_until>now() THEN paused_until END AS paused_until,last_fault FROM model_accounts
        WHERE ({LOGIN_NEEDED}) OR paused_until>now() ORDER BY profile""").fetchall()
    return [{"profile": row["profile"], "login_needed": row["login_needed"], "paused_until": iso(row["paused_until"]),
             "fault": row["last_fault"]} for row in rows]


def incidents(conn):
    rows = conn.execute("""SELECT severity,title,first_seen FROM ops_incidents WHERE state='active'
        ORDER BY severity='critical' DESC,first_seen LIMIT 6""").fetchall()
    return [{"severity": row["severity"], "title": row["title"], "since": iso(row["first_seen"])} for row in rows]


def live_status(conn, project=None, now=None):
    now = now or utcnow()
    value = {"checked_at": iso(now), "host": host(conn, now), "this_thread": thread(conn, project),
             "briefings": briefings(conn), "videos": videos(conn), "research": research(conn),
             "slack_delivery": delivery(conn), "model_turns": turns(conn), "model_accounts": accounts(conn),
             "open_alerts": incidents(conn), "notes": NOTES}
    # Recent history shrinks first; the asked-about thread and the host facts stay whole.
    for key in ("briefings", "videos"):
        while len(json.dumps(value, ensure_ascii=False, default=str)) > LIMIT and len(value[key]) > 1:
            value[key].pop()
    for item in value["briefings"]:
        if len(json.dumps(value, ensure_ascii=False, default=str)) <= LIMIT:
            break
        item.pop("phases", None)
    return value


def kst(value, with_day=False):
    moment = stamp(value) if isinstance(value, str) else value
    if not moment:
        return "-"
    local = moment.astimezone(KST)
    return local.strftime("%m/%d %H:%M" if with_day else "%H:%M")


def edition_name(day, kind):
    return f"{int(day[5:7])}/{int(day[8:10])} {'아침' if kind == 'am' else '저녁'}"


def status_text(value):
    """Deterministic Korean summary for '상태' in the ops channel; works without a model."""
    lines = [f"*서버 현황* · {kst(value['checked_at'], True)} KST 기준"]
    facts = value["host"]
    if not facts.get("available"):
        lines.append("🖥️ 호스트 수집기 기록이 없습니다. 컨테이너 상태는 알 수 없습니다.")
    else:
        machine = facts.get("host") or {}
        containers = facts["containers"]
        freshness = f"{facts['age_seconds']}초 전 수집" + (" · ⚠️ 오래된 기록" if facts["stale"] else "")
        lines.append(f"🖥️ 컨테이너 {containers['running']}/{containers['total']} 실행 · "
                     f"디스크 여유 {machine.get('disk_free_gb', '?')}GB · "
                     f"메모리 여유 {machine.get('mem_available_mb', '?')}MB ({freshness})")
        if facts.get("deploy_window"):
            window = facts["deploy_window"]
            lines.append(f"🔧 배포 중: {', '.join(window['services'])} · {kst(window['until'])}까지")
        for item in containers["attention"]:
            lines.append(f"⚠️ {item['service']}: {item['why']}")
        for key, groups in facts["config_drift"].items():
            lines.append(f"⚠️ 환경값 불일치 {key}: " + " / ".join(",".join(group) for group in groups))
        if release := facts.get("release"):
            active, current = release.get("active"), release.get("current")
            if active and current and active != current and release.get("action") != "releasing":
                lines.append(f"🚀 릴리스 {active[:7]} · 마지막 점검({kst(release.get('checked_at'))})은 이전 {current[:7]} 기준,"
                             " 다음 점검 대기")
            else:
                behind = f" · main보다 {release['behind_by']}커밋 뒤" if release.get("behind_by") else ""
                lines.append(f"🚀 릴리스 {(current or '?')[:7]}{behind} · 판단 {release.get('action') or '?'}"
                             f" ({release.get('mode') or '?'}, {kst(release.get('checked_at'))} 점검)")
    if api := facts.get("claude_api"):
        state = api.get("state")
        reason = (api.get("paused") or {}).get("reason") or ("ledger_unreadable" if state == "ledger_unreadable" else None)
        label = ("꺼짐" if state == "off" else f"쉼({API_PAUSES.get(reason, reason)})" if reason else "켜짐")
        lines.append(f"🤖 Claude API: 31일 ${api.get('spent_31d_usd') or 0:.2f} / ${api.get('cap_usd') or 0:.0f} · {label}")
    for item in value["briefings"][:2]:
        label = BRIEF_STATES.get(item["state"], item["state"])
        done = f" {kst(item['committed_at'])}" if item["committed_at"] else f" · 마감 {kst(item['due_at'])}"
        lines.append(f"📰 {edition_name(item['day'], item['kind'])} 브리핑: {label}{done}")
    for item in value["videos"][:2]:
        label = VIDEO_STATES.get(item["state"], item["state"])
        due = f" · 게시 마감 {kst(item['publish_deadline'])}" if item["state"] in VIDEO_LIVE else ""
        error = f" · {item['error']}" if item["error"] else ""
        lines.append(f"🎬 {edition_name(item['day'], item['kind'])} 영상 v{item['version']}: {label}{due}{error}")
    active = value["research"]["active"]
    if active:
        lines.append("🔬 연구 실행: " + ", ".join(f"{state} {n}건" for state, n in active.items())
                     + f" · 워커 마지막 접속 {kst(value['research']['worker_last_seen'])}")
    delivery_value = value["slack_delivery"]
    problems = sum(item["count"] for item in delivery_value["problems_6h"])
    if problems or delivery_value["overdue_pending"]:
        lines.append(f"📮 Slack 발송: 확인 필요·막힘 {problems}건(6시간) · 밀린 대기 {delivery_value['overdue_pending']}건")
    if value["model_turns"]["model_pause"]:
        pause = value["model_turns"]["model_pause"]
        lines.append(f"⏸️ 모델 작업 일시정지: {pause['reason']} · {kst(pause['until'])}까지")
    for account in value["model_accounts"]:
        states = [*(["로그인 필요"] if account["login_needed"] else []),
                  *([f"한도 대기 · {kst(account['paused_until'])}까지"] if account["paused_until"] else [])]
        lines.append(f"🔑 모델 계정 {account['profile']}: {' · '.join(states)}")
    alerts = value["open_alerts"]
    lines.append(f"🚨 열린 경고 {len(alerts)}건" + ("".join(f"\n  • {item['title']}" for item in alerts) if alerts else ""))
    return "\n".join(lines)


def thread_text(value):
    """Deterministic '상태' answer inside a briefing thread: this edition and its videos first."""
    current = value.get("this_thread")
    if not current:
        return status_text(value)
    edition = current["briefing"]
    name = edition_name(edition["day"], edition["kind"])
    label = BRIEF_STATES.get(edition["state"], edition["state"])
    done = f" {kst(edition['committed_at'])}" if edition["committed_at"] else f" · 마감 {kst(edition['due_at'])}"
    lines = [f"*{name} 진행 현황* · {kst(value['checked_at'], True)} KST 기준", f"📰 브리핑: {label}{done}"]
    if not current["videos"]:
        lines.append("🎬 영상: 아직 제작 작업이 없습니다.")
    for item in current["videos"]:
        state = VIDEO_STATES.get(item["state"], item["state"])
        due = f" · 게시 마감 {kst(item['publish_deadline'])}" if item["state"] in VIDEO_LIVE else ""
        error = f" · {item['error']}" if item["error"] else ""
        lines.append(f"🎬 영상 v{item['version']}: {state}{due} · 갱신 {kst(item['updated_at'])}{error}")
    facts = value["host"]
    if not facts.get("available") or facts["stale"]:
        lines.append("🖥️ 서버 수집 기록이 없거나 오래되어 컨테이너 상태는 확인하지 못했습니다.")
    else:
        broken = [item for item in facts["containers"]["attention"]
                  if item["service"] in {"video-worker", "news-worker", "dispatch", "claude-runtime", "codex-runtime"}]
        lines.append("🖥️ 영상·브리핑 관련 서버: " + ("정상 실행 중" if not broken else
                     " / ".join(f"{item['service']} {item['why']}" for item in broken)))
    return "\n".join(lines)
