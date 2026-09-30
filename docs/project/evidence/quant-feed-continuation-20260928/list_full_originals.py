"""Read-only candidate metadata for an independent positive Quant preview."""

import json

from quant_company.company import Company
from quant_company.config import Settings

company = Company(Settings())
with company.db.transaction() as conn:
    rows = conn.execute("""
        SELECT d.id, s.id AS source_id, d.state, d.stage,
               left(d.metadata->>'title', 150) AS title,
               left(d.metadata->>'url', 220) AS url,
               d.metadata->>'published_on' AS published_on,
               octet_length(d.pages::text) AS original_bytes,
               jsonb_array_length(d.pages) AS page_count,
               d.receipt->>'content_type' AS content_type,
               d.receipt->>'fulltext_status' AS fulltext_status,
               d.brief IS NOT NULL AS has_brief
        FROM quant_feed_documents d
        JOIN quant_feed_candidates c ON c.id=d.candidate_id
        JOIN quant_feed_sources s ON s.id=c.source_id
        WHERE d.state = 'ready'
          AND s.id IN ('arxiv-qfin', 'nber-asset-pricing', 'two-sigma', 'man', 'research-affiliates')
          AND octet_length(d.pages::text) >= 15000
          AND jsonb_array_length(d.pages) >= 2
        ORDER BY CASE s.id
                   WHEN 'arxiv-qfin' THEN 0
                   WHEN 'nber-asset-pricing' THEN 1
                   WHEN 'two-sigma' THEN 2
                   WHEN 'man' THEN 3
                   ELSE 4 END,
                 octet_length(d.pages::text) DESC
        LIMIT 50
    """).fetchall()
print(json.dumps(rows, ensure_ascii=False, default=str))
