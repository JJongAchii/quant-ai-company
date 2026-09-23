"""Server-rendered summaries and one receipt-bound thread per data incident."""

from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from ..company import stable
from .store import utcnow

KST = ZoneInfo("Asia/Seoul")
SUMMARY_FORMAT_VERSION = 2
PROBLEM_TEXT = {
    "parquet_footer_limit": "Parquet 메타데이터가 2 MiB 검사 한도를 초과해 확인하지 못함",
    "descriptor_unavailable_or_changed": "메타데이터 확인 실패 또는 조회 중 객체 변경",
    "lake_catalog_unavailable": "전체 목록 조회 실패",
    "dataset_missing": "이전 목록에 있던 데이터셋이 현재 목록에 없음",
    "empty_dataset": "데이터 행이 0개",
    "data_late": "등록된 최신성 기준보다 늦음",
    "input_quality_failed": "고정 입력의 필수 값 또는 키 검사에서 이상 발견",
    "input_identity_changed": "승인된 고정 입력의 해시가 일치하지 않음",
    "input_unavailable": "승인된 고정 입력 파일에 접근할 수 없음",
    "read_limit": "고정 입력이 상세 검사 한도를 초과함",
    "reader_unavailable": "상세 검사 도구를 사용할 수 없음",
    "reader_timeout": "상세 검사가 제한 시간 안에 끝나지 않음",
    "reader_failed": "상세 검사 도중 읽기 실패",
    "checker_busy": "다른 상세 검사가 진행 중",
    "scope_not_registered": "검사 범위가 등록된 승인 입력과 일치하지 않음",
}


def kst(value):
    if not value:
        return "미확인"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if not value.tzinfo:
        return "미확인"
    return value.astimezone(KST).strftime("%m/%d %H:%M KST")


def problem_text(code):
    return PROBLEM_TEXT.get(code, "검사에서 확인 필요") if code else "재검사에서 이전 오류가 확인되지 않음"


def status_text(snapshot):
    inventory = snapshot["inventory"]
    datasets = snapshot["datasets"]
    counts = Counter(row["freshness"]["state"] for row in datasets)
    checked = sum(row["inspection"] == "metadata_checked" for row in datasets)
    problems = [row for row in datasets if row["problem"]]
    lines = [
        f"*데이터 현황* · {kst(snapshot['checked_at'])}",
        f"• 최신 레이크: {len(datasets)}개 중 메타데이터 확인 {checked}개, 미확인 {len(datasets) - checked}개",
    ]
    if not inventory or not inventory["receipt"].get("ok"):
        lines.append("• 전체 목록 조회 실패 또는 미검사. 아래 개수는 마지막으로 저장된 목록입니다.")
    if counts["unregistered"]:
        lines.append(f"• 최신성 기준 미등록 {counts['unregistered']}개 — 데이터가 늦었다는 판정이 아닙니다.")
    if counts["stale"]:
        lines.append(f"• 등록 기준보다 늦은 데이터 {counts['stale']}개")
    if problems:
        lines.append("*확인 필요*")
        lines += [f"• {row['dataset']}: {problem_text(row['problem'])}" for row in problems[:5]]
        if len(problems) > 5:
            lines.append(f"• 그 외 {len(problems) - 5}개는 운영 상태 API에서 확인")
        if any(row["problem"] in {"parquet_footer_limit", "descriptor_unavailable_or_changed"} for row in problems):
            lines.append("검사 실패만으로 원본 손상이나 데이터 지연을 단정하지 않습니다.")
    core = snapshot["core_checks"]
    if not core:
        lines.append("• 고정 연구 입력 6개: 아직 미검사. 현재 레이크와 별도입니다.")
    else:
        complete = sum(row["state"] == "complete" and not row["problem"] for row in core)
        lines.append(f"• 고정 연구 입력: {len(core)}건 중 전체 입력 검사 완료 {complete}건")
        lines += [f"  ◦ {row['title'][:60]}: {problem_text(row['problem'])}" for row in core if row["problem"]][:2]
    lines.append(f"다음 목록 확인 {kst(snapshot['next_inventory_at'])} · 전체 이름은 채널에 `목록`으로 조회")
    return "\n".join(lines)


def list_text(snapshot):
    names = [("⚠ " if row["problem"] else "") + row["dataset"] for row in snapshot["datasets"]]
    lines = [f"*최신 레이크 데이터셋 {len(names)}개* · {kst(snapshot['checked_at'])}"]
    line = ""
    shown = 0
    for name in names:
        addition = (" · " if line else "") + name
        if len("\n".join(lines)) + len(line) + len(addition) > 2600:
            break
        if len(line) + len(addition) > 100:
            lines.append(line)
            line = name
        else:
            line += addition
        shown += 1
    if line:
        lines.append(line)
    if shown < len(names):
        lines.append(f"그 외 {len(names) - shown}개는 운영 상태 API에서 확인")
    lines.append("⚠는 점검 필요 항목입니다. 데이터 기준일·객체 정보는 운영 상태 API에서 확인합니다.")
    return "\n".join(lines)


