"""Revalidate settled source bindings and render a passed receipt, without model calls."""

import argparse
import copy
import hashlib
import json
from pathlib import Path

from quant_company.contracts import ProviderResponse
from quant_company.quant_feed.editor import render, validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    raw = args.receipt.read_bytes()
    receipt = json.loads(raw)
    if receipt["state"] != "passed" or args.output.exists():
        raise ValueError("requires_passed_receipt_and_new_output")
    bundle = copy.deepcopy(receipt["inputs"]["positive"])
    drafts = [row for row in receipt["calls"] if row["case"].startswith("positive-")
              and row["stage"] != "critique"]
    critics = [row for row in receipt["calls"] if row["case"].startswith("positive-")
               and row["stage"] == "critique"]
    draft, critic = drafts[-1], critics[-1]
    response = ProviderResponse.model_validate(draft["response"])
    if response.request_id != draft["request_id"]:
        raise ValueError("receipt_identity_mismatch")
    brief = validate(response, bundle, draft["stage"])
    bundle["draft"] = brief.model_dump(mode="json")
    critic_response = ProviderResponse.model_validate(critic["response"])
    if critic_response.request_id != critic["request_id"]:
        raise ValueError("receipt_identity_mismatch")
    verdict = validate(critic_response, bundle, "critique")
    if brief.disposition != "publish" or verdict.disposition != "pass":
        raise ValueError("receipt_does_not_pass")
    card = render(brief, bundle["metadata"])
    if len(card) > 2400:
        raise ValueError("card_too_long")
    result = {"state": "passed", "mode": "stored_response_revalidation_and_render",
              "new_model_calls": 0, "slack_writes": 0, "source_receipt_sha256": hashlib.sha256(raw).hexdigest(),
              "draft_request_id": draft["request_id"], "critic_request_id": critic["request_id"],
              "card_characters": len(card), "card": card}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "card"}))


if __name__ == "__main__":
    main()
