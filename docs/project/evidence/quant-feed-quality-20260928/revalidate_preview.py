"""Revalidate saved positive proposals in the released image without network or credentials."""

import json
import sys
from pathlib import Path

from quant_company.contracts import ProviderResponse
from quant_company.quant_feed.contracts import ResearchBrief
from quant_company.quant_feed.editor import _proposal, render, validate

receipt = (json.load(sys.stdin) if "--stdin" in sys.argv else
           json.loads(Path("/qualification/qualification.json").read_text()))
bundle = receipt["inputs"]["positive"]
out = []
for call in receipt["calls"]:
    if not call["case"].startswith("positive") or "response" not in call:
        continue
    response = ProviderResponse.model_validate(call["response"])
    proposal = _proposal(ResearchBrief, response.decision.artifacts[0].content)
    corrections = []
    try:
        validated = validate(response, bundle, call["stage"], audit=corrections)
        error = None
    except ValueError as exc:
        error = str(exc)
        validated = proposal
    out.append({"case": call["case"], "disposition": proposal.disposition,
                "card_length": len(render(validated, bundle["metadata"])),
                "source_corrections": corrections, "error": error})
print(json.dumps(out))