def enqueue(conn, store, project, key, text, kind, *, incident_id=None, check_ids=()):
    identity = stable("data-watch-message:" + key)
    if conn.execute("SELECT 1 FROM messages WHERE id=%s", (identity,)).fetchone():
        return identity
    store.company._message(conn, project, None, "data", "data_watch", text, message_id=identity)
    conn.execute("""INSERT INTO data_watch_publications(id,policy,incident_id,kind,expires_at,check_ids)
        VALUES(%s,%s,%s,%s,%s,%s)""", (identity, store.policy(), incident_id, kind,
        utcnow() + timedelta(days=1 if kind == "summary" else 7), Jsonb(list(check_ids))))
    return identity


def incident_text(key, state, detail, last_healthy):
    problem = detail.get("problem")
    lines = [f"*데이터 {'복구 확인' if state == 'recovered' else '점검 필요'}* · {key}",
             f"무슨 일인가요? {problem_text(problem)}."]
    if problem in {"parquet_footer_limit", "descriptor_unavailable_or_changed"}:
        lines.append("이 결과만으로 원본 데이터 손상이나 최신성 지연을 뜻하지는 않습니다.")
    lines += [f"영향: {detail['impact']}", f"마지막 정상 확인: {kst(last_healthy)}",
              f"이번 검사: {kst(detail['checked_at'])} · 다음 점검: {kst(detail['next_check'])}",
              "상세 영수증은 운영 상태 API에 보관됩니다. 원본과 고정 연구 입력은 자동 변경하지 않습니다."]
    return "\n".join(lines)


def observe(conn, store, key, detail, *, verified, last_healthy=None):
    policy = store.policy()
    identity = stable(f"data-watch-incident:{policy}:{key}")
    row = conn.execute("SELECT * FROM data_watch_incidents WHERE id=%s", (identity,)).fetchone()
    bad = bool(detail["problem"])
    if not row and not bad:
        return 0
    if not bad and not verified:
        return 0  # A pending or revoked check is never recovery evidence.
    if (row and not bad and row["detail"].get("problem") == "data_late"
            and detail.get("freshness") != "fresh"):
        return 0  # Expiry of the calendar cannot prove that delayed data arrived.
    state = "active" if bad else "recovered"
    if not row:
        if not store.company.settings.data_watch_publish_enabled:
            return 0
        project = conn.execute("""INSERT INTO projects(id,title,instruction,owner_user,channel)
            VALUES(%s,%s,'Read-only data incident; upstream changes require a separately authorized task.',%s,%s)
            RETURNING *""", (stable(identity + ":project"), key[:300], store.company.settings.data_watch_owner_user,
            store.company.settings.data_watch_channel_id)).fetchone()
        root_id = stable("data-watch-message:" + identity + ":root")
        conn.execute("""INSERT INTO data_watch_incidents
            (id,issue_key,policy,project_id,root_message_id,state,detail,last_healthy_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""", (identity, policy + ":" + key, policy, project["id"], root_id,
            state, Jsonb(detail), last_healthy))
        enqueue(conn, store, project, identity + ":root", incident_text(key, state, detail, last_healthy),
                "root", incident_id=identity)
        # The maintenance observer consumes this recorded failure once, after root delivery.
        store.company._event(conn, "data_watch_problem", {"incident_id": identity, **detail}, project["id"])
        return 1
    changed = state != row["state"] or detail["problem"] != row["detail"].get("problem")
    transition = row["transition"] + int(changed)
    conn.execute("""UPDATE data_watch_incidents SET state=%s,detail=%s,transition=%s,
        checked_at=%s,last_healthy_at=COALESCE(%s,last_healthy_at) WHERE id=%s""",
        (state, Jsonb(detail), transition, utcnow(), last_healthy, identity))
    if transition == row["announced_transition"] or not store.company.settings.data_watch_publish_enabled:
        return 0
    root = conn.execute("SELECT status,sent_ts FROM outbox WHERE id=%s", (row["root_message_id"],)).fetchone()
    if root["status"] != "delivered" or not root["sent_ts"]:
        return 0
    # Do not pile up later transitions after an ambiguous write in this thread.
    unresolved = conn.execute("""SELECT 1 FROM data_watch_publications p JOIN outbox o ON o.id=p.id
        WHERE p.incident_id=%s AND p.kind='transition' AND o.status<>'delivered' LIMIT 1""", (identity,)).fetchone()
    if unresolved:
        return 0
    project = conn.execute("SELECT * FROM projects WHERE id=%s", (row["project_id"],)).fetchone()
    project["thread_ts"] = root["sent_ts"]
    enqueue(conn, store, project, f"{identity}:transition:{transition}",
            incident_text(key, state, detail, last_healthy or row["last_healthy_at"]), "transition", incident_id=identity)
    conn.execute("UPDATE data_watch_incidents SET announced_transition=%s WHERE id=%s", (transition, identity))
    return 1


