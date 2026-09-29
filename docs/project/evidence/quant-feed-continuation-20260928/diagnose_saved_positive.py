"""Revalidate only the returned positive draft from a private receipt, offline."""

import json
from pathlib import Path

from quant_company.contracts import ProviderResponse
from quant_company.quant_feed.editor import validate

receipt = json.loads(Path("/qualification/receipt.json").read_text())
if receipt["state"] != "not_passed" or receipt["publication_enabled"]:
    raise ValueError("unexpected_qualification_receipt")
rows = [row for row in receipt["calls"] if row["case"] == "positive-review"]
if len(rows) != 1 or rows[0]["state"] != "returned":
    raise ValueError("unexpected_positive_review_receipt")
response = ProviderResponse.model_validate(rows[0]["response"])
try:
    validate(response, receipt["inputs"]["positive"], "review")
except ValueError as exc:
    print(json.dumps({"initial_positive_validation_error": str(exc),
                      "context_clipped": receipt["inputs"]["positive"]["context_clipped"]}))
else:
    raise ValueError("expected_original_validation_failure")
