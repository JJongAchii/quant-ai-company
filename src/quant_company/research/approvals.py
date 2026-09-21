"""Deterministic owner decisions and server-issued Slack controls; no model authority.

Mission implementations may append adapters to ``company.research_approval_adapters``.
All adapter operations run with the owning project locked in the caller transaction.
"""

import re
from decimal import Decimal
from typing import Literal, Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import AwareDatetime, Field, model_validator

from ..company import PolicyError
from ..contracts import StrictModel

ACTIONS = {"research_approve": "approve", "research_cancel": "cancel"}


class ApprovalTarget(StrictModel):
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    target_id: UUID
    revision: int = Field(ge=1)
    manifest_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    title: str = Field(min_length=1, max_length=200)
    state: str
    created_at: AwareDatetime


class ApprovalEvent(StrictModel):
    schema_version: Literal[1] = 1
    origin: Literal["event_callback", "block_actions"]
    team_id: str
    app_id: str
    owner: str
    channel: str
    thread_ts: str
    event_ts: str = Field(pattern=r"^[0-9]+\.[0-9]+$")
    provider_event_id: str | None = None
    original_text: str = Field(max_length=10000)
    binding: UUID | None = None
    action: Literal["approve", "cancel"] | None = None
    message_ts: str | None = None

    @model_validator(mode="after")
    def action_origin(self):
        supplied = (self.binding is not None, self.action is not None, self.message_ts is not None)
        if (self.origin == "block_actions" and not all(supplied)) or (self.origin == "event_callback" and any(supplied)):
            raise ValueError("Invalid approval origin fields")
        return self

    def event_key(self):
        key = f"slack:{self.team_id}:{self.channel}:{self.event_ts}:director"
        return key if self.origin == "event_callback" else f"{key}:action:{self.binding}:{self.action}"


class ApprovalAdapter(Protocol):
    kind: str

    def targets(self, conn, project) -> list[ApprovalTarget]: ...

    def apply(self, conn, project, task, event_key: str, action: str, target: ApprovalTarget) -> None: ...


class ReplayApprovalAdapter:
    kind = "p11"

    def __init__(self, company):
        self.company = company

    def targets(self, conn, project):
        from .recipes import load_recipe, recipe_digest

        rows = conn.execute("""SELECT * FROM research_jobs WHERE project_id=%s
            AND recipe_id='kr-etf-p11-replay-v1' ORDER BY created_at,id""",
                            (project["id"],)).fetchall()
        targets = []
        for row in rows:
            recipe = load_recipe(row["recipe_id"])
            state = row["state"] if row["manifest_digest"] == recipe_digest(recipe) else "stale"
            targets.append(ApprovalTarget(kind=self.kind, target_id=row["id"], revision=row["revision"],
                manifest_digest=row["manifest_digest"], title=recipe.title, state=state, created_at=row["created_at"]))
        return targets

    def apply(self, conn, project, task, event_key, action, target):
        from .store import ResearchStore

        ResearchStore(self.company).owner_command(conn, project, task, event_key, {
            "action": action, "job_id": str(target.target_id), "digest": target.manifest_digest[:12]})


def adapters(company):
    values = (ReplayApprovalAdapter(company), *getattr(company, "research_approval_adapters", ()))
    result = {value.kind: value for value in values}
    if len(result) != len(values):
        raise PolicyError("Duplicate research approval adapter")
    return result


def targets(company, conn, project):
    return [target for adapter in adapters(company).values() for target in adapter.targets(conn, project)]


def short_command(text):
    value = re.sub(r"[.!。]+$", "", text.strip()).strip()
    if re.fullmatch(r"(?:연구\s+)?(?:승인|승인해|승인해줘|승인해 줘|승인해 주세요|승인합니다)", value):
        return {"action": "approve_pending"}
    if value == "연구 취소":
        return {"action": "cancel_pending"}
    if value.startswith(("연구 승인 ", "연구 취소 ")):
        return {"action": "clarify"}
    return None


