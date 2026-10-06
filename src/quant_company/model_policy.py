"""Service-owned model selection. Only authenticated owner commands may change it."""

import re
from typing import get_args

from psycopg.types.json import Jsonb

from .command_help import HELP_TEXT as HELP_TEXT
from .contracts import ReasoningEffort

MODEL_ID = r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}"
EFFORT = "(?:" + "|".join(get_args(ReasoningEffort)) + ")"
ALIASES = {"총괄": "director", "개발": "engineer", "데이터": "data", "금융전략": "financial_strategist",
           "국내연구": "researcher_kr", "글로벌연구": "researcher_global", "가상자산연구": "researcher_crypto",
           "검증": "validator", "리스크": "risk", "운영": "operations", "뉴스": "reporter", "개선": "maintainer",
           "브리핑": "market_brief"}


def parse_command(text):
    value = text.strip()
    simple = {"모델 목록": "catalog", "모델 배정 상태": "status", "모델 배정 이력": "history"}
    if value in simple:
        return {"action": simple[value]}
    if match := re.fullmatch(rf"모델 지정 ([\w-]+) ({MODEL_ID}) ({EFFORT})", value):
        target, model, effort = match.groups()
        return {"action": "pin", "target": ALIASES.get(target, target), "model": model, "reasoning_effort": effort}
    if match := re.fullmatch(r"모델 자동 ([\w-]+)", value):
        target = match[1]
        return {"action": "reset", "target": ALIASES.get(target, target)}
    if match := re.fullmatch(r"모델 배정 복원 ([0-9]{1,12})", value):
        return {"action": "restore", "revision": int(match[1])}
    if match := re.fullmatch(rf"이번 작업 모델 ({MODEL_ID}) ({EFFORT})\n([\s\S]+)", value):
        if match[3].strip():
            return {"action": "task", "model": match[1], "reasoning_effort": match[2], "instruction": match[3].strip()}
    # A malformed selection must not become an ordinary task using the default model.
    if value.startswith(("모델 지정", "모델 자동", "모델 배정", "모델 목록", "이번 작업 모델")):
        return {"action": "help"}
    return None


def policy(conn):
    # Shared readers serialize against activation while allowing concurrent request preparation.
    return conn.execute("SELECT * FROM model_assignment_policy WHERE id=1 FOR SHARE").fetchone()


def targets(company):
    return ({key for key in company.roles if key != "tech_scout"}
            | ({"news_screening", "news_search"} if "reporter" in company.roles else set())
            | ({"maintainer"} if "engineer" in company.roles else set()))


def baseline(company, target):
    if target == "news_screening" or target == "news_search" and company.settings.news_optimization_enabled:
        return {"model": "gpt-5.6-luna", "reasoning_effort": "low"}
    name = "reporter" if target == "news_search" else "engineer" if target == "maintainer" else target
    role = company.roles[name]
    return {"model": role.model, "reasoning_effort": role.reasoning_effort}


def selection(company, conn, target, *, task=None, current=None, default=None):
    base = default or baseline(company, target)
    if not company.settings.model_assignments_enabled:
        return {**base, "source": "default", "revision": 0}
    if task and task.get("model_selection"):
        return task["model_selection"]
    current = current or policy(conn)
    chosen = current["bindings"].get(target)
    source = "pinned"
    if chosen is None and target == "maintainer":
        chosen = current["bindings"].get("engineer")
        source = "engineer"
    if chosen is None and target == "news_search" and not company.settings.news_optimization_enabled:
        chosen = current["bindings"].get("reporter")
        source = "reporter"
    return {**(chosen or base), "source": source if chosen else "default", "revision": current["revision"]}


def effective_role(company, conn, target, *, task=None, current=None):
    role = company.roles["engineer" if target == "maintainer" and target not in company.roles else target]
    chosen = selection(company, conn, target, task=task, current=current)
    return role.model_copy(update={key: chosen[key] for key in ("model", "reasoning_effort")})


def bind(company, conn, request, target, *, task=None, inherited=None):
    if not company.settings.model_assignments_enabled:
        return request
    chosen = inherited
    if request.session and request.session.previous_request_id:
        previous = conn.execute("SELECT request FROM turns WHERE id=%s", (request.session.previous_request_id,)).fetchone()
        if not previous or not previous["request"]:
            raise ValueError("Model session predecessor is missing")
        chosen = {"model": previous["request"]["model"], "reasoning_effort": previous["request"].get("reasoning_effort")}
        chosen.update(source="session", previous_request_id=request.session.previous_request_id)
    chosen = chosen or selection(company, conn, target, task=task,
                                default={"model": request.model, "reasoning_effort": request.reasoning_effort})
    conn.execute("INSERT INTO model_execution_bindings(request_id,target,selection) VALUES(%s,%s,%s)",
                 (request.request_id, target, Jsonb(chosen)))
    return request.model_copy(update={key: chosen[key] for key in ("model", "reasoning_effort")})


def status(company, conn):
    current = policy(conn)
    return {"enabled": company.settings.model_assignments_enabled, "revision": current["revision"],
            "assignments": {target: selection(company, conn, target, current=current)
                            for target in sorted(targets(company))},
            "precedence": ["task", "pinned", "default"],
            "independent_reviewer": "Separate Claude runtime; not managed by Codex assignments"}


def authorized(company, owner, channel, agent, command):
    settings = company.settings
    if owner != settings.model_accounts_owner_user or owner not in settings.slack_allowed_users or not channel:
        return False
    if command["action"] == "task":
        return channel != settings.model_accounts_channel_id and (
            channel in settings.slack_allowed_channels or channel.startswith("D"))
    if command["action"] == "help" and channel != settings.model_accounts_channel_id:
        return channel in settings.slack_allowed_channels or channel.startswith("D")
    return agent == "director" and channel == settings.model_accounts_channel_id and channel in settings.slack_allowed_channels


def enqueue(conn, company, project, task, event_key, command):
    conn.execute("""INSERT INTO model_assignment_commands(id,event_key,owner_user,command)
        VALUES(%s,%s,%s,%s)""", (task["id"], event_key, project["owner_user"], Jsonb(command)))
    conn.execute("UPDATE tasks SET status='waiting' WHERE id=%s", (task["id"],))
    if not company.settings.model_assignments_enabled:
        text = "모델 배정 기능이 아직 활성화되지 않았습니다. 운영 설정을 확인해 주세요."
        conn.execute("""UPDATE model_assignment_commands SET state='completed',completed_at=now(),receipt=%s
            WHERE id=%s""", (Jsonb({"outcome": "disabled"}), task["id"]))
        conn.execute("UPDATE tasks SET status='completed',result=%s WHERE id=%s", (text, task["id"]))
        company._message(conn, project, task["id"], task["agent"], "status", text)
