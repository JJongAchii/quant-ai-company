"""Server-rendered summaries and one receipt-bound thread per data incident."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from ..company import stable
from .coverage import SUMMARY_GROUPS, item_text, observed, short_date
from .store import utcnow

KST = ZoneInfo("Asia/Seoul")
SUMMARY_FORMAT_VERSION = 5
PROBLEM_TEXT = {
    "parquet_footer_limit": "파일 정보를 읽는 검사 도구의 2 MiB 검사 한도에 걸려 확인하지 못함",
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
    inventory_ok = bool(inventory and inventory["receipt"].get("ok") is True)
    datasets = snapshot["datasets"]
    visible = datasets if inventory_ok or snapshot.get("last_successful_inventory_at") else []
    by_name = {row["dataset"]: row for row in visible}
    findings = [row for row in visible if observed(row, snapshot["checked_at"])["state"]
                in {"late", "source_gap", "attention", "unreadable", "undated"}]
    findings.sort(key=lambda row: (
        {"late": 0, "source_gap": 1, "unreadable": 2, "attention": 3,
         "undated": 4}[observed(row, snapshot["checked_at"])["state"]],
        row["dataset"],
    ))
    headline = ("첫 목록 조회 대기" if not inventory else
                "현재 목록 조회 실패" if not inventory_ok else
                f"갱신 점검 {len(findings)}건" if findings else "소스별 날짜 확인")
    lines = [f"*데이터 현황 · {headline}* · {kst(snapshot['checked_at'])}"]
    if not inventory:
        lines.append("*지금 상태* 첫 목록 조회가 아직 끝나지 않았습니다.")
    elif not inventory_ok:
        last_good = snapshot.get("last_successful_inventory_at")
        lines.append(f"*지금 상태* 새 목록을 읽지 못했습니다. 아래 날짜와 숫자는 {kst(last_good)} 마지막 성공 기록 기준입니다."
                     if last_good else "*지금 상태* 새 목록을 읽지 못했고 이전 성공 기록도 없습니다.")
    else:
        lines.append(f"*목록 검사* {kst(inventory.get('checked_at'))} 성공 · {len(datasets)}개 데이터셋")
    if visible:
        lines.append("*소스별 마지막 데이터 날짜* (파일 업로드 날짜와 다름)")
        names = {
            "krx_prices": "주식", "krx_etf": "ETF", "krx_flows": "수급", "krx_index_prices": "지수",
            "prices": "미국 ETF·주가", "us_prices": "미국 전종목", "us_shortvol": "FINRA 공매도량",
            "fred": "FRED", "ecos": "ECOS", "oecd_cli": "OECD CLI",
            "dart_fundamental": "DART", "sec_filings": "SEC 분기 FSDS",
            "sec_13f": "SEC 13F", "sec_nport": "SEC N-PORT",
            "us_dividends": "배당 스냅샷", "us_splits": "분할 스냅샷",
        }
        for group, keys in SUMMARY_GROUPS:
            items = [f"{names[key]} {short_date(observed(by_name[key], snapshot['checked_at'])['date'])}"
                     for key in keys if key in by_name]
            if items:
                lines.append(f"• {group}: " + ", ".join(items))
        at = snapshot["checked_at"]
        if isinstance(at, str):
            at = datetime.fromisoformat(at.replace("Z", "+00:00"))
        local_at = at.astimezone(KST)
        if (local_at.date().isoformat() == "2026-09-28" and local_at.hour < 20
                and "krx_prices" in by_name
                and short_date(observed(by_name["krx_prices"], snapshot["checked_at"])["date"]) == "2026-09-23"):
            lines.append("• 한국시장 참고: 09/24~27 추석 휴장, 09/28분은 장 마감 뒤 저녁 수집 예정")
    if findings:
        lines.append("*갱신 점검 항목*" if inventory_ok else "*이전 기록의 점검 항목*")
        for row in findings[:8]:
            info = observed(row, snapshot["checked_at"])
            note = problem_text(row["problem"]) if row["problem"] else (
                info["checkpoint"].explanation if info["state"] == "source_gap" else
                f"{info['age_days']}일째 새 날짜가 없어 갱신 경로 확인 필요"
                if info["state"] == "attention" else "데이터 날짜 확인 불가")
            lines.append(f"• {row['dataset']}: {info['rule'].meaning if info['rule'] else '기준일'} "
                         f"{short_date(info['date'])} · 파일 교체 {short_date(info['object_modified'])} · {note}")
        if len(findings) > 8:
            lines.append(f"• 그 외 {len(findings) - 8}개는 `목록`에서 확인")
    if inventory_ok:
        checked = sum(row["inspection"] == "metadata_checked" for row in datasets)
        lines.append(f"파일 정보 확인 {checked}/{len(datasets)}개 · 개별 종목 누락 검사와는 별개")
    core = snapshot["core_checks"]
    if not core:
        lines.append("고정 연구 입력 6개는 이 알림의 검사 대상이 아니며 아직 검사 전")
    else:
        complete = sum(row["state"] == "complete" and not row["problem"] for row in core)
        lines.append(f"• 고정 연구 입력: {len(core)}건 중 전체 입력 검사 완료 {complete}건")
        lines += [f"  ◦ {row['title'][:60]}: {problem_text(row['problem'])}" for row in core if row["problem"]][:2]
    if findings:
        lines.append("⚠는 공개 자료·저녁 수집 시각·경과일 기준 점검 요청입니다. 종목별 누락 검사는 별개입니다.")
    lines.append(f"전체 {len(datasets)}개 날짜·파일 교체일은 `목록` · 다음 목록 검사 {kst(snapshot['next_inventory_at'])}")
    return "\n".join(lines)


def list_text(snapshot):
    inventory = snapshot["inventory"]
    visible = snapshot["datasets"] if (inventory and inventory["receipt"].get("ok")
                                      or snapshot.get("last_successful_inventory_at")) else []
    when = (kst(inventory.get("checked_at")) if inventory and inventory["receipt"].get("ok")
            else kst(snapshot.get("last_successful_inventory_at")))
    label = "목록 검사" if inventory and inventory["receipt"].get("ok") else "마지막 성공 목록"
    lines = [f"*데이터셋별 마지막 날짜 · {len(visible)}개*", f"{label} {when}"]
    if inventory and not inventory["receipt"].get("ok"):
        lines.append("⚠ 이번 목록 조회 실패. 아래는 이전 기록입니다.")
    shown = 0
    for row in visible:
        line = "• " + item_text(row, snapshot["checked_at"], verbose=True)
        if len("\n".join(lines)) + len(line) > 2850:
            break
        lines.append(line)
        shown += 1
    if shown < len(visible):
        lines.append(f"그 외 {len(visible) - shown}개는 운영 상태 API에서 확인")
    lines.append("파일 교체일은 데이터 기준일이 아닙니다. 날짜는 데이터셋 전체의 최대값이며 종목별 누락 검사는 아닙니다.")
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
