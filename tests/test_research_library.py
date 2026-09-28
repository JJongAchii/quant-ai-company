"""Real PG original/critique receipts; no live HTTP or Slack."""

import pytest

from quant_company.company import PolicyError
from quant_company.research.library import require_current, sync_library

from .test_quant_feed import TEXT, original, publish, quant  # noqa: F401


def test_library_requires_independent_review_and_keeps_originals_after_retraction(quant):  # noqa: F811
    company = quant.company
    result = company.ingest(event_key="library-project", text="Research program", owner="UHUMAN",
                             channel="CQUANT", thread_ts="100.0")
    first = original(quant, metadata={"doi": "10.1234/fixture"})
    with company.db.transaction() as conn:
        project = company._project(conn, result["project_id"])
        sync_library(company, conn, project)
        assert not conn.execute("SELECT 1 FROM research_literature").fetchone()
    publish(quant)
    with company.db.transaction() as conn:
        project = company._project(conn, result["project_id"])
        sync_library(company, conn, project)
        source = conn.execute("SELECT * FROM research_literature WHERE document_id=%s", (first["document_id"],)).fetchone()
        assert source["state"] == "current"
        require_current(conn, [source["source_id"]])
        conn.execute("UPDATE outbox SET status='delivered'")
    # A reviewed correction is a new immutable version, never a rewrite of source bytes.
    later = original(quant, suffix="?corrected=1", text=TEXT + " The study is retracted.", metadata={"doi": "10.1234/fixture"})
    publish(quant, change="retraction", change_summary="The study is retracted.")
    with company.db.transaction() as conn:
        project = company._project(conn, result["project_id"])
        sync_library(company, conn, project)
        assert conn.execute("SELECT state FROM research_literature WHERE document_id=%s", (later["document_id"],)).fetchone()["state"] == "retracted"
        assert conn.execute("SELECT content FROM sources WHERE id=%s", (source["source_id"],)).fetchone()
    with pytest.raises(PolicyError, match="corrected or retracted"):
        with company.db.transaction() as conn:
            require_current(conn, [source["source_id"]])
