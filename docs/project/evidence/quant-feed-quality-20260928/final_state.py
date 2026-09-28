"""Read-only, secret-free Quant publication and activity check."""

import json
from datetime import UTC, datetime

from quant_company.company import Company
from quant_company.config import Settings

company = Company(Settings())
with company.db.transaction() as conn:
    publications = conn.execute("""
        SELECT o.status, count(*) AS n FROM quant_feed_publications p
        JOIN outbox o ON o.id=p.id GROUP BY o.status ORDER BY o.status
    """).fetchall()
    calls = conn.execute("""
        SELECT state, count(*) AS n FROM quant_feed_calls
        WHERE state='running' GROUP BY state
    """).fetchall()
    documents = conn.execute("""
        SELECT state, count(*) AS n FROM quant_feed_documents GROUP BY state ORDER BY state
    """).fetchall()
print(json.dumps({"checked_at": datetime.now(UTC).isoformat(),
                  "quant_collection_enabled": company.settings.quant_feed_enabled,
                  "quant_publication_enabled": company.settings.quant_feed_publish_enabled,
                  "publications": publications, "running_calls": calls,
                  "documents": documents}, default=str, ensure_ascii=False))
