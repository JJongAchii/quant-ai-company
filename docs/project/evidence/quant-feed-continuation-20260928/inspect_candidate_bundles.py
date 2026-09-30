"""Read bounded source-bundle metadata for possible positive cases."""

import json

from quant_company.company import Company
from quant_company.config import Settings
from quant_company.quant_feed.store import QuantFeedStore

IDS = (
    "868a61ddbfea20909395ae7ee07bb20079fbbffb5ecf9d436c4e15e27de3a73d",
    "5209c2517c42ae1b492e758c5f5f7dfeca5645d837aa54341a46b117f96f8d65",
    "ab808c1014c7fbaf8fddea66170847bc3591ca01434ac5e276bc9267166593f7",
    "1dabb1c146c0ddd1afa86349399df38f1d50d1bd35e84810099042247736adef",
    "32879918c5c174b3777913287bcd3a2304bf33c6681c7fb8c2cbf1938d1612e9",
)

company = Company(Settings())
store = QuantFeedStore(company)
results = []
with company.db.transaction() as conn:
    for identity in IDS:
        document = conn.execute("SELECT * FROM quant_feed_documents WHERE id=%s", (identity,)).fetchone()
        if not document or document["state"] != "ready":
            results.append({"document_id": identity, "error": "not_ready"})
            continue
        bundle = store.bundle(conn, document)
        pages = bundle["pages"]
        results.append({
            "document_id": identity,
            "metadata": bundle["metadata"],
            "original_sha256": bundle["original_sha256"],
            "truncated": bundle["truncated"],
            "context_clipped": bundle["context_clipped"],
            "page_count": len(pages),
            "excerpt_chars": sum(len(page["text"]) for page in pages),
            "sample": [
                {"location": page["location"], "text_start": page["text"][:320]}
                for page in [*pages[:2], *pages[-1:]]
            ],
        })
print(json.dumps(results, ensure_ascii=False, default=str))
