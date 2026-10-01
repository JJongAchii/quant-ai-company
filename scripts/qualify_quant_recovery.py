"""Private subscription qualification; no document mutations or Slack delivery.

Run only through a scoped operator with a staged producer/consumer pair. Raw
originals and model responses remain in the host's private receipt directory.
"""

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from quant_company.company import Company, fingerprint
from quant_company.config import Settings
from quant_company.contracts import ProviderFault, ProviderRequest
from quant_company.execution import provider_for
from quant_company.providers.codex_runner import atomic_json
from quant_company.quant_feed.contracts import QUANT_FEED_AGENT
from quant_company.quant_feed.editor import discovery_prompt, prompt, source_spans, validate
from quant_company.quant_feed.store import QuantFeedStore
from quant_company.web_tools import search_result

STALLED = "c3f27c35c3ff8f16ef7bbfbc60593649bd2b3ca4cd4d5367d83487123f7b9b60"
NEGATIVE = "eab01733dab66494bd5391d6b47154a3c80633108993eb4c0bf85210e59a6003"
POSITIVE = "96e1499827bf453e9f37785ae5daadcd08992622cab63e0768134f57eaa5cdc3"


async def qualify(output):
    company = Company(Settings())
    store = QuantFeedStore(company)
    if company.settings.model_provider != "codex" or not store.authorized():
        raise ValueError("authorized_subscription_quant_required")
    if output.exists():
        raise ValueError("existing_qualification_requires_reconciliation")
    role = company.roles[QUANT_FEED_AGENT]
    with company.db.transaction() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        bundles = {}
        for name, identity in (("stalled", STALLED), ("negative", NEGATIVE), ("positive", POSITIVE)):
            document = conn.execute("SELECT * FROM quant_feed_documents WHERE id=%s", (identity,)).fetchone()
            if not document:
                raise ValueError("frozen_qualification_original_missing")
            bundles[name] = store.bundle(conn, document)
        discovery = store.search_bundle(conn, "discover", "recovery-qualification")
    data = json.loads(prompt(bundles["stalled"], "repair").split("\nDATA:\n", 1)[1])
    if data["source_spans"] != source_spans(bundles["stalled"]["pages"]):
        raise ValueError("source_spans_changed")
    receipt = {"state": "running", "checked_at": datetime.now(UTC).isoformat(), "policy": store.policy(),
               "slack_writes": False, "document_writes": False,
               "account_ledger_writes": company.settings.model_accounts_enabled, "calls": [], "cases": {},
               "stalled_input": {"document_id": STALLED, "original_sha256": bundles["stalled"]["original_sha256"],
                                 "repair_prompt_characters": len(prompt(bundles["stalled"], "repair")),
                                 "source_span_count": len(data["source_spans"]), "source_spans_preserved": True}}
    atomic_json(output, receipt)
    provider = provider_for(company)

    async def call(name, text, contract, *, web_search=False):
        request = ProviderRequest(request_id="quant-feed-recovery-" + fingerprint([store.policy(), name, text])[:40],
                                  model=role.model, reasoning_effort=role.reasoning_effort, prompt=text,
                                  web_search=web_search, output_contract=contract)
        entry = {"case": name, "state": "requested", "request": request.model_dump(mode="json")}
        receipt["calls"].append(entry)
        atomic_json(output, receipt)  # Persist stable ID before calling; never reissue a failed attempt.
        response = await provider.run(request)
        if response.request_id != request.request_id:
            raise ValueError("qualification_response_mismatch")
        entry.update(state="returned", response=response.model_dump(mode="json"))
        atomic_json(output, receipt)
        return response

    try:
        searched = await call("native-discovery", discovery_prompt(discovery), "quant_search_v1", web_search=True)
        found = search_result(searched, discovery)
        if not found["ok"]:
            raise ValueError(found["error"])
        receipt["cases"]["discovery"] = {"state": "passed", "candidate_count": len(found["results"]),
                                          "native_search_events": len(searched.web_searches), "verified": False}
        negative = await call("negative-critic", prompt(bundles["negative"], "critique"), "quant_critique_v2")
        rejected = validate(negative, bundles["negative"], "critique")
        if rejected.disposition == "pass" or rejected.direct_quant_scope and rejected.substantive_research:
            raise ValueError("negative_critic_missed_scope")
        receipt["cases"]["negative"] = {"state": "passed", "disposition": rejected.disposition}
        positive = await call("positive-critic", prompt(bundles["positive"], "critique"), "quant_critique_v2")
        accepted = validate(positive, bundles["positive"], "critique")
        if accepted.disposition != "pass":
            raise ValueError("positive_critic_did_not_pass")
        receipt["cases"]["positive"] = {"state": "passed", "disposition": accepted.disposition}
        receipt["state"] = "passed"
    except ProviderFault as error:
        receipt.update(state="blocked", fault=error.code)
    except ValueError as error:
        receipt.update(state="not_passed", error=str(error))
    finally:
        atomic_json(output, receipt)
    summary = {key: receipt.get(key) for key in ("state", "policy", "slack_writes", "document_writes", "account_ledger_writes", "cases",
                                                "stalled_input", "fault", "error")}
    summary.update(call_count=len(receipt["calls"]), receipt_sha256=hashlib.sha256(output.read_bytes()).hexdigest())
    print(json.dumps(summary))
    if receipt["state"] != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(qualify(parser.parse_args().output))
