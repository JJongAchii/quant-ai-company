"""Small, authenticated owner commands. Never ask a model to change its own budget."""

import re

from psycopg.types.json import Jsonb

from .company import PolicyError


def parse_daily_limit_command(text):
    # Anchored imperatives only. Questions, quoted examples and vague 'remove limits' stay conversation.
    match = re.fullmatch(
        r"(개선\s*(?:BOT|봇)(?:\s*전용)?|회사\s*(?:공통)?|전체|모든)\s*"
        r"(?:(?:일일|하루)\s*)?(?:\d+\s*회\s*)?(?:호출\s*)?한도(?:를)?\s*"
        r"(?:해제|없애)(해줘|해주세요|해 주세요|줘|주세요|해|하고|고)?[.!。]*"
        r"(?:\s+(.*))?", text.strip(), re.I)
    if not match:
        return None
    suffix, remainder = match[2], (match[3] or "").strip()
    if remainder and suffix not in {"하고", "고"}:
        return None
    if suffix in {"하고", "고"} and not remainder:
        return None
    scope = "maintenance" if match[1].startswith("개선") else "company" if match[1].startswith("회사") else "both"
    return {"scope": scope, "diagnosis": remainder}


def effective_limits(conn, company, maintenance_cap=None):
    row = conn.execute("SELECT * FROM company_policy WHERE id=1").fetchone()
    return {
        "revision": row["revision"],
        "company": None if row["remove_company_daily_limit"] else company.settings.company_max_daily_turns or None,
        "maintenance": None if row["remove_maintenance_daily_limit"] else maintenance_cap or None,
    }


def apply_daily_limit_command(conn, company, project, task, event_key, text, command):
    from .maintenance.requests import permitted, service, submit

    runtime = service(conn, company, project["owner_user"])
    if not permitted(company, project) or not runtime.get("enabled") or task["agent"] != "director":
        raise PolicyError("Owner control requires an authorized maintenance owner and channel")
    conn.execute("SELECT * FROM company_policy WHERE id=1 FOR UPDATE")
    before = effective_limits(conn, company, runtime.get("daily_model_call_cap"))
    conn.execute("""UPDATE company_policy SET revision=revision+1,
        remove_company_daily_limit=remove_company_daily_limit OR %s,
        remove_maintenance_daily_limit=remove_maintenance_daily_limit OR %s WHERE id=1""",
                 (command["scope"] in {"company", "both"}, command["scope"] in {"maintenance", "both"}))
    after = effective_limits(conn, company, runtime.get("daily_model_call_cap"))
    receipt = {"state": "applied", "before": before, "after": after, "event_key": event_key}
    if command["diagnosis"]:
        # Queue independently of inference quotas; acceptance is not a finished diagnosis.
        receipt["diagnosis"] = submit(conn, company, project, task)
    conn.execute("""INSERT INTO policy_commands(event_key,owner_user,project_id,request_text,receipt)
        VALUES (%s,%s,%s,%s,%s)""", (event_key, project["owner_user"], project["id"], text, Jsonb(receipt)))
    company._event(conn, "daily_limit_policy_applied", receipt, project["id"])
    def cap(value):
        return "제한 없음" if value is None else f"{value}회/일"
    summary = (f"일일 호출 정책 v{after['revision']} 적용 확인: 회사 공통 {cap(after['company'])}, "
               f"개선BOT 전용 {cap(after['maintenance'])}. Codex 구독 한도와 업무별 반복·동시 실행 제한은 유지됩니다.")
    if command["diagnosis"]:
        review = receipt["diagnosis"]
        summary += (f"\n추가 진단은 접수됐습니다 ({review['request_id'][:8]}). 결과를 이 스레드에 보고합니다."
                    if review.get("accepted") else f"\n추가 진단은 미접수 상태입니다: {review.get('reason', 'service_unavailable')}.")
    conn.execute("UPDATE tasks SET status='completed',result=%s WHERE id=%s", (summary, task["id"]))
    company._message(conn, project, task["id"], "director", "control", summary)
    return receipt
