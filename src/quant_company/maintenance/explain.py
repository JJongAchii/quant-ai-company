"""Plain Korean case text for the owner: the problem, the needed improvement, progress and who acts.

Built only from recorded rows, never from a model call. Internal state codes stay out of the text.
"""

from uuid import UUID

from ..data_watch.coverage import NAMES as DATASETS
from ..data_watch.reporting import kst, problem_text

STAGES = {"proposal": "가설 제안", "challenge": "가설 반박 검토", "selection": "실행 결정",
          "implementation": "실험 코드 준비", "repair": "기술 수리", "interpretation": "결과 해석",
          "audit": "독립 감사", "cycle_review": "주기 검토", "meaning": "의미 검토",
          "technical_review": "기술 실패 검토", "program_proposal": "연구 과제 제안",
          "program_data": "데이터 점검", "program_selection": "연구 과제 선택", "planning_slate": "연구 계획 후보"}
REASONS = {"uncertain": "모델 결과를 확인할 수 없음", "timeout": "제한 시간 초과",
           "invalid_output": "응답 형식 오류", "task_turn_limit": "같은 업무의 모델 호출 횟수 한도"}
# Why a case stopped, and what the owner can do about it.
STOPS = {
    "review_reconciliation_required": ("Claude 검토 결과가 확정되지 않아 개선봇이 자동으로 다룰 수 없습니다.",
                                       "개선봇이 고칠 수 없는 종류입니다. 고치려면 개발 작업(코드 PR)이 필요합니다."),
    "investigation_budget_exhausted": ("정해진 조사 횟수 안에 원인을 찾지 못했습니다.",
                                       "계속하려면 확인할 범위를 좁혀 새로 요청해 주세요."),
    "evidence_churn_requires_review": ("조사하는 동안 코드와 기록이 계속 바뀌어 결론을 내지 못했습니다.",
                                       "계속하려면 새로 요청해 주세요."),
    "maintenance_proposal_context_too_large": ("조사할 자료가 너무 많아 진단을 끝내지 못했습니다.",
                                               "계속하려면 확인할 범위를 좁혀 새로 요청해 주세요."),
    "protected_or_unknown_path": ("개선봇이 바꿀 수 없는 보호 영역을 고쳐야 합니다.", "개발 작업(코드 PR)이 필요합니다."),
    "prompt_replay_cannot_validate_runtime_code_changes": ("개선봇의 비교 시험으로는 검증할 수 없는 수정입니다.",
                                                           "개발 작업(코드 PR)이 필요합니다."),
    "pr_ref_moved": ("PR을 만드는 사이 main이 바뀌어 다시 시험해야 합니다.",
                     "없음. 다시 시험할 수 있으면 개선봇이 이어갑니다. 오래 그대로면 새로 요청해 주세요."),
    "model_uncertain": ("모델 응답을 확인할 수 없어 멈췄습니다.", "개발 작업으로 원인을 확인해야 합니다."),
}
WORKING = {"design": "설계 문서를 쓰고 있습니다.", "patch": "고칠 코드를 만들고 있습니다.",
           "evaluate": "고치기 전후를 비교 시험하고 있습니다.", "publish": "수정안을 GitHub에서 시험(CI)하고 있습니다.",
           "ci": "수정안을 GitHub에서 시험(CI)하고 있습니다.", "pr": "PR을 만들고 있습니다."}
APPLYING = {"approved": "승인을 받아 반영을 준비하고 있습니다.", "merging": "PR을 병합하고 있습니다.",
            "merged": "PR을 병합했습니다.", "deploy_pending": "병합했습니다. 운영에는 다음 자동 배포 때 반영됩니다.",
            "complete": "반영을 마쳤습니다.", "blocked": "반영하다 확인이 필요한 문제가 생겨 멈췄습니다."}
GROUPS = ("승인 대기", "진행 중", "멈춤", "끝남")


def uuid(value):
    """A recorded ID from an observation payload, or None; a malformed one must not abort the reporter."""
    try:
        return UUID(str(value))
    except ValueError:
        return None


