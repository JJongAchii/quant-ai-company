"""Check historical brief accuracy as a private control, not a republication.

Uses the established qualification loader, which deliberately removes prior
publication and historical objections for source-accuracy controls. Production
dedupe is not changed, and no document or Slack publication is created.
"""

import asyncio
import importlib.util
import json
from pathlib import Path

from quant_company.company import Company
from quant_company.config import Settings
from quant_company.execution import provider_for
from quant_company.providers.codex_runner import atomic_json
from quant_company.quant_feed.contracts import QUANT_FEED_AGENT
from quant_company.quant_feed.editor import validate
from quant_company.quant_feed.store import QuantFeedStore

OUTPUT = Path("/qualification/receipts/positive-accuracy.json")


async def main():
    if OUTPUT.exists():
        raise ValueError("existing_control_requires_reconciliation")
    spec = importlib.util.spec_from_file_location(
        "established_quant_qualification", "/qualification/source/scripts/qualify_quant_editorial.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    company = Company(Settings())
    store = QuantFeedStore(company)
    bundles = helper.load_inputs(company, store,
        "eab01733dab66494bd5391d6b47154a3c80633108993eb4c0bf85210e59a6003",
        "96e1499827bf453e9f37785ae5daadcd08992622cab63e0768134f57eaa5cdc3")
    bundle = bundles["positive"]
    assert bundle["prior"] is None
    request = helper.qualification_request(company.roles[QUANT_FEED_AGENT], "recovery-positive-accuracy",
                                            bundle, "critique", store.policy())
    receipt = {"state": "requested", "policy": store.policy(), "request": request.model_dump(mode="json"),
               "control": "historical source accuracy, NOT a new publication", "document_writes": False,
               "slack_writes": False, "account_ledger_writes": company.settings.model_accounts_enabled}
    atomic_json(OUTPUT, receipt)
    result = await provider_for(company).run(request)
    receipt.update(state="returned", response=result.model_dump(mode="json"))
    atomic_json(OUTPUT, receipt)
    value = validate(result, bundle, "critique")
    receipt.update(state="passed" if value.disposition == "pass" else "not_passed", critic=value.model_dump(mode="json"))
    atomic_json(OUTPUT, receipt)
    print(json.dumps({key: receipt[key] for key in ("state", "policy", "control", "document_writes",
                                                    "slack_writes", "account_ledger_writes", "critic")}))
    if value.disposition != "pass":
        raise SystemExit(2)


if __name__ == "__main__":
    asyncio.run(main())