def has_targets(company, owner, channel, thread_ts):
    """Keep short research replies out of the unrelated maintenance approval parser."""
    with company.db.transaction() as conn:
        project = conn.execute("SELECT * FROM projects WHERE owner_user=%s AND channel=%s AND thread_ts=%s",
                               (owner, channel, thread_ts)).fetchone()
        return bool(project and targets(company, conn, project))


def pending_maintenance(conn, project):
    # A short reply in a mixed thread must not silently choose research over an announced PR.
    if not conn.execute("SELECT to_regclass('maintenance_jobs') AS name").fetchone()["name"]:
        return []
    choices = []
    for row in conn.execute("SELECT id,receipt FROM maintenance_jobs WHERE kind='repair' AND state='pr_open' AND receipt ? 'pr'").fetchall():
        notice_id = uuid5(NAMESPACE_URL, f"maintenance-pr:{row['id']}:{project['id']}")
        if conn.execute("SELECT 1 FROM outbox WHERE id=%s AND project_id=%s AND status='delivered'",
                        (notice_id, project["id"])).fetchone():
            choices.append(f"PR #{row['receipt']['pr']['number']} 반영해")
    return choices


def publish_approval(company, conn, project, task_id, target: ApprovalTarget, text: str):
    target = ApprovalTarget.model_validate(target)
    if (project["status"] != "active" or target.revision != project["revision"]
            or target.state != "pending_approval" or not project["channel"] or not project["thread_ts"]
            or project["owner_user"] not in company.settings.slack_allowed_users
            or target not in targets(company, conn, project)):
        raise PolicyError("Approval notice requires a current server specification")
    message_id, binding = uuid4(), uuid4()
    company._message(conn, project, task_id, "director", "status", text,
                     message_id=str(message_id), notify_owner=True)
    conn.execute("""INSERT INTO research_approval_bindings
        (id,message_id,project_id,target_kind,target_id,revision,manifest_digest,owner_user,team_id,channel,thread_ts)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (binding, message_id, project["id"], target.kind, target.target_id, target.revision,
         target.manifest_digest, project["owner_user"], company.settings.slack_team_id,
         project["channel"], project["thread_ts"]))
    return str(binding)


def blocks_for_outbox(company, row, credential):
    """Only persisted server notices get controls; AgentDecision has no blocks input."""
    with company.db.transaction() as conn:
        binding = conn.execute("SELECT * FROM research_approval_bindings WHERE message_id=%s FOR UPDATE",
                               (row["id"],)).fetchone()
        if not binding:
            return None
        project = conn.execute("SELECT * FROM projects WHERE id=%s", (binding["project_id"],)).fetchone()
        if (row["agent"] != "director" or str(row["project_id"]) != str(binding["project_id"])
                or row["revision"] != binding["revision"] or row["channel"] != binding["channel"]
                or row["thread_ts"] != binding["thread_ts"] or project["owner_user"] != binding["owner_user"]
                or company.settings.slack_team_id != binding["team_id"]):
            raise PolicyError("Approval message identity changed")
        current = [t for t in targets(company, conn, project) if _matches(t, binding)]
        if (project["status"] != "active" or project["revision"] != binding["revision"]
                or len(current) != 1 or current[0].state != "pending_approval"):
            return None
        if binding["app_id"] and binding["app_id"] != credential["app_id"]:
            raise PolicyError("Approval Slack app identity changed")
        conn.execute("UPDATE research_approval_bindings SET app_id=%s WHERE id=%s",
                     (credential["app_id"], binding["id"]))
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": row["text"][start:start + 2900]}}
              for start in range(0, len(row["text"]), 2900)]
    blocks.append({"type": "actions", "block_id": "research_approval:" + str(binding["id"]), "elements": [
        {"type": "button", "text": {"type": "plain_text", "text": label}, "action_id": action_id,
         "value": str(binding["id"]), "style": style}
        for action_id, label, style in (("research_approve", "승인", "primary"), ("research_cancel", "취소", "danger"))]})
    return blocks


def _matches(target, binding):
    return (target.kind == binding["target_kind"] and target.target_id == binding["target_id"]
            and target.revision == binding["revision"] and target.manifest_digest == binding["manifest_digest"])


def _explain(company, conn, project, task, choices, reason):
    text = reason + "\n현재 스레드의 연구 명세를 확인한 뒤 해당 승인 메시지의 버튼이나 전체 명령을 사용해 주세요."
    text += "\n" + ("\n".join(f"• {t.title} · v{t.revision} · {t.state} · {t.target_id} · {t.manifest_digest[:12]}"
                              for t in choices[-10:]) or "등록된 연구 명세가 없습니다.")
    conn.execute("UPDATE tasks SET status='completed',result=%s WHERE id=%s", (text, task["id"]))
    company._message(conn, project, task["id"], "director", "status", text)
    return "target_required"


def apply_owner_command(company, conn, project, task, event_key, command, context=None):
    from .store import ResearchStore

    if not event_key.startswith("slack:") or project["owner_user"] not in company.settings.slack_allowed_users:
        raise PolicyError("Research commands require authenticated Slack owner input")
    event = ApprovalEvent.model_validate(context) if context else None
    if event and (event.event_key() != event_key or event.owner != project["owner_user"]
                  or event.channel != project["channel"] or event.thread_ts != project["thread_ts"]
                  or event.team_id != company.settings.slack_team_id):
        raise PolicyError("Research owner event does not match this thread")
    if command["action"] in {"approve", "cancel", "status"} and not (event and event.binding):
        result = ResearchStore(company).owner_command(conn, project, task, event_key, command)
        target = command.get("job_id")
    else:
        choices = targets(company, conn, project)
        current = [t for t in choices if t.state == "pending_approval" and t.revision == project["revision"]]
        if event and event.binding:
            binding = conn.execute("""SELECT b.*,o.sent_ts,o.status AS delivery_status FROM research_approval_bindings b
                JOIN outbox o ON o.id=b.message_id WHERE b.id=%s AND b.project_id=%s""",
                (event.binding, project["id"])).fetchone()
            if (not binding or binding["owner_user"] != event.owner or binding["team_id"] != event.team_id
                    or binding["app_id"] != event.app_id or binding["channel"] != event.channel
                    or binding["thread_ts"] != event.thread_ts or binding["sent_ts"] != event.message_ts
                    or binding["delivery_status"] != "delivered"):
                raise PolicyError("Research approval button does not match its delivered message")
            current = [t for t in current if _matches(t, binding)]
        # The event must be newer than the specification, so a delayed short reply cannot approve a later draft.
        fresh = bool(event and len(current) == 1 and Decimal(event.event_ts) >= Decimal(str(current[0].created_at.timestamp())))
        other = pending_maintenance(conn, project) if not (event and event.binding) else []
        if project["status"] != "active" or len(current) != 1 or not fresh or command["action"] == "clarify" or other:
            reason = "승인 대상을 하나로 확인할 수 없거나 이미 지난 명세입니다."
            if other:
                reason += "\n이 스레드에는 PR 승인도 있습니다: " + ", ".join(other)
            result = _explain(company, conn, project, task, choices, reason)
            target = None
        else:
            selected = current[0]
            action = event.action if event and event.binding else command["action"].removesuffix("_pending")
            company._event(conn, "research_approval_authorized", {"schema_version": 1,
                "owner_event_id": event_key, "task_id": str(task["id"]), "action": action,
                "target": selected.model_dump(mode="json"), "provenance": event.model_dump(mode="json")}, project["id"])
            adapters(company)[selected.kind].apply(conn, project, task, event_key, action, selected)
            target, result = str(selected.target_id), action
    company._event(conn, "research_owner_decision", {"schema_version": 1, "owner_event_id": event_key,
        "target_id": target, "result": result, "request_text": task["instruction"],
        "provenance": event.model_dump(mode="json") if event else None}, project["id"])
