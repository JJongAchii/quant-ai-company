"""Read-only revalidation of existing preview briefs against their frozen originals."""

import json

from quant_company.company import Company
from quant_company.config import Settings
from quant_company.contracts import AgentDecision, ArtifactDraft, ProviderResponse
from quant_company.quant_feed.editor import render, validate
from quant_company.quant_feed.store import QuantFeedStore

IDS = (
    "a7dc910cf962c50bddc6763d8ad0d04aa209dda009c9caaaf85dbefb3ac65600",
    "fb9d13d7371e87b5523bfe25fb0fcd021068fc092aeb3dd150166ca3a99616c3",
)
company = Company(Settings())
store = QuantFeedStore(company)
results = []
with company.db.transaction() as conn:
    for identity in IDS:
        document = conn.execute("SELECT * FROM quant_feed_documents WHERE id=%s", (identity,)).fetchone()
        if not document or not document["brief"]:
            results.append({"document_id": identity, "error": "existing_brief_missing"})
            continue
        bundle = store.bundle(conn, document)
        response = ProviderResponse(
            request_id="offline-existing-preview", provider="fixture",
            decision=AgentDecision(
                status="complete", say="", artifacts=[ArtifactDraft(
                    title="existing brief", content=json.dumps(document["brief"], ensure_ascii=False)
                )]
            ),
        )
        try:
            value = validate(response, bundle, "review")
            result = {"valid": True, "card_length": len(render(value, bundle["metadata"]))}
        except ValueError as exc:
            result = {"valid": False, "error": str(exc)}
        results.append({"document_id": identity, "state": document["state"],
                        "critique_disposition": (document["critique"] or {}).get("disposition"),
                        "evidence_count": len(document["brief"].get("evidence", [])), **result})
print(json.dumps(results, ensure_ascii=False))
