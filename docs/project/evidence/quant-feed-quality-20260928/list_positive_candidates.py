"""Read bounded candidate metadata to select a second real positive case; no writes."""

import json

from quant_company.company import Company
from quant_company.config import Settings

company = Company(Settings())
with company.db.transaction() as conn:
    counts = conn.execute("""
        SELECT s.id AS source_id, d.state, count(*) AS n,
               min(octet_length(d.pages::text)) AS min_bytes
        FROM quant_feed_documents d
        JOIN quant_feed_candidates c ON c.id=d.candidate_id
        JOIN quant_feed_sources s ON s.id=c.source_id
        GROUP BY s.id,d.state ORDER BY s.id,d.state
    """).fetchall()
    rows = conn.execute("""
        SELECT d.id, s.id AS source_id, left(d.metadata->>'title', 140) AS title,
               left(d.metadata->>'url', 220) AS url, d.state, d.stage,
               octet_length(d.pages::text) AS original_bytes,
               jsonb_array_length(d.pages) AS page_count,
               d.receipt->>'content_type' AS content_type,
               d.receipt->>'fulltext_status' AS fulltext_status,
               d.brief IS NOT NULL AS has_brief,
               d.critique->>'disposition' AS critique_disposition
        FROM quant_feed_documents d
        JOIN quant_feed_candidates c ON c.id=d.candidate_id
        JOIN quant_feed_sources s ON s.id=c.source_id
        WHERE d.state IN ('ready','preview') AND s.id IN (
          'arxiv-qfin','two-sigma','man','research-affiliates','nber-asset-pricing')
        ORDER BY (d.state='preview') DESC, original_bytes, d.created_at DESC
        LIMIT 80
    """).fetchall()
print(json.dumps({"counts": counts, "rows": rows}, default=str, ensure_ascii=False))
