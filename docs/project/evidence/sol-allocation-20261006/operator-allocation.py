"""Owner-requested one-shot configuration change inside the company API container.

Run through /app/entrypoint.py so credentials stay inside the application boundary.
This is an operator action, not a synthesized Slack event or model task.
"""

import json
import os
import sys
import urllib.request
from datetime import UTC, datetime
from typing import Literal

from psycopg.types.json import Jsonb
from pydantic import Field

from quant_company.company import Company, fingerprint
from quant_company.config import Settings
from quant_company.contracts import ReasoningEffort, StrictModel
from quant_company.model_control import validate_selection
from quant_company.model_policy import authorized, baseline, selection, targets
from quant_company.providers.model_catalog import validate_models


class Account(StrictModel):
    profile: Literal["primary", "backup"]
    revision: int = Field(ge=0)


class Choice(StrictModel):
    model: Literal["gpt-6.1-sol"]
    reasoning_effort: ReasoningEffort


class Plan(StrictModel):
    operation_id: str = Field(pattern=r"^[a-z0-9-]{1,120}$")
    source_request: str = Field(min_length=1, max_length=1000)
    owner_user: str
    expected_revision: int = Field(ge=0)
    expected_account: Account
    assignments: dict[str, Choice] = Field(min_length=1, max_length=32)


def main():
    mode = sys.argv[1]
    if mode not in {"preview", "apply", "receipt"}:
        raise ValueError("Unknown operator mode")
    payload = json.load(sys.stdin)
    plan = Plan.model_validate(payload["plan"])
    plan_data = plan.model_dump(mode="json")
    digest = fingerprint(plan_data)
    company = Company(Settings())
    settings = company.settings
    token = settings.require_operator_token()
    api_url = os.environ.get("SOL_ALLOCATION_API_URL", "http://127.0.0.1:8000")
    request = urllib.request.Request(api_url + "/v1/model-assignments",
                                     headers={"Authorization": "Bearer " + token})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=30) as response:
        if not json.load(response)["enabled"]:
            raise ValueError("Live operator API has assignments disabled")
    if not settings.model_accounts_enabled or not settings.model_assignments_enabled:
        raise ValueError("Assignment control must remain enabled")
    command_scope = {"action": "pin"}
    if not authorized(company, plan.owner_user, settings.model_accounts_channel_id, "director", command_scope):
        raise ValueError("The approving owner is not configured for model control")
    with company.db.transaction() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("operator-models:" + plan.operation_id,))
        prior = conn.execute("""SELECT detail FROM events WHERE kind='operator_model_assignment'
            AND detail->>'operation_id'=%s ORDER BY id LIMIT 1""", (plan.operation_id,)).fetchone()
        if prior:
            if prior["detail"]["plan_sha256"] != digest:
                raise ValueError("Operation ID is already bound to different input")
            return {**prior["detail"], "cached": True}
        if mode == "receipt":
            return {"operation_id": plan.operation_id, "state": "not_committed", "writes": 0}
        catalog = payload["catalog"]
        models = validate_models(catalog["models"])
        if catalog["account"] != plan.expected_account.model_dump() or catalog["cli_version"] != "0.160.1":
            raise ValueError("Catalog account or validated runtime changed")
        checked = datetime.fromisoformat(catalog["checked_at"].replace("Z", "+00:00"))
        if checked.tzinfo is None:
            raise ValueError("Catalog timestamp must include a timezone")
        age = (datetime.now(UTC) - checked).total_seconds()
        if not 0 <= age <= 600:
            raise ValueError("Catalog is not fresh; refresh read-only discovery")
        account = conn.execute("SELECT profile,revision FROM model_account_policy WHERE id=1 FOR SHARE").fetchone()
        current = conn.execute("SELECT * FROM model_assignment_policy WHERE id=1 FOR UPDATE").fetchone()
        if account != plan.expected_account.model_dump() or current["revision"] != plan.expected_revision:
            raise ValueError("Account or policy revision changed; no assignment was applied")
        if conn.execute("SELECT EXISTS(SELECT 1 FROM model_assignment_commands WHERE state='requested') AS pending").fetchone()["pending"]:
            raise ValueError("Existing owner commands must complete before this batch")
        desired = {target for target in targets(company) if baseline(company, target)["model"] != "unused"}
        if set(plan.assignments) != desired:
            raise ValueError("Proposal must cover the registered model-using roles and background jobs")
        updated = dict(current["bindings"])
        for target, choice in plan.assignments.items():
            chosen = choice.model_dump()
            validate_selection(target, chosen, models)
            updated[target] = chosen
        next_revision = current["revision"] + 1
        effective = {target: selection(company, conn, target, current={"revision": next_revision, "bindings": updated})
                     for target in sorted(desired)}
        if any(value["model"] != "gpt-6.1-sol" for value in effective.values()):
            raise ValueError("Effective allocation does not match the owner-requested model")
        result = {"schema_version": 1, "operation_id": plan.operation_id, "plan_sha256": digest,
                  "origin": "explicit_owner_request_via_authenticated_operator", "source_request": plan.source_request,
                  "approved_owner": plan.owner_user, "actor": "operator", "account": account,
                  "before_revision": current["revision"], "revision": next_revision, "before": current["bindings"],
                  "after": updated, "effective_assignments": effective, "catalog": catalog,
                  "active_roles": sorted(key for key, role in company.roles.items() if role.active),
                  "state": "preview" if mode == "preview" else "applied", "at": datetime.now(UTC).isoformat(),
                  "slack_messages_sent": 0, "model_inference_calls": 0}
        if mode == "preview":
            return {**result, "writes": 0}
        conn.execute("UPDATE model_assignment_policy SET revision=%s,bindings=%s WHERE id=1",
                     (next_revision, Jsonb(updated)))
        # Nullable command_id distinguishes this operator revision from a Slack task.
        conn.execute("""INSERT INTO model_assignment_revisions(revision,bindings,command_id,owner_user)
            VALUES(%s,%s,NULL,'operator')""", (next_revision, Jsonb(updated)))
        company._event(conn, "operator_model_assignment", result)
    return {**result, "cached": False}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2, default=str))