def refresh(conn, store, snapshot):
    count, inventory = 0, snapshot["inventory"]
    next_at = snapshot["next_inventory_at"]
    if inventory:
        ok = inventory["receipt"].get("ok") is True
        count += observe(conn, store, "전체 목록 접근", {
            "problem": None if ok else "lake_catalog_unavailable", "checked_at": inventory["checked_at"],
            "impact": "최신 레이크 전체 목록 확인 불가; 고정 연구 입력 영향 미확인",
            "evidence": "inventory:" + inventory["id"], "next_check": next_at}, verified=ok,
            last_healthy=inventory["checked_at"] if ok else None)
    for item in snapshot["datasets"]:
        verified = item["inspection"] == "metadata_checked"
        healthy = verified and not item["problem"] and item["freshness"]["state"] == "fresh"
        if healthy:
            conn.execute("UPDATE data_watch_datasets SET last_healthy_at=%s WHERE id=%s",
                         (item["described_at"], item["id"]))
        count += observe(conn, store, "최신 레이크 " + item["dataset"], {
            "problem": item["problem"], "checked_at": inventory["checked_at"] if item["problem"] == "dataset_missing"
            and inventory else item["described_at"] or item["observed_at"],
            "freshness": item["freshness"]["state"],
            "impact": item["dataset"] + "의 최신 입력 사용 전 확인 필요; 고정 연구 입력 영향은 별도 검사",
            "evidence": f"version:{item['version']} descriptor:{item['descriptor_receipt_id']}",
            "next_check": next_at}, verified=verified,
            last_healthy=item["described_at"] if healthy else item["last_healthy_at"])
    for item in snapshot["core_checks"]:
        verified = item["state"] == "complete" and item["receipt"] and item["receipt"]["state"] == "checked"
        count += observe(conn, store, "고정 입력 " + item["research_job_id"], {
            "problem": item["problem"], "checked_at": item["checked_at"],
            "research_job_id": item["research_job_id"], "check_id": item["id"],
            "impact": item["title"][:200] + " · " + (item["url"] or "연구 링크 미확인"),
            "evidence": f"check:{item['id']} scope:{item['scope_digest']} receipt:{item['receipt_digest']}",
            "next_check": "다음 일일 검사 / 등록 워커 연결"}, verified=verified,
            last_healthy=item["checked_at"] if verified and not item["problem"] else None)
    at = utcnow().astimezone(KST)
    if store.company.settings.data_watch_publish_enabled and at.hour >= 9:
        key = f"{store.policy()}:summary:v{SUMMARY_FORMAT_VERSION}:{at.date()}"
        project_id = stable("data-watch-project:" + key)
        conn.execute("""INSERT INTO projects(id,title,instruction,owner_user,channel)
            VALUES(%s,'데이터 일일 현황','Read-only daily data summary',%s,%s) ON CONFLICT DO NOTHING""",
            (project_id, store.company.settings.data_watch_owner_user, store.company.settings.data_watch_channel_id))
        project = conn.execute("SELECT * FROM projects WHERE id=%s", (project_id,)).fetchone()
        enqueue(conn, store, project, key, status_text(snapshot), "summary",
                check_ids=[item["id"] for item in snapshot["core_checks"][:8]])
    return {"state": "reported", "transitions": count}


def check_allowed(conn, store, check_id):
    from .core import CoreChecks

    check = conn.execute("SELECT * FROM data_watch_checks WHERE id=%s", (check_id,)).fetchone()
    job = CoreChecks(store).eligible(conn, check["research_job_id"], execution=False) if check else None
    return bool(job and job["revision"] == check["revision"] and check["policy"] == store.policy())


def incident_allowed(conn, store, incident):
    if not incident or not store.authorized() or incident["policy"] != store.policy():
        return False
    detail = incident["detail"]
    if detail.get("research_job_id"):
        return check_allowed(conn, store, detail["check_id"])
    return True


def gate(conn, store, row):
    pub = conn.execute("SELECT * FROM data_watch_publications WHERE id=%s", (row["id"],)).fetchone()
    project = conn.execute("SELECT * FROM projects WHERE id=%s", (row["project_id"],)).fetchone()
    permitted = (pub and store.authorized() and store.company.settings.data_watch_publish_enabled
                 and pub["policy"] == store.policy() and pub["expires_at"] > utcnow()
                 and row["agent"] == "data" and row["channel"] == store.company.settings.data_watch_channel_id
                 and len(row["text"]) <= 3000
                 and project["owner_user"] == store.company.settings.data_watch_owner_user
                 and project["channel"] == row["channel"] and project["revision"] == row["revision"])
    if permitted and pub["incident_id"]:
        incident = conn.execute("SELECT * FROM data_watch_incidents WHERE id=%s", (pub["incident_id"],)).fetchone()
        permitted = incident_allowed(conn, store, incident)
    if permitted and pub["check_ids"]:
        permitted = all(check_allowed(conn, store, identity) for identity in pub["check_ids"])
    if permitted and pub["kind"] == "transition":
        root = conn.execute("SELECT status,sent_ts FROM outbox WHERE id=%s", (incident["root_message_id"],)).fetchone()
        permitted = (root["status"] == "delivered" and root["sent_ts"] and row["thread_ts"] == root["sent_ts"])
    if not permitted:
        conn.execute("UPDATE outbox SET status='stale',error='data_watch_policy_or_receipt_changed' WHERE id=%s", (row["id"],))
    return bool(permitted)
