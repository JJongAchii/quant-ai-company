"""Planning corpus and evidence sets on real PostgreSQL; synthetic originals, no feed or model calls."""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from psycopg.types.json import Jsonb

from quant_company.company import PolicyError, fingerprint, now
from quant_company.research.library import available_sources, project_corpus
from quant_company.research.planning_retrieval import POLICY, compute, freeze, tokens
from quant_company.research.planning_store import QuestionStore

from .test_research_missions import Harness
from .test_research_planning_questions import approve, spec
from .test_research_programs import program  # noqa: F401


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


@pytest.fixture
def planning(company):
    company.settings.research_planning_enabled = True
    return Harness(company)


def question(h, **changes):
    with h.company.db.transaction() as conn:
        return QuestionStore(h.company).draft(conn, h.company._project(conn, h.project["project_id"]),
                                              spec(**changes), actor="director")


def evidence(h, question_id):
    with h.company.db.transaction() as conn:
        return compute(h.company, conn, question_id)


def test_korean_bigrams_and_english_stopwords():
    assert tokens("The momentum of 모멘텀 전략") == ["momentum", "모멘", "멘텀", "전략"]


def test_related_old_literature_beats_unrelated_recent(planning):
    pid = planning.project["project_id"]
    related = literature(planning.company, pid, "rel", title="ETF momentum after trading costs",
                         idea="Momentum premium in Korean ETF", market="한국 ETF", at=now() - timedelta(days=60))
    for n in range(25):
        literature(planning.company, pid, f"noise{n}", title=f"Chinese housing credit {n}", idea="Housing")
    result = evidence(planning, question(planning)["id"])
    literature_members = [m for m in result["members"] if m["role"] != "explicit"]
    # Housing documents share no question term, so none is selected as related evidence.
    assert [m["source_id"] for m in literature_members] == [related]
    assert len(result["cards"]) <= POLICY["max_cards"]
    assert all(len(json.dumps(card, ensure_ascii=False, sort_keys=True)) <= POLICY["card_chars"]
               for card in result["cards"])


def test_counter_and_foundation_slots_are_reserved_when_present(planning):
    pid = planning.project["project_id"]
    for n in range(14):
        literature(planning.company, pid, f"mom{n}", title=f"Momentum ETF study {n}", idea="momentum momentum")
    counter = literature(planning.company, pid, "counter", title="Momentum fails after costs",
                         idea="replication failure", kind="replication")
    classic = literature(planning.company, pid, "classic", title="Classic momentum", idea="momentum",
                         vintage="classic")
    result = evidence(planning, question(planning, counter_terms=["failure", "replication"])["id"])
    roles = {m["source_id"]: m["role"] for m in result["members"]}
    assert roles[counter] == "counter" and roles[classic] == "foundation"
    assert result["notes"]["counter_reserved"] and result["notes"]["foundation_reserved"]


def test_required_sources_over_budget_make_oversize_and_one_gap(planning):
    company, pid = planning.company, planning.project["project_id"]
    refs = []
    for n in range(4):
        refs.append(f"fixture:big{n}")
        company.put_source(source_id=refs[-1], title=f"Large original {n}", uri=f"fixture://big{n}",
                           content=("x" * 99_000) + f" {n}", available_at=now() - timedelta(days=1),
                           project_id=pid, approved=True, synthetic=True)
    opened = approve(planning, question(planning, source_refs=refs))
    for _ in range(2):
        with company.db.transaction() as conn:
            frozen = freeze(company, conn, opened["id"])
    assert frozen["state"] == "oversize"
    assert {m["source_id"] for m in frozen["members"] if m["full_text"]} == set(refs)
    with company.db.transaction() as conn:
        gaps = conn.execute("SELECT kind,resolver FROM research_gaps WHERE question_id=%s", (opened["id"],)).fetchall()
    assert [(g["kind"], g["resolver"]) for g in gaps] == [("oversize", "owner")]


def test_same_input_same_digest_and_frozen_set_survives_new_literature(planning):
    pid = planning.project["project_id"]
    literature(planning.company, pid, "a", title="Momentum ETF costs", idea="momentum")
    opened = approve(planning, question(planning))
    first = evidence(planning, opened["id"])
    assert evidence(planning, opened["id"])["digest"] == first["digest"]
    with planning.company.db.transaction() as conn:
        frozen = freeze(planning.company, conn, opened["id"])
    literature(planning.company, pid, "b", title="Unrelated housing", idea="housing")
    assert evidence(planning, opened["id"])["corpus_digest"] != frozen["corpus_digest"]
    with planning.company.db.transaction() as conn:
        again = conn.execute("SELECT digest,members FROM research_evidence_sets WHERE id=%s", (frozen["id"],)).fetchone()
    assert again["digest"] == frozen["digest"] and again["members"] == frozen["members"]


def test_freeze_requires_an_owner_activated_question(planning):
    with pytest.raises(PolicyError, match="owner-activated"):
        with planning.company.db.transaction() as conn:
            freeze(planning.company, conn, question(planning)["id"])


def test_plain_sources_get_excerpt_cards(planning):
    planning.company.put_source(source_id="fixture:plain", title="Plain momentum note", uri="fixture://plain",
        content="Momentum note without a structured brief.", available_at=now() - timedelta(days=1),
        project_id=planning.project["project_id"], approved=True, synthetic=True)
    result = evidence(planning, question(planning, source_refs=["fixture:plain"])["id"])
    card = next(c for c in result["cards"] if c["source_id"] == "fixture:plain")
    assert card["excerpt"].startswith("Momentum note") and card["role"] == "explicit"


def test_no_matching_terms_keeps_required_members(planning):
    planning.company.put_source(source_id="fixture:only", title="Required note", uri="fixture://only",
        content="Required.", available_at=now() - timedelta(days=1), project_id=planning.project["project_id"],
        approved=True, synthetic=True)
    literature(planning.company, planning.project["project_id"], "z", title="Housing", idea="housing")
    result = evidence(planning, question(planning, source_refs=["fixture:only"], search_terms_en=["zebra"],
                                         search_terms_ko=[])["id"])
    assert [m["source_id"] for m in result["members"]] == ["fixture:only"]
    assert result["state"] == "ready"


def test_bound_question_reports_refs_outside_program(program):  # noqa: F811
    h = program
    h.company.settings.research_planning_enabled = True
    h.company.put_source(source_id="fixture:outside", title="Project note", uri="fixture://outside",
        content="Project note.", available_at=now() - timedelta(days=1), project_id=h.project["project_id"],
        approved=True, synthetic=True)
    with h.company.db.transaction() as conn:
        drafted = QuestionStore(h.company).draft(conn, h.company._project(conn, h.project["project_id"]),
            spec(source_refs=["fixture:outside"]), actor="director", program_id=h.program_id)
        result = compute(h.company, conn, drafted["id"])
    assert result["notes"]["excluded_refs"] == ["fixture:outside"]
    assert "fixture:outside" not in {m["source_id"] for m in result["members"]}
    assert {m["source_id"] for m in result["members"]} == {"fixture:baseline"}
    assert result["program_manifest_digest"] == h.digest
