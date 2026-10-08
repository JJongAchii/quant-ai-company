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
    if company.settings.company_improvements_enabled and not conn.execute(
        "SELECT to_regclass('maintenance_cases') AS name").fetchone()["name"]:
        return {"enabled": False, "reason": "maintenance_case_schema_not_initialized"}
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
    from ..owner_controls import effective_limits

    limits = effective_limits(conn, company, config.get("max_daily_calls"))
    return as_json({"enabled": bool(config.get("enabled")), "worker_recently_seen": fresh,
                    "heartbeat_at": heartbeat, "poll_seconds": config.get("poll_seconds"),
                    "daily_model_calls": used, "daily_model_call_cap": limits["maintenance"],
                    "company_daily_model_call_cap": limits["company"], "policy_revision": limits["revision"],
                    "budget_resets_at": next_day if limits["maintenance"] or limits["company"] else None,
                    "approval_required_for_application": True})


def report(conn, job, owner):
    """Follow a diagnostic's recorded case, never infer completion from the triage state."""
    current = job
    visited = set()
    while (next_id := current["receipt"].get("recheck_id") or current["receipt"].get("case_id")):
        if next_id in visited or len(visited) >= 6:
            return {"request_id": str(job["id"]), "state": "blocked", "error": "case_link_requires_review"}
        visited.add(next_id)
        linked = conn.execute("SELECT * FROM maintenance_jobs WHERE id=%s AND payload->'owners'=%s",
                              (next_id, Jsonb([owner]))).fetchone()
        if not linked:
            return {"request_id": str(job["id"]), "state": "blocked", "error": "case_owner_scope_mismatch"}
        current = linked
    finding = current["payload"].get("finding", {})
    from ..system_state import assessment

    truth = assessment(conn, current["id"])
    result = {"request_id": str(job["id"]), "case_id": str(current["id"]),
              "state": current["state"], "error": current["error"],
              "title": finding.get("title"), "problem": finding.get("problem"),
              "hypothesis": finding.get("hypothesis"), "evaluation_mode": finding.get("evaluation", {}).get("mode"),
              "evidence_keys": finding.get("evidence_keys", []),
              "investigation_round": current["payload"].get("investigation_round", 0),
              "candidate_attempt": current["payload"].get("patch_attempt", 1),
              "prior_attempts": len(current["payload"].get("candidate_attempts", [])),
              "validation": current["receipt"].get("ci"),
              "reason": current["receipt"].get("reason") or job["receipt"].get("reason"), "pr": current["receipt"].get("pr"),
              "head": current["receipt"].get("head"),
              "outcome": current["receipt"].get("outcome"),
              "required_evidence": current["receipt"].get("required_evidence", []),
              "review_ids": current["receipt"].get("review_ids", []),
              "created_at": job["created_at"], "assessment": truth,
              "finding_is_current_fact": truth["disposition"] == "reproduced"}
    if truth["disposition"] in {"invalidated", "resolved"}:
        result["historical_finding"] = {k: result.pop(k) for k in ("problem", "hypothesis")}
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
        from .cases import allowed, lookup

        case = lookup(conn, project["id"])
        if case and allowed(conn, company, case):
            jobs = [conn.execute("SELECT * FROM maintenance_jobs WHERE id=%s", (case["request_id"],)).fetchone()]
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
        from .cases import destination, installed

        case = (conn.execute("SELECT * FROM maintenance_cases WHERE request_id=%s", (identity,)).fetchone()
                if installed(conn) else None)
        return {"accepted": True, "request_id": identity, "duplicate": True, "service": runtime,
                **({"destination": destination(conn, case)} if case else {})}
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
    from .cases import attach

    case = attach(conn, company, identity, project)
    return {"accepted": True, "request_id": identity, "state": "queued", "service": runtime,
            **({"destination": case} if case else {}),
            "next": ("Maintenance reports to the linked case thread. A pending/uncertain delivery is not a live thread."
                     if case else "Maintenance reports to this thread without another user message. "
                     "Receipt is not analysis completion.")}


