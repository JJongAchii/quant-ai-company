"""Render an existing source-validated Quant preview without publishing it."""

from quant_company.company import Company
from quant_company.config import Settings
from quant_company.quant_feed.contracts import ResearchBrief
from quant_company.quant_feed.editor import render
from quant_company.quant_feed.store import QuantFeedStore

DOCUMENT = "a7dc910cf962c50bddc6763d8ad0d04aa209dda009c9caaaf85dbefb3ac65600"
company = Company(Settings())
store = QuantFeedStore(company)
with company.db.transaction() as conn:
    document = conn.execute("SELECT * FROM quant_feed_documents WHERE id=%s", (DOCUMENT,)).fetchone()
    if not document or not document["brief"]:
        raise ValueError("saved_brief_missing")
    bundle = store.bundle(conn, document)
print(render(ResearchBrief.model_validate(document["brief"]), bundle["metadata"]))
