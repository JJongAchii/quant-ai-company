"""Export only qualification metadata and hashes; no originals or model prose."""

import hashlib
import json
from pathlib import Path

root = Path("/var/lib/quant-company/operations/quant-feed-structural-20260929/preview")
labels = ("crypto-volatility-v11", "crypto-volatility-v12", "crypto-volatility-v13", "crypto-volatility-v14",
          "agentic-lob-v14", "agentic-lob-v15", "agentic-lob-v16")
result = []
for label in labels:
    path = root / (label + ".json")
    if not path.is_file():
        continue
    raw = path.read_bytes()
    receipt = json.loads(raw)
    operation_path = root / (label + "-operation.json")
    result.append({
        "label": label,
        "receipt_sha256": hashlib.sha256(raw).hexdigest(),
        "state": receipt["state"],
        "checked_at": receipt["checked_at"],
        "policy_digest": receipt["policy"],
        "error": receipt.get("error"),
        "fault": receipt.get("fault"),
        "case_results": receipt.get("case_results"),
        "inputs": {name: {key: bundle.get(key) for key in ("document_id", "original_sha256")}
                   for name, bundle in receipt["inputs"].items()},
        "new_calls": [{key: call.get(key) for key in
                       ("case", "stage", "request_id", "prompt_sha256", "output_contract", "state", "disposition")}
                      for call in receipt["calls"]],
        "revalidated_response_count": len(receipt.get("revalidated_responses", [])),
        "card_characters": len(receipt["card"]) if receipt.get("card") else None,
        "operation": json.loads(operation_path.read_text()) if operation_path.is_file() else None,
    })
print(json.dumps(result, indent=2))
