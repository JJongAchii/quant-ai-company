"""Bounded real-subscription editorial check against two frozen originals; never publishes."""

import argparse
import asyncio
import copy
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from quant_company.company import Company, fingerprint
from quant_company.config import Settings
from quant_company.contracts import ProviderFault, ProviderRequest, ProviderResponse
from quant_company.execution import provider_for
from quant_company.quant_feed.contracts import QUANT_FEED_AGENT
from quant_company.quant_feed.editor import ProposalValidationError, output_contract, prompt, render, validate
from quant_company.quant_feed.store import QuantFeedStore


def qualification_request(role, label, bundle, stage, policy):
    body = dict(model=role.model, reasoning_effort=role.reasoning_effort,
                prompt=prompt(bundle, stage), output_contract=output_contract(stage))
    return ProviderRequest(request_id="quant-feed-qualify-" + fingerprint([label, policy, body])[:40], **body)


async def qualify(company, negative_id, positive_id, output, *, provider=None, reuse=None):
    if company.settings.quant_feed_publish_enabled or company.settings.model_provider != "codex":
        raise ValueError("qualification_requires_subscription_preview")
    store = QuantFeedStore(company)
    if not store.authorized():
        raise ValueError("quant_not_authorized")
    provider = provider or provider_for(company)
    role = company.roles[QUANT_FEED_AGENT]
    reused = None
    reuse_digest = None
    if reuse is not None:
        raw = reuse.read_bytes()
        reuse_digest = hashlib.sha256(raw).hexdigest()
        reused = json.loads(raw)
        if (reused["state"] != "not_passed"
                or reused["policy"] == store.policy()
                or reused["inputs"]["negative"]["document_id"] != negative_id
                or reused["inputs"]["positive"]["document_id"] != positive_id
                or not {"negative-critic", "negative-review"} <= {row["case"] for row in reused["calls"]}
                or not any(row["case"].startswith("positive-") and row["stage"] in {"review", "repair", "revision"}
                           for row in reused["calls"])
                or any(row["state"] not in {"returned", "validated"} for row in reused["calls"])):
            raise ValueError("only_settled_complete_cases_under_a_changed_policy_may_be_revalidated")
    if output.exists():
        receipt = json.loads(output.read_text())
        if (
            receipt["policy"] != store.policy()
            or receipt["inputs"]["negative"]["document_id"] != negative_id
            or receipt["inputs"]["positive"]["document_id"] != positive_id
            or receipt.get("reuse_receipt_sha256") != reuse_digest
        ):
            raise ValueError("qualification_receipt_inputs_changed")
        if receipt["state"] != "running":
            return receipt  # A blocked/uncertain attempt needs reconciliation, never a new ID.
    else:
        if reused is not None:
            inputs = copy.deepcopy(reused["inputs"])
        else:
            inputs = load_inputs(company, store, negative_id, positive_id)
        receipt = {
            "checked_at": datetime.now(UTC).isoformat(),
            "policy": store.policy(),
            "inputs": inputs,
            "reuse_receipt_sha256": reuse_digest,
            "mode": "subscription-frozen-originals",
            "publication_enabled": False,
            "document_writes": False,
            "slack_writes": False,
            "calls": [],
            "revalidated_responses": [],
            "state": "running",
        }
    bundles = copy.deepcopy(receipt["inputs"])

    def revalidate(label, bundle, stage):
        rows = [r for r in reused["calls"] if (r["case"] == label if label.startswith("negative-")
                                               else r["case"].startswith("positive-") and r["stage"] != "critique")]
        row = rows[-1]
        response = ProviderResponse.model_validate(row["response"])
        if response.request_id != row["request_id"]:
            raise ValueError("qualification_receipt_response_mismatch")
        audit = []
        value = validate(response, bundle, stage, audit=audit)
        receipt.setdefault("revalidated_responses", []).append({
            "case": label, "original_request_id": response.request_id, "model_reissued": False,
            "source_corrections": audit, "disposition": value.disposition})
        return value

    def save():
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = output.with_name(output.name + ".tmp")
        with temporary.open("w") as handle:
            os.chmod(temporary, 0o600)
            handle.write(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(output)
        directory = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    async def call(label, bundle, stage):
        request = qualification_request(role, label, bundle, stage, receipt["policy"])
        row = {
            "case": label,
            "stage": stage,
            "request_id": request.request_id,
            "document_id": bundle["document_id"],
            "original_sha256": bundle["original_sha256"],
            "prompt_sha256": fingerprint(request.prompt),
            "output_contract": request.output_contract,
            "state": "requested",
        }
        previous = next((item for item in receipt["calls"] if item["request_id"] == request.request_id), None)
        if previous:
            row = previous
            if row["state"] not in {"returned", "validated"} or "response" not in row:
                raise ProviderFault(
                    "uncertain", "A previous call has no recorded response; reconcile its request ID."
                )
            response = ProviderResponse.model_validate(row["response"])
            if response.request_id != request.request_id:
                raise ValueError("qualification_receipt_response_mismatch")
        else:
            receipt["calls"].append(row)
            save()  # Durably persist the ID before a potentially ambiguous remote call.
            try:
                response = await provider.run(request)
            except ProviderFault as exc:
                row.update(state="fault", fault=exc.code)
                save()
                raise
            except Exception as exc:
                row.update(state="uncertain", exception_type=type(exc).__name__)
                save()
                raise ProviderFault("uncertain", "Runtime outcome requires reconciliation.") from exc
            row.update(state="returned", response=response.model_dump(mode="json"))
            save()
        if response.request_id != request.request_id:
            raise ValueError("qualification_receipt_response_mismatch")
        corrections = []
        try:
            value = validate(response, bundle, stage, audit=corrections)
        except ProposalValidationError as exc:
            row.update(validation_issues=exc.issues)
            save()
            raise
        row.update(state="validated", disposition=value.disposition, source_corrections=corrections)
        save()
        return value

    receipt["case_results"] = {"negative": {"state": "not_run"}, "positive": {"state": "not_run"}}
    active_case = "negative"
    try:
        try:
            negative = bundles["negative"]
            if not negative.get("draft"):
                raise ValueError("negative_case_requires_previous_false_positive")
            rejected = (revalidate("negative-critic", negative, "critique") if reused is not None
                        else await call("negative-critic", negative, "critique"))
            if rejected.disposition == "pass" or (rejected.direct_quant_scope and rejected.substantive_research):
                raise ValueError("negative_critic_missed_scope_or_depth")
            negative.update(draft=None, previous_critique=None)
            review = (revalidate("negative-review", negative, "review") if reused is not None
                      else await call("negative-review", negative, "review"))
            if review.disposition != "reject":
                raise ValueError("negative_review_not_rejected")
            receipt["case_results"]["negative"] = {"state": "passed"}
        except ValueError as exc:
            receipt["case_results"]["negative"] = {"state": "not_passed", "error": str(exc)}
        save()
        active_case = "positive"
        positive = bundles["positive"]
        positive.update(draft=None)
        repair_used = reused is not None  # Reusing a repaired draft must not reset its technical budget.

        async def review_positive(stage):
            nonlocal repair_used
            try:
                return await call("positive-" + stage, positive, stage)
            except ProposalValidationError as exc:
                if repair_used:
                    raise
                repair_used = True
                positive.update(draft=exc.draft, previous_critique=exc.feedback(positive.get("previous_critique")))
                return await call("positive-" + stage + "-repair", positive, "repair")

        draft = (revalidate("positive-draft", positive, "review") if reused is not None
                 else await review_positive("review"))
        if draft.disposition != "publish":
            raise ValueError("positive_original_not_publishable")
        positive["draft"] = draft.model_dump(mode="json")
        critic = await call("positive-critic", positive, "critique")
        revision_used = reused is not None and any(r["stage"] == "revision" for r in reused["calls"])
        if critic.disposition == "revise" and not revision_used:
            positive["previous_critique"] = critic.model_dump(mode="json")
            draft = await review_positive("revision")
            if draft.disposition != "publish":
                raise ValueError("positive_revision_not_publishable")
            positive["draft"] = draft.model_dump(mode="json")
            critic = await call("positive-final-critic", positive, "critique")
        if critic.disposition != "pass":
            raise ValueError("positive_critique_not_passed")
        receipt["case_results"]["positive"] = {"state": "passed"}
        receipt.update(state="passed" if receipt["case_results"]["negative"]["state"] == "passed"
                       else "not_passed", card=render(draft, positive["metadata"]))
    except ProviderFault as exc:
        receipt["case_results"][active_case] = {"state": "blocked", "fault": exc.code}
        receipt.update(state="blocked", fault=exc.code)
    except ValueError as exc:
        receipt["case_results"][active_case] = {"state": "not_passed", "error": str(exc)}
        receipt.update(state="not_passed", error=str(exc))
    finally:
        save()
    return receipt


def load_inputs(company, store, negative_id, positive_id):
    with company.db.transaction() as conn:
        inputs = {}
        for label, identity in (("negative", negative_id), ("positive", positive_id)):
            document = conn.execute("SELECT * FROM quant_feed_documents WHERE id=%s", (identity,)).fetchone()
            if not document:
                raise ValueError("qualification_document_missing")
            inputs[label] = store.bundle(conn, document)
        for bundle in inputs.values():
            bundle.update(prior=None, previous_critique=None)
        return inputs


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--negative", required=True)
    parser.add_argument("--positive", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--live", required=True, action="store_true")
    parser.add_argument("--reuse", type=Path, help="Revalidate a settled draft without reissuing calls or resetting its revision budget")
    args = parser.parse_args()
    receipt = await qualify(Company(Settings()), args.negative, args.positive, args.output, reuse=args.reuse)
    print(json.dumps({key: receipt.get(key) for key in ("state", "fault", "error", "slack_writes")}))
    if receipt["state"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
