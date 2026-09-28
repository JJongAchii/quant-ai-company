"""One real subscription critique of a previously source-validated Quant preview brief."""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from quant_company.company import Company, fingerprint
from quant_company.config import Settings
from quant_company.contracts import (
    AgentDecision,
    ArtifactDraft,
    ProviderFault,
    ProviderRequest,
    ProviderResponse,
)
from quant_company.execution import provider_for
from quant_company.quant_feed.contracts import QUANT_FEED_AGENT
from quant_company.quant_feed.editor import prompt, render, validate
from quant_company.quant_feed.store import QuantFeedStore


def persist(path, receipt):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as handle:
        os.chmod(temporary, 0o600)
        handle.write(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


async def qualify(document_id, output):
    company = Company(Settings())
    if company.settings.model_provider != "codex" or company.settings.quant_feed_publish_enabled:
        raise ValueError("subscription_preview_requires_paused_quant")
    store = QuantFeedStore(company)
    if not store.authorized():
        raise ValueError("quant_not_authorized")
    policy = store.policy()
    if output.exists():
        receipt = json.loads(output.read_text())
        if receipt["policy"] != policy or receipt["document_id"] != document_id:
            raise ValueError("existing_critic_receipt_inputs_changed")
        if receipt["state"] != "running":
            return receipt
    else:
        with company.db.transaction() as conn:
            document = conn.execute("SELECT * FROM quant_feed_documents WHERE id=%s", (document_id,)).fetchone()
            if not document or document["state"] != "preview" or not document["brief"]:
                raise ValueError("existing_positive_preview_missing")
            bundle = store.bundle(conn, document)
            bundle.update(prior=None, previous_critique=None)
            original_brief = document["brief"]
        synthetic = ProviderResponse(
            request_id="quant-existing-brief-validation", provider="fixture",
            decision=AgentDecision(status="complete", say="", artifacts=[ArtifactDraft(
                title="existing brief", content=json.dumps(original_brief, ensure_ascii=False))]),
        )
        corrections = []
        brief = validate(synthetic, bundle, "review", audit=corrections)
        if brief.disposition != "publish" or corrections:
            raise ValueError("existing_brief_not_source_validated_without_changes")
        bundle["draft"] = brief.model_dump(mode="json")
        card = render(brief, bundle["metadata"])
        receipt = {"state": "running", "checked_at": datetime.now(UTC).isoformat(),
                   "policy": policy, "document_id": document_id,
                   "original_sha256": bundle["original_sha256"],
                   "brief_sha256": fingerprint(bundle["draft"]),
                   "mode": "subscription-existing-brief-new-critic",
                   "publication_enabled": False, "document_writes": False, "slack_writes": False,
                   "bundle": bundle, "card": card, "call": None}
        persist(output, receipt)
    bundle = receipt["bundle"]
    role = company.roles[QUANT_FEED_AGENT]
    request = ProviderRequest(
        request_id="quant-feed-existing-critic-" + fingerprint([document_id, prompt(bundle, "critique")])[:40],
        model=role.model, reasoning_effort=role.reasoning_effort,
        prompt=prompt(bundle, "critique"),
    )
    row = receipt["call"]
    try:
        if row is None:
            row = {"request_id": request.request_id, "prompt_sha256": fingerprint(request.prompt),
                   "state": "requested"}
            receipt["call"] = row
            persist(output, receipt)  # ID is durable before an ambiguous external call.
            try:
                response = await provider_for(company).run(request)
            except ProviderFault as exc:
                row.update(state="fault", fault=exc.code)
                persist(output, receipt)
                raise
            except Exception as exc:
                row.update(state="uncertain", exception_type=type(exc).__name__)
                persist(output, receipt)
                raise ProviderFault("uncertain", "Reconcile saved request ID before any retry.") from exc
            row.update(state="returned", response=response.model_dump(mode="json"))
            persist(output, receipt)
        elif row["request_id"] != request.request_id or row["state"] not in {"returned", "validated"}:
            raise ProviderFault("uncertain", "Reconcile saved request ID before any retry.")
        response = ProviderResponse.model_validate(row["response"])
        if response.request_id != request.request_id:
            raise ValueError("existing_critic_response_id_changed")
        critique = validate(response, bundle, "critique")
        row.update(state="validated", disposition=critique.disposition,
                   direct_quant_scope=critique.direct_quant_scope,
                   substantive_research=critique.substantive_research)
        receipt.update(state="passed" if critique.disposition == "pass" else "not_passed",
                       critique=critique.model_dump(mode="json"),
                       card_length=len(receipt["card"]))
    except ProviderFault as exc:
        receipt.update(state="blocked", fault=exc.code)
    except ValueError as exc:
        receipt.update(state="not_passed", error=str(exc))
    finally:
        persist(output, receipt)
    return receipt


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    receipt = await qualify(args.document, args.output)
    print(json.dumps({key: receipt.get(key) for key in
                      ("state", "fault", "error", "card_length", "publication_enabled", "slack_writes")}))
    if receipt["state"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
