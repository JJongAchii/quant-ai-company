"""Read-only shortlist of ready PDFs whose entire extracted context fits the model input."""

import json

from quant_company.company import Company
from quant_company.config import Settings
from quant_company.quant_feed.store import QuantFeedStore

company = Company(Settings())
store = QuantFeedStore(company)
results = []
with company.db.transaction() as conn:
    rows = conn.execute("""
        SELECT d.*, s.id AS source_id
        FROM quant_feed_documents d
        JOIN quant_feed_candidates c ON c.id=d.candidate_id
        JOIN quant_feed_sources s ON s.id=c.source_id
        WHERE d.state='ready' AND d.receipt->>'content_type'='application/pdf'
          AND jsonb_array_length(d.pages)>=2
        ORDER BY octet_length(d.pages::text),d.created_at DESC
        LIMIT 120
    """).fetchall()
    for row in rows:
        bundle = store.bundle(conn, row)
        if bundle["context_clipped"] or bundle["truncated"]:
            continue
        results.append({"document_id": row["id"], "source_id": row["source_id"],
                        "title": row["metadata"].get("title"), "url": row["metadata"].get("url"),
                        "original_sha256": bundle["original_sha256"],
                        "page_count": len(bundle["pages"]),
                        "excerpt_chars": sum(len(page["text"]) for page in bundle["pages"])})
print(json.dumps(results[:40], ensure_ascii=False))
