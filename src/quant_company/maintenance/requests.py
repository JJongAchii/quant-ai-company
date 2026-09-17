"""Director-facing history and durable maintenance requests. No GitHub/model credentials."""

import json
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from psycopg.types.json import Jsonb

from ..company import PolicyError, as_json
from .policy import SECRET, digest


def permitted(company, project):
    return (project["owner_user"] in company.settings.slack_allowed_users
            and bool(project["channel"])
            and (project["channel"] in company.settings.slack_allowed_channels
                 or project["channel"].startswith("D")))


def service(conn, company, owner):
    if not conn.execute("SELECT to_regclass('maintenance_control') AS name").fetchone()["name"]:
        return {"enabled": False, "reason": "maintenance_not_installed"}
    # Also tolerates the previous schema during a rolling process restart.
    config = conn.execute("SELECT to_jsonb(c)->'runtime' AS runtime FROM maintenance_control c WHERE id=1").fetchone()
    config = (config or {}).get("runtime") or {}
    if owner not in config.get("allowed_owners", []) or owner not in company.settings.slack_allowed_users:
        return {"enabled": False, "reason": "maintenance_not_authorized_or_configured"}
    at = datetime.now(UTC)
    heartbeat = config.get("heartbeat_at")
    fresh = bool(heartbeat and at - datetime.fromisoformat(heartbeat) < timedelta(
        seconds=max(1200, config.get("poll_seconds", 300) * 3)))
    used = conn.execute("SELECT count(*) AS n FROM maintenance_calls WHERE created_at>=CURRENT_DATE").fetchone()["n"]
    next_day = conn.execute("SELECT (CURRENT_DATE+1)::timestamptz AS reset").fetchone()["reset"]
    return as_json({"enabled": bool(config.get("enabled")), "worker_recently_seen": fresh,
                    "heartbeat_at": heartbeat, "poll_seconds": config.get("poll_seconds"),
                    "daily_model_calls": used, "daily_model_call_cap": config.get("max_daily_calls"),
                    "budget_resets_at": next_day, "approval_required_for_application": True})


def report(conn, job, owner):
    """Follow a diagnostic's recorded case, never infer completion from the triage state."""
    current = job
    if job["receipt"].get("case_id"):
        linked = conn.execute("SELECT * FROM maintenance_jobs WHERE id=%s AND payload->'owners'=%s",
                              (job["receipt"]["case_id"], Jsonb([owner]))).fetchone()
        if not linked:
            return {"request_id": str(job["id"]), "state": "blocked", "error": "case_owner_scope_mismatch"}
        current = linked
    finding = current["payload"].get("finding", {})
    result = {"request_id": str(job["id"]), "case_id": str(current["id"]),
              "state": current["state"], "error": current["error"],
              "title": finding.get("title"), "problem": finding.get("problem"),
              "hypothesis": finding.get("hypothesis"), "evaluation_mode": finding.get("evaluation", {}).get("mode"),
              "evidence_keys": finding.get("evidence_keys", []),
              "reason": job["receipt"].get("reason"), "pr": current["receipt"].get("pr"),
              "created_at": job["created_at"]}
    if SECRET.search(json.dumps(result, default=str)):
        return {"request_id": str(job["id"]), "state": "blocked", "error": "report_contains_possible_secret"}
    return as_json(result)


def status(conn, company, project):
    if not permitted(company, project):
        return {"service": {"enabled": False, "reason": "owner_or_channel_not_authorized"}, "requests": []}
    runtime = service(conn, company, project["owner_user"])
    jobs = []
    if runtime.get("enabled"):
        jobs = conn.execute("""SELECT * FROM maintenance_jobs WHERE payload->'owners'=%s AND
            ((kind='review' AND payload->>'request_project_id'=%s) OR
             (kind='repair' AND EXISTS (SELECT 1 FROM jsonb_array_elements(payload->'observations') x
              WHERE x->>'project_id'=%s))) ORDER BY created_at DESC,id DESC LIMIT 10""",
                            (Jsonb([project["owner_user"]]), str(project["id"]), str(project["id"]))).fetchall()
    reports = []
    for job in jobs:
        item = report(conn, job, project["owner_user"])
        if len(json.dumps(reports + [item], ensure_ascii=False)) > 16000:
            break
        reports.append(item)
    return {"service": runtime, "requests": reports, "truncated": len(reports) < len(jobs) or len(jobs) == 10,
            "scope": "This thread's requests and observed cases; bounded to 10 and 16000 characters. "
                     "A hypothesis is not a proven cause."}