def clip(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def facts(conn, company, request_id, value):
    """What started the case (the owner's request or the first recorded failure) and how often it recurred."""
    job = conn.execute("SELECT * FROM maintenance_jobs WHERE id=%s", (request_id,)).fetchone()
    if not job:
        return {}
    if job["kind"] == "review":
        result = {"kind": "request", "text": job["payload"].get("instruction") or ""}
    else:
        rows = [row for row in job["payload"].get("observations", []) if isinstance(row, dict)]
        first = rows[0] if rows else {}
        detail = first.get("detail") if isinstance(first.get("detail"), dict) else {}
        employee = first.get("author")
        if first.get("kind") == "turn_blocked" and uuid(detail.get("turn_id")):
            row = conn.execute("SELECT k.agent FROM turns t JOIN tasks k ON k.id=t.task_id WHERE t.id=%s",
                               (uuid(detail["turn_id"]),)).fetchone()
            employee = row["agent"] if row else None
        elif first.get("kind") == "task_blocked" and uuid(detail.get("task_id")):
            row = conn.execute("SELECT agent FROM tasks WHERE id=%s", (uuid(detail["task_id"]),)).fetchone()
            employee = row["agent"] if row else None
        label = None
        if first.get("kind") == "data_watch_problem" and uuid(first.get("project_id")):
            row = conn.execute("SELECT title FROM projects WHERE id=%s", (uuid(first["project_id"]),)).fetchone()
            key = row["title"] if row else ""
            label = ("데이터 목록 조회" if key == "전체 목록 접근" else "연구 고정입력" if key.startswith("고정 입력 ")
                     else DATASETS.get(key.removeprefix("최신 레이크 "), key.removeprefix("최신 레이크 ")))
        seen = conn.execute("""SELECT count(*) AS n,min(body->>'created_at') AS first,max(body->>'created_at') AS last
            FROM maintenance_observations WHERE job_id=%s AND body->>'kind'=%s""",
                            (job["id"], first.get("kind"))).fetchone()
        role = company.roles.get(employee) if employee else None
        result = {"kind": first.get("kind"), "employee": role.name if role else employee, "at": first.get("created_at"),
                  "others": sum(row.get("kind") != first.get("kind") for row in rows), "label": label, "count": seen["n"],
                  "first_at": seen["first"], "last_at": seen["last"],
                  "detail": {key: detail[key] for key in ("stage", "attempt", "reason", "problem") if key in detail}}
    finding = conn.execute("SELECT payload->'finding' AS finding FROM maintenance_jobs WHERE id=%s",
                           (uuid(value.get("case_id")) or job["id"],)).fetchone()
    if finding and isinstance(finding["finding"], dict):
        result["expected"] = finding["finding"].get("expected")
    return result


def subject(about):
    """(headline, problem, impact, improvement) from what started the case, before any diagnosis."""
    kind, detail = about.get("kind"), about.get("detail") or {}
    name = about.get("employee") or "직원"
    when = kst(about["at"]) + ", " if about.get("at") else ""
    if kind == "request":
        lines = str(about.get("text") or "").strip().splitlines() or ["내용 없는 요청"]
        return ("요청: " + clip(lines[0], 60), clip(" ".join(lines), 280), None,
                "개선봇이 요청 내용을 조사해 필요한 수정을 정합니다.")
    if kind == "staff_independent_review_failure":
        return ("직원 교육 채점(Claude 검토)이 결과 없이 끝남",
                f"{when}{name}의 교육 답변을 Claude가 독립 검토하다가 결과를 확정하지 못했습니다.",
                "참고용 채점이라 직원 점수와 브리핑·영상 같은 실제 서비스에는 영향이 없습니다.",
                "Claude 검토 실행이 중간에 실패하지 않도록 고쳐야 합니다.")
    if kind == "research_contract_failure":
        stage = STAGES.get(detail.get("stage"), detail.get("stage") or "연구")
        return ("연구 단계가 응답 형식 오류로 멈춤",
                f"{when}승인된 연구의 '{stage}' 단계에서 {name}의 응답이 정해진 형식 검사를 "
                f"{detail.get('attempt') or '여러'}번 통과하지 못했습니다.", "이 연구는 이 단계에서 더 진행되지 않습니다.",
                "직원 응답과 형식 검사가 서로 맞도록 서비스 코드를 고쳐야 합니다. 연구 판단과 예산은 바꾸지 않습니다.")
    if kind in {"turn_blocked", "task_blocked"}:
        reason = REASONS.get(detail.get("reason"), detail.get("reason") or "원인 미기록")
        return (f"{name} 업무가 중간에 멈춤", f"{when}{name}의 업무 한 건이 '{reason}' 때문에 멈췄습니다.", None,
                "업무가 끝까지 진행되도록 멈춘 원인을 찾아 고쳐야 합니다.")
    if kind == "data_watch_problem":
        label = about.get("label") or "데이터"
        return (f"데이터 점검 문제: {label}",
                f"{when}데이터 점검에서 '{label}' 문제가 확인됐습니다: {problem_text(detail.get('problem'))}.", None,
                "데이터 수집·반영 경로를 확인해 문제를 없애야 합니다.")
    if kind == "staff_assessment":
        return (f"{name} 교육 문제 오답", f"{when}{name}이(가) 교육 문제에서 정답 기준을 통과하지 못했습니다.",
                "교육용 평가라 실제 업무 결과는 바뀌지 않습니다.", f"{name}의 업무 절차나 지침을 보완해야 합니다.")
    return ("개선이 필요한 문제", "기록된 업무 실패가 접수됐습니다.", None, "개선봇이 원인을 조사해 필요한 수정을 정합니다.")


def progress(value, runtime):
    """(지금, 할 일): where the case stands and whether the owner has to act."""
    state, error = value["state"], value.get("error")
    pr = value.get("pr") if isinstance(value.get("pr"), dict) else {}
    waiting = "없음. 진행되면 이 카드가 바뀝니다."
    application = (value.get("application") or {}).get("state")
    if application:
        return (APPLYING.get(application, "반영 상태를 확인하고 있습니다."),
                "반영 기록 확인이 필요합니다. 개발 작업으로 확인해 주세요." if application == "blocked"
                else "없음. 결과를 이 스레드에 알립니다.")
    if state == "blocked":
        happened, action = STOPS.get(error, (f"개선봇이 더 진행할 수 없는 상태입니다(기록: {error}).",
                                             "계속하려면 개발 작업이 필요합니다."))
        return "멈췄습니다. " + happened, action
    if error == "daily_model_budget":
        if runtime and runtime.get("daily_model_call_cap") is None and runtime.get("company_daily_model_call_cap") is None:
            return "일일 한도가 해제됐습니다. 순서가 오면 최신 기록으로 이어갑니다.", waiting
        resets = runtime.get("budget_resets_at")
        return ("오늘 모델 사용 한도에 걸려 기다립니다. " + (f"{kst(resets)} 이후 이어갑니다." if resets
                                                       else "한도가 풀리면 이어갑니다."), waiting)
    if error:
        return "다른 업무가 먼저라 잠시 기다립니다. 차례가 오면 이어서 진행합니다.", waiting
    if state == "pr_open":
        number = pr.get("number")
        status = (f"설계 PR #{number}이 준비됐습니다. 효과는 아직 검증 전입니다."
                  if value.get("evaluation_mode") == "design_only"
                  else f"수정안 PR #{number}이 준비됐고 시험을 통과했습니다. 운영 반영은 승인 후에 합니다.")
        return status, f"PR을 보고 괜찮으면 이 스레드에 ‘PR {number} 반영해’라고 답해 주세요."
    if state == "applied":
        return "반영을 마쳤습니다.", "없음."
    if state == "closed":
        return "PR이 반영되지 않고 닫혔습니다.", "없음."
    if state == "done":
        reason = clip(value.get("reason"), 160)
        return "조사를 마쳤고 고칠 곳은 찾지 못했습니다." + (f" 개선봇 설명: {reason}" if reason else ""), "없음."
    if state in WORKING:
        return WORKING[state], waiting
    return ("개선봇이 원인을 조사하고 있습니다" + ("(모델 응답 대기)." if value.get("model_wait") else "."), waiting)


def summary(value, runtime):
    about = value.get("facts") or {}
    headline, problem, impact, improvement = subject(about)
    cause = None
    old = value.get("historical_finding") or {}
    if value.get("title"):
        headline = clip(value["title"], 120)
        problem = clip(value.get("problem") or old.get("problem") or problem, 280)
        improvement = clip(about.get("expected") or value["title"], 280)
        if value.get("hypothesis"):
            cause = clip(value["hypothesis"], 200)
    if about.get("others"):
        problem += f" 같은 접수에 다른 종류의 기록 {about['others']}건이 함께 있습니다."
    if about.get("kind") == "request" and not value.get("title") and value["state"] in {"blocked", "done"}:
        improvement = "정해지지 않았습니다. 개선봇이 고칠 내용을 확정하지 못했습니다."
    status, action = progress(value, runtime)
    disposition = (value.get("assessment") or {}).get("disposition")
    if disposition == "resolved":
        status += " 이후 해결된 것으로 확인됐습니다."
    elif disposition == "invalidated":
        status += " 이후 확인에서 문제가 아니었던 것으로 정리됐습니다."
    recurrence = (f"{kst(about['first_at'])}부터 {about['count']}번 (마지막 {kst(about['last_at'])})"
                  if (about.get("count") or 0) > 1 else None)
    return {"headline": headline, "problem": problem, "impact": impact, "recurrence": recurrence,
            "improvement": improvement, "cause": cause, "status": status, "action": action}


def lines(plain):
    out = [f"무슨 문제: {plain['problem']}"]
    if plain["impact"]:
        out.append(f"영향: {plain['impact']}")
    if plain["recurrence"]:
        out.append(f"반복: {plain['recurrence']}")
    out.append(f"필요한 개선: {plain['improvement']}")
    if plain["cause"]:
        out.append(f"원인 추정(검증 전): {plain['cause']}")
    return out + [f"지금: {plain['status']}", f"할 일: {plain['action']}"]


def text(value, runtime):
    """A self-contained message: the headline, then the same lines as the case card."""
    plain = summary(value, runtime)
    return "\n".join([f"*{plain['headline']}*", *lines(plain)])


def notice(value, runtime):
    """A thread reply that pings the owner; the card above it keeps the full description."""
    plain = summary(value, runtime)
    return f"*{plain['headline']}*\n지금: {plain['status']}\n할 일: {plain['action']}"


def group(value):
    application = (value.get("application") or {}).get("state")
    if application:
        return "멈춤" if application == "blocked" else "끝남" if application == "complete" else "진행 중"
    if value["state"] == "pr_open" and not value.get("error"):
        return "승인 대기"
    if value["state"] == "blocked":
        return "멈춤"
    return "끝남" if value["state"] in {"applied", "closed", "done", "superseded"} else "진행 중"


def list_text(rows):
    """rows: (value, thread link) newest first. Repeats of one problem collapse into one line."""
    if not rows:
        return "개선 케이스 목록: 조회할 수 있는 케이스가 없습니다."
    counts, entries = dict.fromkeys(GROUPS, 0), {}
    for value, link in rows:
        name, plain = group(value), summary(value, {})
        counts[name] += 1
        entry = entries.setdefault((name, plain["headline"]), {"count": 0, "link": link, "note": {
            "승인 대기": plain["action"], "멈춤": plain["status"].removeprefix("멈췄습니다. "),
            "끝남": plain["status"]}.get(name, "")})
        entry["count"] += 1
    out = [f"개선 케이스 목록 · 최근 {len(rows)}건 · " + " · ".join(f"{name} {counts[name]}" for name in GROUPS)]
    for name in GROUPS:
        for (group_name, headline), entry in entries.items():
            if group_name == name:
                repeat = f" ×{entry['count']}" if entry["count"] > 1 else ""
                note = f" — {clip(entry['note'], 90)}" if entry["note"] else ""
                out.append(f"• [{name}] {headline}{repeat}{note} · {entry['link'] or '스레드 전달 대기'}")
    return "\n".join(out)
