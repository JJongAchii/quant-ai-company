"""Planning corpus and evidence sets on real PostgreSQL; synthetic originals, no feed or model calls."""

import json
from datetime import timedelta
from types import SimpleNamespace

from psycopg.types.json import Jsonb

from quant_company.company import fingerprint, now
from quant_company.research.library import available_sources, project_corpus

from .test_research_missions import Harness


def literature(company, project_id, key, *, title, idea="", market="", topic="research_validity",
               vintage="recent", kind="empirical", change="new", authors=("Example Author",), at=None,
               work=None, text=None):
    """Register a reviewed original in the same shape sync_library stores; no feed calls."""
    created = at or now()
    doc, work_id, url = "doc-" + key, work or "work-" + key, "https://example.org/" + key
    pages = [{"location": "PDF p.1", "text": text or f"Original text for {key}."}]
    brief = {"disposition": "publish", "reason": "fixture", "title": title, "authors": list(authors),
             "published_on": "2026-01-01", "kind": kind, "maturity": "working_paper", "market": market,
             "topic": topic, "vintage": vintage, "idea": idea, "data_period": "", "validation": "",
             "author_results": "", "costs_turnover": "", "limitations": [], "application": "", "change": change}
    digest = fingerprint(pages)
    content = json.dumps({"document_id": doc, "metadata": {"url": url}, "receipt": {}, "pages": pages,
                          "brief": brief, "critique": {"disposition": "pass"}, "content_digest": digest},
                         ensure_ascii=False)
    source_id = "literature:" + fingerprint([str(project_id), doc])
    with company.db.transaction() as conn:
        conn.execute("""INSERT INTO quant_feed_sources(id,config,config_digest,enabled)
            VALUES('fixture','{}','fixture',false) ON CONFLICT DO NOTHING""")
        conn.execute("INSERT INTO quant_feed_works(id) VALUES(%s) ON CONFLICT DO NOTHING", (work_id,))
        conn.execute("""INSERT INTO quant_feed_candidates(id,source_id,url,title,source_digest)
            VALUES(%s,'fixture',%s,%s,'fixture')""", (doc, url, title))
        conn.execute("""INSERT INTO quant_feed_documents(id,work_id,candidate_id,source_digest,content_digest,
            metadata,receipt,pages,brief,critique,created_at) VALUES(%s,%s,%s,'fixture',%s,%s,'{}',%s,%s,%s,%s)""",
            (doc, work_id, doc, digest, Jsonb({"url": url}), Jsonb(pages), Jsonb(brief),
             Jsonb({"disposition": "pass"}), created))
        conn.execute("""INSERT INTO sources(id,title,uri,content,available_at,project_id,approved,synthetic,metadata)
            VALUES(%s,%s,%s,%s,%s,%s,true,true,%s)""", (source_id, title, url, content, created, project_id,
            Jsonb({"kind": "literature", "document_id": doc, "work_id": work_id})))
        conn.execute("""INSERT INTO research_literature(source_id,project_id,document_id,work_id,content_digest,
            state,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)""", (source_id, project_id, doc, work_id, digest,
            "retracted" if change == "retraction" else "current", created))
    return source_id


def test_corpus_keeps_owner_and_project_boundaries(company):
    mine, other = Harness(company), company.ingest(event_key="fixture:other", text="Other", owner="UOTHER",
                                                     channel="COTHER", thread_ts="9.0")
    kept = literature(company, mine.project["project_id"], "mine", title="Momentum after costs")
    hidden = literature(company, other["project_id"], "other", title="Momentum after costs elsewhere")
    with company.db.transaction() as conn:
        rows = project_corpus(company, conn, company._project(conn, mine.project["project_id"]), [])
    assert {row["id"] for row in rows} == {kept}
    assert hidden not in {row["id"] for row in rows}


def test_corpus_has_no_recency_cap(company):
    h = Harness(company)
    old = literature(company, h.project["project_id"], "old", title="Momentum after costs",
                     at=now() - timedelta(days=40))
    for n in range(20):
        literature(company, h.project["project_id"], f"new{n}", title=f"Unrelated topic {n}",
                   at=now() - timedelta(days=1, minutes=n))
    with company.db.transaction() as conn:
        project = company._project(conn, h.project["project_id"])
        legacy = {row["id"] for row in available_sources(company, conn, project,
                  SimpleNamespace(source_ids=[], include_quant_feed=True))}
        corpus = {row["id"] for row in project_corpus(company, conn, project, [])}
    assert old not in legacy and old in corpus and len(corpus) == 21
