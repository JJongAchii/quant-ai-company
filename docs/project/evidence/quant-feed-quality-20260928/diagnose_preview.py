"""Inspect only bounded citation mismatch metadata from a saved preview receipt."""

import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

RECEIPT = Path(sys.argv[1] if len(sys.argv) > 1 else
               "/var/lib/quant-company/operations/quant-feed-quality-20260928/preview/qualification.json")


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


receipt = json.loads(RECEIPT.read_text())
pages = receipt["inputs"]["positive"]["pages"]
locations = {page["location"]: compact(page["text"]) for page in pages}
results = []
for call in receipt["calls"]:
    if not call["case"].startswith("positive") or "response" not in call:
        continue
    decision = call["response"]["decision"]
    artifact = decision["artifacts"][0]
    brief = json.loads(artifact["content"], strict=False)
    missing = []
    for index, evidence in enumerate(brief.get("evidence", []), 1):
        quote = compact(evidence["quote"])
        location = evidence["location"]
        if quote not in locations.get(location, ""):
            missing.append(
                {
                    "index": index,
                    "location": location,
                    "location_exists": location in locations,
                    "matching_locations": [
                        other for other, text in locations.items() if quote in text
                    ],
                    "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
                    "quote_length": len(quote),
                    "quote_head": evidence["quote"][:90],
                }
            )
    results.append(
        {"case": call["case"], "disposition": brief.get("disposition"),
         "evidence_count": len(brief.get("evidence", [])), "missing": missing}
    )
print(json.dumps({"state": receipt["state"], "error": receipt.get("error"),
                  "positive_calls": results}, ensure_ascii=False))