def tool(conn, company, task, request):
    project = company._project(conn, task["project_id"])
    if not permitted(company, project):
        raise PolicyError("Owner or channel is not authorized for company records")
    if request.name == "company_history":
        result = history(conn, company, project, request.arguments)
    elif request.name == "repository_read":
        from ..system_state import repository_read

        result = repository_read(conn, request.arguments)
    elif request.name == "system_status":
        from ..system_state import current_system

        if request.arguments:
            raise PolicyError("system_status takes no arguments")
        result = current_system(conn, company, [project["owner_user"]])
    else:
        if request.arguments:
            raise PolicyError("maintenance_review and maintenance_status take no arguments")
        result = (submit(conn, company, project, task) if request.name == "maintenance_review"
                  else status(conn, company, project))
    identity = record_source(conn, project, request.name, result)
    result["source_id"] = identity
    return result


def record_source(conn, project, name, result):
    content = json.dumps(result, ensure_ascii=False)
    if SECRET.search(content):
        raise PolicyError("Company evidence contains a possible secret")
    identity = "company:" + digest([str(project["id"]), name, result])[:24]
    conn.execute("""INSERT INTO sources(id,title,uri,content,available_at,approved,project_id,synthetic)
        VALUES (%s,%s,%s,%s,now(),true,%s,false) ON CONFLICT DO NOTHING""",
                 (identity, name, "company://records/" + identity, content, project["id"]))
    return identity


def progress_text(value, runtime):
    state, error = value["state"], value.get("error")
    prefix = f"진단 요청 {value['request_id'][:8]}: "
    if error == "daily_model_budget":
        if runtime.get("daily_model_call_cap") is None and runtime.get("company_daily_model_call_cap") is None:
            return prefix + "일일 한도 해제가 적용됐습니다. 저장된 요청을 최신 근거로 재개할 차례를 기다립니다."
        return prefix + f"모델 호출 한도로 대기 중입니다. 예산 초기화: {runtime.get('budget_resets_at')}. 요청은 보존되어 자동 재개됩니다."
    if error == "review_reconciliation_required":
        return prefix + ("독립 검토의 원 요청·runtime 영수증·원 CLI 출력 대사가 필요합니다. "
                         "현재 결과는 불확실하며 코드만 읽어서 원인을 확정할 수 없습니다. "
                         "원 요청을 재호출하지 않고 직원 평가 점수도 유지합니다. "
                         "수정안·검증·PR·운영 반영은 아직 완료되지 않았습니다.")
    if state == "blocked":
        return prefix + f"검증에서 멈췄습니다 ({error}). 수정 완료가 아닙니다. 근거와 실패 기록을 보존했으며 운영자 확인이 필요합니다."
    if error:
        return prefix + f"일시 대기 중입니다 ({error}). 같은 작업을 이어가며 결과를 이 스레드에 보고합니다."
    if state == "done":
        return prefix + "점검을 마쳤습니다. 결과: 수정 후보 없음. 수리·PR·운영 반영 기록은 없습니다. " + (value.get("reason") or "확인된 수정 후보가 없습니다.")[:1800]
    if state in {"pr_open", "applied", "closed"}:
        return prefix + {"pr_open": "검토할 PR이 준비됐습니다. 운영 반영에는 별도 승인이 필요합니다.",
                         "applied": "연결된 개선안의 반영이 완료됐습니다.", "closed": "연결된 PR이 닫혀 있습니다."}[state]
    if value.get("title"):
        return (prefix + f"개선 후보를 확인했습니다: {value['title']}\n"
                f"원인 가설(미검증): {value.get('hypothesis') or '검토 중'}\n"
                f"현재 단계: {state}. 수정 완료는 검증과 PR 기록으로 보고합니다.")
    return prefix + "대화 기록과 실제 모델 입력을 확인하는 작업이 접수됐습니다. 진행 상태와 결과를 이 스레드에 보고합니다."
