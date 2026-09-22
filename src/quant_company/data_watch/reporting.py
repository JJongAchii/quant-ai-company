"""Server-rendered summaries and one receipt-bound thread per data incident."""

from collections import Counter
from datetime import timedelta
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from ..company import stable
from .store import utcnow

KST = ZoneInfo("Asia/Seoul")


def status_text(snapshot):
    inventory = snapshot["inventory"]
    stamp = inventory["checked_at"] if inventory else "미검사"
    counts = Counter(row["freshness"]["state"] for row in snapshot["datasets"])
    text = (f"*데이터 현황* · 전체 메타데이터 확인: {stamp}\n"
            f"데이터 {len(snapshot['datasets'])}개 · 최신성 기준 미등록 {counts['unregistered']}개 · "
            f"지연 {counts['stale']}개 · 날짜 미검사 {counts['unchecked']}개\n"
            "메타데이터 검사는 종목별 누락·중복 검사가 아닙니다.")
    if not inventory or not inventory["receipt"].get("ok"):
        text += "\n전체 목록 조회: 미검사 또는 접근 실패. 이전 목록을 보존했습니다."
    for row in snapshot["datasets"]:
        bounds = row["date_bounds"]
        dates = ", ".join(f"{k}: {v.get('max', '미검사')}" for k, v in list(bounds.items())[:3]) or "미검사"
        timing = row["freshness"]
        label = {"unregistered": "기준 미등록", "unchecked": "미검사", "fresh": "등록 기준 충족", "stale": "지연"}[timing["state"]]
        line = (f"\n• {row['dataset']}: {row['problem'] or label} · 데이터 기준일 {dates}"
                f" · 업로드 {row['source']['last_modified']}")
        if len(text + line) > 6500:
            text += "\n(긴 목록은 data_watch_status 또는 운영 상태 API에서 확인)"
            break
        text += line
    text += "\n*고정 연구 입력 상세 검사*"
    if not snapshot["core_checks"]:
        text += "\n미검사 — 상세 검사 기능과 승인된 ETF 연구 입력 연결이 필요합니다."
    for row in snapshot["core_checks"][:8]:
        label = (row["problem"] if row["problem"] else "전체 입력 검사 완료" if row["state"] == "complete" else "미검사 / 워커 대기")
        text += (f"\n• {row['title'][:80]}: {label} · 검사 {row['checked_at'] or '미검사'}"
                 f" · 입력 범위 {row['scope_digest'][:12]}")
    text += f"\n다음 목록 확인: {snapshot['next_inventory_at']} · 최신 레이크와 고정 연구 입력은 별도로 판단합니다."
    return text


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
    return (f"*데이터 {'복구 확인' if state == 'recovered' else '점검 필요'}* · {key}\n"
            f"상태: {detail.get('problem') or '재검사에서 해당 오류가 확인되지 않음'}\n"
            f"영향: {detail['impact']}\n마지막 해당 검사 통과: {last_healthy or '미확인'}\n"
            f"근거: {detail['evidence']} · 검사 {detail['checked_at']}\n"
            f"다음 점검: {detail['next_check']}\n"
            "수집·연구 입력은 자동 수정하지 않습니다. 회사 코드 수리가 필요하면 improvements 케이스로 연결합니다.")


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
        key = f"{store.policy()}:summary:{at.date()}"
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