def history(conn, company, project, arguments):
    if set(arguments) - {"query", "limit"}:
        raise PolicyError("company_history accepts query and limit only")
    query, limit = arguments.get("query", ""), arguments.get("limit", 12)
    if not isinstance(query, str) or len(query) > 200 or type(limit) is not int or not 1 <= limit <= 20:
        raise PolicyError("Invalid company_history query or limit")
    patterns = ["%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                for word in query.split()[:8]] or ["%"]
    rows = conn.execute("""SELECT m.id,m.project_id,m.text,m.created_at,p.channel,p.thread_ts,
        k.agent,k.status,k.result,k.error FROM messages m JOIN projects p ON p.id=m.project_id
        LEFT JOIN tasks k ON k.id=m.task_id WHERE p.owner_user=%s
          AND (p.channel=ANY(%s) OR p.channel LIKE 'D%%')
          AND m.kind IN ('human','instruction') AND m.created_at>=now()-interval '30 days'
          AND (m.text ILIKE ANY(%s) OR k.result ILIKE ANY(%s))
        ORDER BY m.created_at DESC,m.id DESC LIMIT %s""",
                        (project["owner_user"], company.settings.slack_allowed_channels,
                         patterns, patterns, limit + 1)).fetchall()
    items = []
    for row in as_json(rows[:limit]):
        identity = "message:" + row.pop("id")
        if SECRET.search(json.dumps(row)):
            items.append({"key": identity, "omitted": "possible_secret"})
            continue
        row["key"] = identity
        row["excerpted"] = len(row["text"]) > 1500 or len(row.get("result") or "") > 2000
        row["text"], row["result"] = row["text"][:1500], (row.get("result") or "")[:2000]
        row["slack_url"] = f"https://app.slack.com/archives/{row['channel']}/p{row['thread_ts'].replace('.', '')}"
        if len(json.dumps(items + [row], ensure_ascii=False)) > 36000:
            break
        items.append(row)
    return {"as_of": datetime.now(UTC).isoformat(), "window_days": 30, "records": items,
            "truncated": len(rows) > len(items), "limit": limit,
            "interpretation": "Recorded requests and current task outcomes, not a quality score or proof that an "
                              "employee saw other threads. Query with short keywords to inspect omitted older requests."}


def submit(conn, company, project, task):
    if task["parent_id"] or not conn.execute("SELECT 1 FROM inbound WHERE task_id=%s", (task["id"],)).fetchone():
        raise PolicyError("Maintenance review must originate from a human request")
    runtime = service(conn, company, project["owner_user"])
    if not runtime.get("enabled"):
        return {"accepted": False, "service": runtime}
    # Serialize per owner as well as the caller's existing project lock.
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("maintenance-review:" + project["owner_user"],))
    identity = str(uuid5(NAMESPACE_URL, "maintenance-review:" + str(task["id"])))
    existing = conn.execute("SELECT id FROM maintenance_jobs WHERE id=%s", (identity,)).fetchone()
    if existing:
        return {"accepted": True, "request_id": identity, "duplicate": True, "service": runtime}
    pending = conn.execute("""SELECT id FROM maintenance_jobs WHERE kind='review' AND payload->'owners'=%s
        AND state IN ('review','triage') ORDER BY created_at LIMIT 3""", (Jsonb([project["owner_user"]]),)).fetchall()
    if len(pending) >= 3:
        return {"accepted": False, "reason": "review_queue_full", "pending_request_ids": [str(r["id"]) for r in pending]}
    payload = {"owners": [project["owner_user"]], "request_project_id": str(project["id"]),
               "request_task_id": str(task["id"]), "request_revision": project["revision"],
               "instruction": task["instruction"]}
    if SECRET.search(json.dumps(payload)):
        return {"accepted": False, "reason": "request_contains_possible_secret"}
    conn.execute("INSERT INTO maintenance_jobs(id,kind,state,payload) VALUES (%s,'review','review',%s)",
                 (identity, Jsonb(payload)))
    company._event(conn, "maintenance_review_requested", {"request_id": identity, "task_id": str(task["id"])}, project["id"])
    return {"accepted": True, "request_id": identity, "state": "queued", "service": runtime,
            "next": "Maintenance reports to this thread without another user message. Receipt is not analysis completion."}


def tool(conn, company, task, request):
    project = company._project(conn, task["project_id"])
    if not permitted(company, project):
        raise PolicyError("Owner or channel is not authorized for company records")
    if request.name == "company_history":
        result = history(conn, company, project, request.arguments)
    else:
        if request.arguments:
            raise PolicyError("maintenance_review and maintenance_status take no arguments")
        result = (submit(conn, company, project, task) if request.name == "maintenance_review"
                  else status(conn, company, project))
    content = json.dumps(result, ensure_ascii=False)
    identity = "company:" + digest([str(project["id"]), request.name, result])[:24]
    conn.execute("""INSERT INTO sources(id,title,uri,content,available_at,approved,project_id,synthetic)
        VALUES (%s,%s,%s,%s,now(),true,%s,false) ON CONFLICT DO NOTHING""",
                 (identity, request.name, "company://records/" + identity, content, project["id"]))
    result["source_id"] = identity
    return result


def progress_text(value, runtime):
    state, error = value["state"], value.get("error")
    prefix = f"진단 요청 {value['request_id'][:8]}: "
    if error == "daily_model_budget":
        return prefix + f"모델 호출 한도로 대기 중입니다. 예산 초기화: {runtime.get('budget_resets_at')}. 요청은 보존되어 자동 재개됩니다."
    if state == "blocked":
        return prefix + f"검증에서 멈췄습니다 ({error}). 수정 완료가 아닙니다. 근거와 실패 기록을 보존했으며 운영자 확인이 필요합니다."
    if error:
        return prefix + f"일시 대기 중입니다 ({error}). 같은 작업을 이어가며 결과를 이 스레드에 보고합니다."
    if state == "done":
        return prefix + "점검을 마쳤습니다. " + (value.get("reason") or "확인된 수정 후보가 없습니다.")[:1800]
    if state in {"pr_open", "applied", "closed"}:
        return prefix + {"pr_open": "검토할 PR이 준비됐습니다. 운영 반영에는 별도 승인이 필요합니다.",
                         "applied": "연결된 개선안의 반영이 완료됐습니다.", "closed": "연결된 PR이 닫혀 있습니다."}[state]
    if value.get("title"):
        return (prefix + f"개선 후보를 확인했습니다: {value['title']}\n"
                f"원인 가설(미검증): {value.get('hypothesis') or '검토 중'}\n"
                f"현재 단계: {state}. 수정 완료는 검증과 PR 기록으로 보고합니다.")
    return prefix + "대화 기록과 실제 모델 입력을 확인하는 작업이 접수됐습니다. 진행 상태와 결과를 이 스레드에 보고합니다."
