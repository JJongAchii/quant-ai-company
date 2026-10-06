"""Server-rendered summaries and one receipt-bound thread per data incident."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from ..company import stable
from .coverage import NAMES, RULES, SUMMARY_GROUPS, UPDATE_LABELS, observed
from .store import utcnow

KST = ZoneInfo("Asia/Seoul")
SUMMARY_FORMAT_VERSION = 6
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


SUMMARY_MERGES = (
    ("한국시장 주식·ETF·수급·지수", ("krx_prices", "krx_etf", "krx_flows", "krx_index_prices")),
    ("미국 가격·공매도량", ("prices", "us_shortvol")),
    ("미국 재무 공시(SEC)", ("sec_filings", "sec_fundamental")),
    ("미국 펀드 자료(N-PORT)", ("sec_nport", "sec_nport_fund")),
    ("미국 배당·주식분할", ("us_dividends", "us_splits")),
)
FINDING_STATES = {"late", "source_gap", "attention", "unreadable", "undated"}


def compact_date(value, checked_at):
    if not value:
        return "날짜 없음"
    if isinstance(checked_at, str):
        checked_at = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    return value.strftime("%m/%d") if value.year == checked_at.astimezone(KST).year else value.isoformat()


def data_date_text(info, checked_at):
    if info["state"] == "reference":
        return "고정 매핑"
    if info["date"] is None:
        return "날짜 확인 실패"
    rule, value = info["rule"], info["date"]
    if rule and rule.meaning == "기준월":
        return f"{value.year}년 {value.month}월"
    suffix = {"거래일": "거래분까지", "제출일": "제출분까지", "공시 반영일": "반영분까지",
              "스냅샷일": "기준", "관측일": "관측분까지"}
    meaning = suffix.get(rule.meaning, rule.meaning) if rule else "기준"
    return f"{compact_date(value, checked_at)} {meaning}"


def summary_items(rows, checked_at):
    by_name = {row["dataset"]: row for row in rows}
    result, used = [], set()
    for label, keys in SUMMARY_MERGES:
        if not all(key in by_name for key in keys):
            continue
        infos = [observed(by_name[key], checked_at) for key in keys]
        signatures = {(info["date"], info["state"], by_name[key].get("problem"),
                       info["rule"].meaning,
                       UPDATE_LABELS.get(key) if info["state"] == "source_gap" else None)
                      for key, info in zip(keys, infos, strict=True)}
        if len(signatures) == 1:
            result.append((label, by_name[keys[0]], infos[0], set(keys)))
            used.update(keys)
    result.extend((NAMES.get(row["dataset"], row["dataset"]), row, observed(row, checked_at), {row["dataset"]})
                  for row in rows if row["dataset"] not in used)
    return result


def issue_note(row, info):
    if info["state"] == "source_gap":
        return UPDATE_LABELS[row["dataset"]] + " 미반영"
    if info["state"] == "late":
        return "등록된 수집 기준보다 늦음 → 수집 경로 점검"
    if info["state"] == "attention":
        return f"{info['age_days']}일째 새 날짜 없음"
    return {
        "parquet_footer_limit": "파일 정보 읽기 한도 초과 → 검사 도구 점검",
        "descriptor_unavailable_or_changed": "파일 정보 읽기 실패 → 재검사 필요",
        "dataset_missing": "데이터 목록에서 사라짐 → 수집 경로 점검",
        "empty_dataset": "자료가 0행임 → 수집 결과 점검",
    }.get(row.get("problem"), "기준 날짜를 읽지 못함 → 날짜 항목 점검")


def status_text(snapshot):
    inventory, at = snapshot["inventory"], snapshot["checked_at"]
    inventory_ok = bool(inventory and inventory["receipt"].get("ok") is True)
    visible = snapshot["datasets"] if inventory_ok or snapshot.get("last_successful_inventory_at") else []
    lines = [f"*데이터 업데이트 현황* · {kst(at)}"]
    if not inventory:
        lines += ["", "첫 데이터 목록을 아직 받지 못했습니다."]
    elif not inventory_ok:
        last_good = snapshot.get("last_successful_inventory_at")
        lines += ["", "*현재 조회 오류*",
                  f"아래는 {kst(last_good)} 마지막 성공 기록입니다. 현재 상태는 재검사 대기 중입니다."
                  if last_good else "데이터 목록을 읽지 못했습니다. 이전 성공 기록도 없습니다."]
    items = summary_items(visible, at)
    sections = (
        ("*수집·반영 경로 점검*", {"source_gap", "late"}, "🔴"),
        ("*갱신 일정 확인 필요*", {"attention"}, "🟠"),
        ("*읽기·날짜 확인 문제*", {"unreadable", "undated"}, "⚪"),
    )
    shown = 0
    for title, states, icon in sections:
        if shown >= 8:
            break
        selected = [item for item in items if item[2]["state"] in states]
        if not selected:
            continue
        lines += ["", title if inventory_ok else title + " (이전 기록)"]
        for label, row, info, _ in selected[:8 - shown]:
            date = "" if info["date"] is None else data_date_text(info, at) + " → "
            lines.append(f"{icon} *{label}*: {date}{issue_note(row, info)}")
            shown += 1
    findings = sum(info["state"] in FINDING_STATES for _, _, info, _ in items)
    if findings > shown:
        lines.append(f"추가 {findings - shown}종은 `목록`에서 확인")
    primary = {key for _, keys in SUMMARY_GROUPS for key in keys} | {"sec_insider"}
    remaining = [(label, info) for label, _, info, keys in items
                 if keys & primary and info["state"] not in FINDING_STATES]
    if remaining:
        lines += ["", "*그 밖의 주요 데이터는 여기까지*" if findings else "*주요 데이터는 여기까지*"]
        lines += [f"• {label}: {data_date_text(info, at)}" for label, info in remaining]
    core = snapshot["core_checks"]
    if core:
        complete = sum(row["state"] == "complete" and not row["problem"] for row in core)
        lines += ["", f"연구 고정입력 검사: {complete}/{len(core)}건 완료"]
        lines += [f"• {row['title'][:60]}: {problem_text(row['problem'])}" for row in core if row["problem"]][:2]
    lines += ["", f"전체 {len(visible)}개 날짜·파일 교체일은 `목록`"]
    checked = f"목록 조회 {kst(inventory.get('checked_at'))} · " if inventory else ""
    lines.append(f"{checked}다음 확인 {kst(snapshot['next_inventory_at'])}")
    return "\n".join(lines)


def list_text(snapshot):
    inventory, at = snapshot["inventory"], snapshot["checked_at"]
    inventory_ok = bool(inventory and inventory["receipt"].get("ok") is True)
    visible = snapshot["datasets"] if inventory_ok or snapshot.get("last_successful_inventory_at") else []
    when = kst(inventory.get("checked_at")) if inventory_ok else kst(snapshot.get("last_successful_inventory_at"))
    lines = [f"*전체 데이터 목록 · {len(visible)}개*", f"{'조회' if inventory_ok else '마지막 성공 기록'} {when}",
             "자료 날짜 · 파일 교체일 (MM/DD는 올해 날짜)"]
    if not inventory_ok:
        lines.append("⚠ 현재 조회가 끝나지 않았거나 실패했습니다. 아래는 이전 기록입니다.")
    shown = 0
    groups = (("한국시장", "한국 시장"), ("미국시장", "미국 시장"), ("거시", "경제지표"),
              ("공시", "한국 기업 공시"), ("미국 공시", "미국 기업 공시·펀드"),
              ("이력 자료", "고정·이력 자료"), (None, "기타"))
    for group, heading in groups:
        members = [row for row in visible if (RULES.get(row["dataset"]).group
                   if RULES.get(row["dataset"]) else None) == group]
        if not members:
            continue
        lines += ["", f"*{heading}*"]
        for row in members:
            info = observed(row, at)
            icon = ("🔴" if info["state"] in {"source_gap", "late"} else
                    "🟠" if info["state"] == "attention" else
                    "⚪" if info["state"] in {"unreadable", "undated"} else "•")
            label = NAMES.get(row["dataset"], row["dataset"])
            line = f"{icon} {label}: {data_date_text(info, at)} · 파일 {compact_date(info['object_modified'], at)}"
            if len("\n".join(lines)) + len(line) > 2770:
                break
            lines.append(line)
            shown += 1
    if shown < len(visible):
        lines.append(f"나머지 {len(visible) - shown}개는 운영 상태 API에서 확인")
    lines += ["", "🔴 공개분/등록 기준 미달 · 🟠 갱신 일정 점검 · ⚪ 날짜 확인 실패",
              "자료 날짜는 데이터셋 전체의 최대 날짜이며, 파일 교체일과 다릅니다."]
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
    dataset = key.removeprefix("최신 레이크 ")
    label = ("데이터 목록 조회" if key == "전체 목록 접근" else
             "연구 고정입력" if key.startswith("고정 입력 ") else NAMES.get(dataset, dataset))
    if state == "recovered":
        lines = [f"*{label} · 검사 복구*", "이번 검사에서 이전 오류가 발생하지 않았습니다.",
                 "최신 자료 날짜는 `상태` 또는 `목록`에서 확인할 수 있습니다."]
    else:
        action = {
            "parquet_footer_limit": "파일 정보 읽기 한도를 점검한 뒤 데이터 날짜를 재검사",
            "descriptor_unavailable_or_changed": "파일 정보 읽기 재검사",
            "lake_catalog_unavailable": "다음 목록 조회에서 재확인",
            "dataset_missing": "수집·발행 경로와 데이터 목록 확인",
            "empty_dataset": "수집 결과에 실제 데이터 행이 있는지 확인",
            "data_late": "등록된 수집 일정과 반영 경로 확인",
        }.get(problem, "상세 검사 결과 확인")
        lines = [f"*{label} · 점검 필요*", f"문제: {problem_text(problem)}", f"다음 조치: {action}"]
        if key == "전체 목록 접근" and last_healthy:
            lines.append(f"현재 날짜 표시는 {kst(last_healthy)} 마지막 성공 기록을 사용합니다.")
        if key.startswith("고정 입력 "):
            lines.append(f"영향: {detail['impact']}")
    lines += [f"확인 {kst(detail['checked_at'])} · 다음 점검 {kst(detail['next_check'])}"]
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
