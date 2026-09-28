"""Read bounded production Quant metadata; no originals, credentials or messages."""

import json

from quant_company.company import Company
from quant_company.config import Settings

company = Company(Settings())
with company.db.transaction() as conn:
    rows = conn.execute("""
        SELECT d.id, s.id AS source_id, d.metadata->>'title' AS title,
               d.metadata->>'url' AS url, d.state, d.stage,
               octet_length(d.pages::text) AS original_bytes,
               d.receipt->>'fulltext_status' AS fulltext_status,
               d.brief IS NOT NULL AS has_brief,
               d.critique IS NOT NULL AS has_critique
        FROM quant_feed_documents d
        JOIN quant_feed_candidates c ON c.id=d.candidate_id
        JOIN quant_feed_sources s ON s.id=c.source_id
        WHERE d.metadata->>'url' ILIKE '%agentic-ai-and-governance%'
           OR s.id IN ('nber', 'aqr', 'kcmi', 'research-affiliates', 'ssrn', 'arxiv')
        ORDER BY (d.metadata->>'url' ILIKE '%agentic-ai-and-governance%') DESC,
                 (d.state='preview') DESC, original_bytes DESC
        LIMIT 30
    """).fetchall()
    statuses = conn.execute("""
        SELECT state, count(*) AS n FROM quant_feed_documents GROUP BY state ORDER BY state
    """).fetchall()
    publications = conn.execute("""
        SELECT o.status, count(*) AS n FROM quant_feed_publications p
        JOIN outbox o ON o.id=p.id GROUP BY o.status ORDER BY o.status
    """).fetchall()
print(json.dumps({
    'publication_enabled': company.settings.quant_feed_publish_enabled,
    'document_states': statuses, 'publication_states': publications,
    'possible_cases': rows,
}, default=str, ensure_ascii=False))
