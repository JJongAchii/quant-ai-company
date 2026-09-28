"""Critique one already-returned real Quant revision, without DB or Slack writes."""

import argparse
import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from quant_company.company import Company, fingerprint
from quant_company.config import Settings
from quant_company.contracts import ProviderFault, ProviderRequest, ProviderResponse
from quant_company.execution import provider_for
from quant_company.quant_feed.contracts import QUANT_FEED_AGENT
from quant_company.quant_feed.editor import prompt, render, validate
from quant_company.quant_feed.store import QuantFeedStore

DOCUMENT = "a7dc910cf962c50bddc6763d8ad0d04aa209dda009c9caaaf85dbefb3ac65600"
SOURCE_SHA256 = "26959229a2a8dd400da6fa9e853f58dfbf4a42b6ff980acd1e2d9259c5e852ef"
SOURCE_POLICY = "ae087d1cf858843865e95e9d8fe5d6bf13a77b776eb030da5d3533da7d6d6ee5"
EXPECTED_CORRECTIONS = [{"kind": "initial_case_quote_match", "evidence_index": 1,
                         "location": "PDF p.2"}]


def persist(path, receipt):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    with temporary.open("x") as handle:
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


def prepare(source_path):
    raw = source_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("saved_revision_receipt_changed")
    source = json.loads(raw)
    if (source["state"] != "not_passed" or source["policy"] != SOURCE_POLICY
            or source["publication_enabled"] or source["slack_writes"] or source["document_writes"]):
        raise ValueError("saved_revision_source_not_paused")
    calls = [item for item in source["calls"] if item["case"] == "positive-contract-revision"]
    if (len(calls) != 1 or calls[0]["document_id"] != DOCUMENT
            or calls[0]["state"] != "returned"):
        raise ValueError("saved_revision_call_not_unique")
    bundle = dict(source["inputs"]["positive"])
    if calls[0]["original_sha256"] != bundle["original_sha256"]:
        raise ValueError("saved_revision_original_changed")
    response = ProviderResponse.model_validate(calls[0]["response"])
    if response.request_id != calls[0]["request_id"]:
        raise ValueError("saved_revision_response_id_changed")
    corrections = []
    brief = validate(response, bundle, "review", audit=corrections)
    if brief.disposition != "publish" or corrections != EXPECTED_CORRECTIONS:
        raise ValueError("saved_revision_source_contract_changed")
    bundle["draft"] = brief.model_dump(mode="json")
    bundle["previous_critique"] = None
    return bundle, render(brief, bundle["metadata"]), corrections


async def qualify(source_path, output):
    company = Company(Settings())
    if company.settings.model_provider != "codex" or company.settings.quant_feed_publish_enabled:
        raise ValueError("subscription_preview_requires_paused_quant")
    store = QuantFeedStore(company)
    if not store.authorized():
        raise ValueError("quant_not_authorized")
    policy = store.policy()
    if output.exists():
        receipt = json.loads(output.read_text())
        if (receipt["policy"] != policy or receipt["document_id"] != DOCUMENT
                or receipt["source_receipt_sha256"] != SOURCE_SHA256):
            raise ValueError("saved_revision_critic_receipt_inputs_changed")
        if receipt["state"] != "running":
            return receipt
    else:
        bundle, card, corrections = prepare(source_path)
        receipt = {"state": "running", "checked_at": datetime.now(UTC).isoformat(),
                   "policy": policy, "document_id": DOCUMENT,
                   "source_receipt_sha256": SOURCE_SHA256,
                   "original_sha256": bundle["original_sha256"],
                   "brief_sha256": fingerprint(bundle["draft"]),
                   "mode": "subscription-saved-real-revision-new-critic",
                   "publication_enabled": False, "document_writes": False, "slack_writes": False,
                   "source_corrections": corrections, "bundle": bundle, "card": card, "call": None}
        persist(output, receipt)
    bundle = receipt["bundle"]
    role = company.roles[QUANT_FEED_AGENT]
    request = ProviderRequest(
        request_id="quant-feed-regime-revision-critic-"
        + fingerprint([SOURCE_SHA256, policy, prompt(bundle, "critique")])[:40],
        model=role.model, reasoning_effort=role.reasoning_effort,
        prompt=prompt(bundle, "critique"),
    )
    row = receipt["call"]
    try:
        if row is None:
            row = {"request_id": request.request_id, "prompt_sha256": fingerprint(request.prompt),
                   "state": "requested"}
            receipt["call"] = row
            persist(output, receipt)  # Durable ID before an ambiguous external call.
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
            raise ValueError("saved_revision_critic_response_id_changed")
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
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    receipt = await qualify(args.source, args.output)
    print(json.dumps({key: receipt.get(key) for key in
                      ("state", "fault", "error", "card_length", "publication_enabled", "slack_writes")}))
    if receipt["state"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
