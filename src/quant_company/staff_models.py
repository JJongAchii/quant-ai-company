"""An operator-reviewed, atomic allocation; no Slack event or inference is synthesized."""

from psycopg.types.json import Jsonb
from pydantic import Field

from .company import PolicyError, fingerprint
from .contracts import StrictModel
from .model_policy import selection
from .providers.routing import CLAUDE_MODEL

TARGETS = ("director", "financial_strategist", "researcher_kr", "researcher_global", "researcher_crypto",
           "data", "engineer", "validator", "risk", "operations", "maintainer", "quant_scout")
PRESERVED = ("reporter", "market_brief", "news_screening", "news_search")


class StaffAllocation(StrictModel):
    operation_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    expected_revision: int = Field(ge=0, strict=True)
    dry_run: bool = Field(default=True, strict=True)
    plan_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def allocate(company, command):
    settings = company.settings
    if (not settings.model_assignments_enabled or not settings.model_accounts_enabled
            or settings.model_accounts_owner_user not in settings.slack_allowed_users):
        raise PolicyError("Configured owner and model assignments are required")
    with company.db.transaction() as conn:
        conn.execute("SELECT id FROM model_account_policy WHERE id=1 FOR SHARE")
        current = conn.execute("SELECT * FROM model_assignment_policy WHERE id=1 FOR UPDATE").fetchone()
        prior = conn.execute("""SELECT detail FROM events WHERE kind='staff_claude_allocation'
            AND detail->>'operation_id'=%s ORDER BY created_at DESC LIMIT 1""", (command.operation_id,)).fetchone()
        if prior:
            receipt = prior["detail"]
            if (receipt["expected_revision"] != command.expected_revision
                    or command.plan_digest not in {None, receipt["plan_digest"]}):
                raise PolicyError("Operation ID was already used for a different allocation")
            return {**receipt, "cached": True}
        if current["revision"] != command.expected_revision:
            raise PolicyError("Model policy revision changed; preview the current policy again")
        if not set(TARGETS) | {"reporter", "market_brief"} <= company.roles.keys():
            raise PolicyError("The expected staff roster is not installed")
        before = {target: selection(company, conn, target, current=current) for target in (*TARGETS, *PRESERVED)}
        after = dict(current["bindings"])
        for target in TARGETS:
            effort = before[target]["reasoning_effort"]
            if target == "director" and effort != "max":
                raise PolicyError("Director requires max effort")
            after[target] = {"model": CLAUDE_MODEL, "reasoning_effort": effort, "operation_id": command.operation_id}
        plan = {"operation_id": command.operation_id, "expected_revision": current["revision"],
                "before": current["bindings"], "after": after,
                "preserved": {target: before[target] for target in PRESERVED},
                "active_roles": sorted(key for key, role in company.roles.items() if role.active),
                "authority": "authenticated_operator", "new_claude_work_billing": "subscription_only"}
        digest = fingerprint(plan)
        if command.dry_run:
            return {"state": "preview", "plan_digest": digest, **plan}
        if command.plan_digest != digest:
            raise PolicyError("Apply requires the exact reviewed preview digest")
        revision = current["revision"] + 1
        conn.execute("UPDATE model_assignment_policy SET revision=%s,bindings=%s WHERE id=1",
                     (revision, Jsonb(after)))
        conn.execute("""INSERT INTO model_assignment_revisions(revision,bindings,owner_user)
            VALUES(%s,%s,%s)""", (revision, Jsonb(after), settings.model_accounts_owner_user))
        receipt = {"state": "applied", "revision": revision, "plan_digest": digest, **plan}
        company._event(conn, "staff_claude_allocation", receipt)
        return {**receipt, "cached": False}
