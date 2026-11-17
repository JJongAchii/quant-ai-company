"""Operator-reviewed, atomic allocations; no Slack event or inference is synthesized."""

from dataclasses import dataclass

from psycopg.types.json import Jsonb
from pydantic import Field

from .company import PolicyError, fingerprint
from .contracts import StrictModel
from .model_policy import selection
from .providers.routing import CLAUDE_MODEL, CLAUDE_SONNET

TARGETS = ("director", "financial_strategist", "researcher_kr", "researcher_global", "researcher_crypto",
           "data", "engineer", "validator", "risk", "operations", "maintainer")
# Quant Scout moved to the editorial allocation (owner, 2026-11-17: it does not need the strongest model), so each
# operation owns a disjoint set of targets and the two apply in either order.
PRESERVED = ("reporter", "market_brief", "news_screening", "news_search", "quant_scout")

# The news desk (editing and trends share the reporter target), screening, search and Quant Scout move to Sonnet at
# their current Codex efforts; the briefing keeps the strongest model (owner, 2026-11-17). Video, email and the
# independent reviewer are configured elsewhere and are not model assignment targets.
EDITORIAL = {"reporter": (CLAUDE_SONNET, "medium"), "news_screening": (CLAUDE_SONNET, "low"),
             "news_search": (CLAUDE_SONNET, "low"), "quant_scout": (CLAUDE_SONNET, "high"),
             "market_brief": (CLAUDE_MODEL, "xhigh")}


@dataclass(frozen=True)
class Plan:
    event: str
    targets: tuple
    preserved: tuple
    roles: frozenset
    # target -> (model, effort); None keeps each target's current effort on Opus (the staff allocation).
    fixed: dict | None = None

    def binding(self, target, before):
        if self.fixed is not None:
            return self.fixed[target]
        effort = before["reasoning_effort"]
        if target == "director" and effort != "max":
            raise PolicyError("Director requires max effort")
        return CLAUDE_MODEL, effort


STAFF = Plan("staff_claude_allocation", TARGETS, PRESERVED, frozenset({*TARGETS, "reporter", "market_brief"}))
EDITORIAL_PLAN = Plan("editorial_claude_allocation", tuple(EDITORIAL), TARGETS,
                      frozenset({"reporter", "market_brief", "quant_scout"}), EDITORIAL)


class StaffAllocation(StrictModel):
    operation_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    expected_revision: int = Field(ge=0, strict=True)
    dry_run: bool = Field(default=True, strict=True)
    plan_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def allocate(company, command, plan=STAFF):
    settings = company.settings
    if (not settings.model_assignments_enabled or not settings.model_accounts_enabled
            or settings.model_accounts_owner_user not in settings.slack_allowed_users):
        raise PolicyError("Configured owner and model assignments are required")
    with company.db.transaction() as conn:
        conn.execute("SELECT id FROM model_account_policy WHERE id=1 FOR SHARE")
        current = conn.execute("SELECT * FROM model_assignment_policy WHERE id=1 FOR UPDATE").fetchone()
        # An operation ID belongs to one allocation of any kind; reusing it for another kind is refused.
        prior = conn.execute("""SELECT kind,detail FROM events WHERE kind IN ('staff_claude_allocation',
            'editorial_claude_allocation') AND detail->>'operation_id'=%s ORDER BY created_at DESC LIMIT 1""",
                             (command.operation_id,)).fetchone()
        if prior:
            receipt = prior["detail"]
            if (prior["kind"] != plan.event or receipt["expected_revision"] != command.expected_revision
                    or command.plan_digest not in {None, receipt["plan_digest"]}):
                raise PolicyError("Operation ID was already used for a different allocation")
            return {**receipt, "cached": True}
        if current["revision"] != command.expected_revision:
            raise PolicyError("Model policy revision changed; preview the current policy again")
        if not plan.roles <= company.roles.keys():
            raise PolicyError("The expected staff roster is not installed")
        before = {target: selection(company, conn, target, current=current)
                  for target in (*plan.targets, *plan.preserved)}
        after = dict(current["bindings"])
        for target in plan.targets:
            model, effort = plan.binding(target, before[target])
            after[target] = {"model": model, "reasoning_effort": effort, "operation_id": command.operation_id}
        receipt_plan = {"operation_id": command.operation_id, "expected_revision": current["revision"],
                        "before": current["bindings"], "after": after,
                        "preserved": {target: before[target] for target in plan.preserved},
                        "active_roles": sorted(key for key, role in company.roles.items() if role.active),
                        "authority": "authenticated_operator", "new_claude_work_billing": "subscription_only"}
        digest = fingerprint(receipt_plan)
        if command.dry_run:
            return {"state": "preview", "plan_digest": digest, **receipt_plan}
        if command.plan_digest != digest:
            raise PolicyError("Apply requires the exact reviewed preview digest")
        revision = current["revision"] + 1
        conn.execute("UPDATE model_assignment_policy SET revision=%s,bindings=%s WHERE id=1",
                     (revision, Jsonb(after)))
        conn.execute("""INSERT INTO model_assignment_revisions(revision,bindings,owner_user)
            VALUES(%s,%s,%s)""", (revision, Jsonb(after), settings.model_accounts_owner_user))
        receipt = {"state": "applied", "revision": revision, "plan_digest": digest, **receipt_plan}
        company._event(conn, plan.event, receipt)
        return {**receipt, "cached": False}
