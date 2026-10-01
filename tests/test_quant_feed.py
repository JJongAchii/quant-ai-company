import copy
import io
import json
import sys
from datetime import UTC, datetime
from unittest.mock import Mock

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from quant_company.company import fingerprint
from quant_company.contracts import AgentDecision, ArtifactDraft, ProviderResponse, Role
from quant_company.quant_feed import schedule
from quant_company.quant_feed.contracts import (
    EditorialCritique,
    EvidenceCritique,
    QuantSource,
    ResearchBrief,
    load_sources,
)
from quant_company.quant_feed.editor import (
    INSTRUCTIONS,
    ProposalValidationError,
    prompt,
    render,
    source_spans,
    validate,
)
from quant_company.quant_feed.feeds import aliases, collect, obvious_nonresearch_title
from quant_company.quant_feed.originals import download, extract_pdf, fetch_original, parse_html
from quant_company.quant_feed.store import QuantFeedStore, _bounded_pages
from quant_company.slack import SlackIngress, SlackOutbox

TEXT = ("A study by Example Author, published 2026-09-01. US equities from 2000 to 2020. "
        "The chronological holdout shows weaker performance than the training sample. "
        "Transaction costs are not estimated. The study does not establish tradability. ") * 5


def brief(**updates):
    value = dict(disposition="publish", reason="검증 한계에 대한 연구적 가치", title="예측 검증의 한계",
                 authors=["Example Author"], published_on="2026-09-01", kind="empirical", maturity="working_paper",
                 market="미국 주식", topic="research_validity", vintage="recent", why_read="시간 분할 검증의 중요성",
                 idea="학습·검증 성과 차이를 비교한다.", data_period="2000–2020 미국 주식",
                 validation="시간 순 홀드아웃", author_results="검증 성과가 학습 성과보다 약하다고 저자가 보고",
                 costs_turnover="거래비용 추정 미기재", limitations=["거래비용 및 실거래 가능성 검증 부재"],
                 application="한국 적용에는 시점별 종목·가격 데이터 필요. 로컬 가용성 미확인.",
                 evidence=[{"claim": "기간", "location": "PDF p.1", "quote": "US equities from 2000 to 2020."},
                           {"claim": "한계", "location": "PDF p.1", "quote": "Transaction costs are not estimated."}])
    value.update(updates)
    return value


def bound_brief(*, first="p1-s1", second="p1-s2", **updates):
    def source(text, *ids):
        return {"text": text, "basis": "source", "span_ids": list(ids)}

    def gap(text):
        return {"text": text, "basis": "qualified_gap", "span_ids": []}

    def interpretation(text):
        return {"text": text, "basis": "interpretation", "span_ids": []}

    value = brief(
        market=[source("미국 주식시장", first)],
        why_read=[interpretation("시장 검증 방식에 참고할 만하다.")],
        idea=[source("시점별 분할 검증을 비교한다.", first)],
        data_period=[source("미국 주식 표본을 사용한다.", first)],
        validation=[source("시간 순 홀드아웃을 사용했다.", first)],
        author_results=[source("학습 표본과 검증 표본을 비교했다.", first),
                        source("검증 성과는 학습보다 약했다.", second)],
        costs_turnover=[gap("비용 추정은 제공 원문에서 확인되지 않음.")],
        limitations=[interpretation("실거래 적용에는 별도 검증이 필요하다.")],
        application=[interpretation("시점별 한국 시장자료로 후속 검증을 고려할 수 있다.")],
        evidence=[],
    )
    value.update(updates)
    return value


def critique(**updates):
    value = dict(disposition="pass", reason="원문과 주장 일치", original_sufficient=True, claims_supported=True,
                 dates_authors_verified=True, limitations_honest=True, direct_quant_scope=True,
                 substantive_research=True, relevance_and_value=True,
                 no_investment_advice=True, material_change_verified=True, issues=[])
    value.update(updates)
    return value


def response(ready, content):
    return ProviderResponse(request_id=ready["request"]["request_id"], provider="fixture",
                            decision=AgentDecision(status="complete", say="", artifacts=[
                                ArtifactDraft(title="quant review", content=json.dumps(content, ensure_ascii=False))]))


@pytest.fixture
def quant(company, tmp_path, monkeypatch):
    company.settings.quant_feed_enabled = True
    company.settings.quant_feed_publish_enabled = True
    company.settings.quant_feed_channel_id = "CQUANT"
    company.settings.quant_feed_owner_user = "UHUMAN"
    company.settings.company_web_enabled = False
    company.roles["quant_scout"] = Role(id="quant_scout", name="Quant Scout", mission="Synthetic curation test",
                                       model="gpt-5.6-luna", instructions="Delivery only", tools=[], can_delegate_to=[])
    source = QuantSource(id="example", publisher="Example research", kind="seed", url="https://example.org/paper",
                         article_hosts=["example.org", "arxiv.org"])
    path = tmp_path / "sources.json"
    path.write_text(json.dumps([source.model_dump()]))
    company.settings.quant_feed_sources_file = path
    monkeypatch.setattr(schedule, "delivery_time", lambda at: at)
    return QuantFeedStore(company)


def original(store, suffix="", text=TEXT, metadata=None, receipt_updates=None):
    claimed = store.claim_source()
    source = store.sources()["example"]
    entry = {"url": source.url + suffix, "title": "Research" + suffix, "metadata": metadata or {}}
    if claimed:
        store.save_source(claimed, {"ok": True, "entries": [entry]})
    else:
        with store.db.transaction() as conn:
            store.add_candidate(conn, source, entry)
    candidate = store.claim_candidate()
    receipt = {"ok": True, "url": candidate["url"], "original_sha256": fingerprint(text),
               "pages": [{"location": "PDF p.1", "text": text}], "metadata": {}, "links": [],
               "truncated": False}
    receipt.update(receipt_updates or {})
    return store.save_original(candidate, receipt)


def publish(store, **updates):
    first = store.prepare()
    store.commit(response(first, brief(**updates)))
    second = store.prepare()
    assert second["request"]["request_id"] != first["request"]["request_id"]
    return store.commit(response(second, critique()))


def test_real_postgres_two_stage_atomic_outbox_and_idempotency(quant):
    original(quant)
    ready = quant.prepare()
    assert quant.prepare() == ready
    result = response(ready, brief())
    quant.commit(result)
    assert quant.commit(result)["duplicate"]
    with quant.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0
        assert conn.execute("SELECT reserved FROM daily_usage").fetchone()["reserved"] == 1
    ready = quant.prepare()
    result = response(ready, critique())
    assert quant.commit(result)["document_state"] == "queued"
    assert quant.commit(result)["duplicate"]
    with quant.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox WHERE agent='quant_scout'").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM turns").fetchone()["n"] == 0
        assert conn.execute("SELECT reserved FROM daily_usage").fetchone()["reserved"] == 2


def test_failed_document_does_not_block_next_or_replay_uncertain(quant):
    original(quant)
    first = quant.prepare()
    quant.fault(first["request"]["request_id"], "uncertain")
    original(quant, "-next")
    next_request = quant.prepare()
    assert next_request["request"]["request_id"] != first["request"]["request_id"]
    with quant.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM quant_feed_calls WHERE state='blocked'").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM quant_feed_documents WHERE state='held'").fetchone()["n"] == 1


def test_disabled_source_quarantines_existing_ready_original_without_deleting_it(quant):
    original(quant)
    source = quant.sources()["example"].model_copy(update={"enabled": False})
    quant.company.settings.quant_feed_sources_file.write_text(json.dumps([source.model_dump()]))
    assert quant.prepare()["state"] == "idle"
    with quant.db.transaction() as conn:
        document = conn.execute("SELECT state,error FROM quant_feed_documents").fetchone()
    assert (document["state"], document["error"]) == ("held", "source_disabled_requires_requalification")


def test_obvious_two_sigma_nonresearch_is_quarantined_without_model_call(quant):
    original(quant)
    source = next(s for s in load_sources() if s.id == "two-sigma")
    quant.company.settings.quant_feed_sources_file.write_text(json.dumps([
        quant.sources()["example"].model_dump(), source.model_dump()]))
    with quant.db.transaction() as conn:
        quant.sync_sources(conn)
        conn.execute("UPDATE quant_feed_candidates SET source_id=%s,title=%s",
                     ("two-sigma", "Office Hours with a Portfolio Manager"))
    assert quant.prepare()["state"] == "idle"
    with quant.db.transaction() as conn:
        document = conn.execute("SELECT state,error FROM quant_feed_documents").fetchone()
        calls = conn.execute("SELECT count(*) AS n FROM quant_feed_calls").fetchone()["n"]
    assert (document["state"], document["error"], calls) == ("held", "source_title_nonresearch_precheck", 0)
    assert not obvious_nonresearch_title("two-sigma", "Thematic Research: Forecasting Factor Returns")


def test_two_bounded_revisions_then_hold(quant):
    original(quant)
    quant.commit(response(quant.prepare(), brief()))
    revision = critique(disposition="revise", claims_supported=False, issues=["claim overstatement"])
    quant.commit(response(quant.prepare(), revision))
    quant.commit(response(quant.prepare(), brief()))
    assert quant.commit(response(quant.prepare(), revision))["document_state"] == "ready"
    quant.commit(response(quant.prepare(), brief()))
    assert quant.commit(response(quant.prepare(), revision))["document_state"] == "held"
    assert quant.prepare()["state"] == "idle"


def test_second_revision_can_pass_but_never_skips_independent_critique(quant):
    original(quant)
    quant.commit(response(quant.prepare(), brief()))
    revision = critique(disposition="revise", claims_supported=False, issues=["claim overstatement"])
    quant.commit(response(quant.prepare(), revision))
    quant.commit(response(quant.prepare(), brief()))
    quant.commit(response(quant.prepare(), revision))
    quant.commit(response(quant.prepare(), brief()))
    with quant.db.transaction() as conn:
        document = conn.execute("SELECT state,revision FROM quant_feed_documents").fetchone()
    assert (document["state"], document["revision"]) == ("ready", 2)
    assert quant.commit(response(quant.prepare(), critique()))["document_state"] == "queued"


def test_preview_never_queues_and_activation_does_not_replay_it(quant):
    quant.company.settings.quant_feed_publish_enabled = False
    original(quant)
    assert publish(quant)["document_state"] == "preview"
    quant.company.settings.quant_feed_publish_enabled = True
    assert quant.prepare()["state"] == "idle"
    assert quant.status()["deliveries"] == []


def test_bundle_distinguishes_original_truncation_from_bounded_context(quant):
    original(quant, text="HEAD " + TEXT * 100 + " TAIL")
    with quant.db.transaction() as conn:
        document = conn.execute("SELECT * FROM quant_feed_documents").fetchone()
        bundle = quant.bundle(conn, document)
    assert bundle["context_clipped"] is True
    assert bundle["truncated"] is False
    assert len(bundle["pages"]) == 3
    assert bundle["pages"][0]["text"].startswith("HEAD")
    assert bundle["pages"][-1]["text"].endswith("TAIL")


def test_uneven_full_original_is_not_unnecessarily_clipped():
    pages = [{"location": "PDF p.1", "text": "x" * 30000},
             {"location": "PDF p.2", "text": "y" * 1000}]
    assert _bounded_pages(pages) == (pages, False)


def test_source_spans_are_lossless_bounded_and_deterministic():
    pages = [{"location": "PDF p.1", "text": TEXT + "end"},
             {"location": "PDF p.2", "text": "A long uninterrupted word " * 80}]
    spans = source_spans(pages)
    assert spans == source_spans(pages)
    assert len({s["span_id"] for s in spans}) == len(spans)
    assert all(8 <= len(s["text"]) <= 507 for s in spans)
    for page in pages:
        assert "".join(s["text"] for s in spans if s["location"] == page["location"]) == page["text"]
    data = json.loads(prompt({"pages": pages}, "review").split("\nDATA:\n")[1])
    assert "pages" not in data and data["source_spans"] == spans


def test_native_references_resolve_exact_original_and_keep_ids_in_revision_prompt(quant):
    original(quant)
    ready = quant.prepare()
    with quant.db.transaction() as conn:
        bundle = conn.execute("SELECT bundle FROM quant_feed_calls WHERE id=%s",
                              (ready["request"]["request_id"],)).fetchone()["bundle"]
    spans = source_spans(bundle["pages"])
    value = brief(evidence=[{"claim": "원문 기간", "span_id": spans[0]["span_id"]},
                            {"claim": "원문 한계", "span_id": spans[1]["span_id"]}])
    proposed = response(ready, value)
    proposed.decision.artifacts[0].title = "quant_brief_v2"
    result = quant.commit(proposed)
    assert [r["kind"] for r in result["source_corrections"]] == ["source_span_resolved"] * 2
    with quant.db.transaction() as conn:
        doc = conn.execute("SELECT * FROM quant_feed_documents").fetchone()
        revised_bundle = quant.bundle(conn, doc)
    assert doc["brief"]["evidence"][0]["quote"] == spans[0]["text"]
    view = json.loads(prompt(revised_bundle, "revision").split("\nDATA:\n")[1])
    assert view["draft"]["evidence"] == [{"claim": e["claim"], "span_ids": [e["span_id"]]} for e in value["evidence"]]
    # Exact quotation is not semantic support: the separate critic can still refuse it.
    held = critique(disposition="hold", claims_supported=False, issues=["claim not supported by selected passage"])
    assert quant.commit(response(quant.prepare(), held))["document_state"] == "held"
    with quant.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0


def test_unknown_source_span_is_actionable_and_never_becomes_evidence(quant):
    original(quant)
    ready = quant.prepare()
    proposed = response(ready, brief(evidence=[{"claim": "invented reference", "span_id": "p999-s999"}] * 2))
    proposed.decision.artifacts[0].title = "quant_brief_v2"
    result = quant.commit(proposed)
    assert result["validation_issue"] == "quant_unknown_evidence_span"
    assert result["validation_issues"][0]["field"] == "evidence[0].span_id"
    with quant.db.transaction() as conn:
        assert conn.execute("SELECT revision FROM quant_feed_documents").fetchone()["revision"] == 0
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0


def test_a_compound_claim_can_bind_multiple_exact_source_spans_without_duplicate_prompt_text():
    pages = [{"location": "PDF p.1", "text": "The study runs 100000 callbacks on 10000 paths."},
             {"location": "PDF p.2", "text": "The outputs are volatility and collapse probability. Costs are not estimated."}]
    bundle = {"pages": pages, "as_of": "2026-09-29", "links": [], "commercial": False,
              "metadata": {"publisher": "Example", "url": "https://example.org/paper"}}
    value = brief(evidence=[{"claim": "Runs and both defined observables", "span_ids": ["p1-s1", "p2-s1"]},
                            {"claim": "Costs not estimated", "span_ids": ["p2-s1"]}])
    proposed = response({"request": {"request_id": "quant-feed-grouped"}}, value)
    proposed.decision.artifacts[0].title = "quant_brief_v3"
    audit = []
    resolved = validate(proposed, bundle, "review", audit=audit)
    assert [span.quote for span in resolved.evidence[0].source_spans] == [page["text"] for page in pages]
    assert len(audit) == 3
    assert "PDF p.1–2" in render(resolved, bundle["metadata"])
    bundle["draft"] = resolved.model_dump()
    for stage in ("revision", "critique"):
        view = json.loads(prompt(bundle, stage).split("\nDATA:\n")[1])
        assert view["draft"]["evidence"] == value["evidence"]
        assert all(set(item) == {"claim", "span_ids"} for item in view["draft"]["evidence"])
    tampered = resolved.model_dump()
    tampered["evidence"][0]["source_spans"][1]["quote"] = "An invented result that is absent from the original."
    forged = response({"request": {"request_id": "quant-feed-forged"}}, tampered)
    with pytest.raises(ProposalValidationError, match="quant_evidence_span_mismatch"):
        validate(forged, bundle, "review")


def test_field_bound_results_cannot_lose_a_different_result_when_references_change():
    bundle = {"pages": [{"location": "PDF p.1", "text": "A method compares training and holdout samples. "
                         "The training result exceeds the holdout result."},
                        {"location": "PDF p.2", "text": "A balanced result appears only in this second passage."}],
              "metadata": {"publisher": "Example", "url": "https://example.org/paper"},
              "as_of": "2026-09-29", "links": [], "commercial": False}
    value = bound_brief(first="p1-s1", second="p2-s1", author_results=[
        {"text": "학습 결과가 검증 결과보다 높았다.", "basis": "source", "span_ids": ["p1-s1"]},
        {"text": "균형 결과도 보고했다.", "basis": "source", "span_ids": ["p2-s1"]},
    ])
    proposed = response({"request": {"request_id": "quant-feed-bound"}}, value)
    proposed.decision.artifacts[0].title = "quant_brief_v4"
    resolved = validate(proposed, bundle, "review")
    assert resolved.author_results == "학습 결과가 검증 결과보다 높았다. 균형 결과도 보고했다."
    assert [(item.field_path, [span.span_id for span in item.source_spans]) for item in resolved.evidence
            if item.field_path.startswith("author_results")] == [
                ("author_results[0]", ["p1-s1"]), ("author_results[1]", ["p2-s1"])]
    bundle["draft"], bundle["source_draft"] = resolved.model_dump(), value
    bundle["previous_critique"] = {"issues": ["old objection"]}
    revision = json.loads(prompt(bundle, "revision").split("\nDATA:\n")[1])
    criticism = json.loads(prompt(bundle, "critique").split("\nDATA:\n")[1])
    assert revision["draft"] == value and "source_draft" not in revision
    assert revision["previous_critique"]["issues"] == ["old objection"]
    assert "previous_critique" not in criticism
    assert criticism["draft"]["evidence"][-1]["field_path"] == "author_results[1]"
    value["author_results"][1]["span_ids"] = ["p99-s1"]
    proposed.decision.artifacts[0].content = json.dumps(value, ensure_ascii=False)
    with pytest.raises(ProposalValidationError) as error:
        validate(proposed, bundle, "revision")
    assert error.value.issues[0]["field"] == "author_results[1].span_ids"
    value["author_results"][1].update(basis="qualified_gap", span_ids=[])
    proposed.decision.artifacts[0].content = json.dumps(value, ensure_ascii=False)
    with pytest.raises(ProposalValidationError) as error:
        validate(proposed, bundle, "revision")
    assert error.value.issues[0]["field"] == "author_results[1]"


def test_atomic_field_evidence_can_exceed_legacy_limit_without_losing_source_bindings():
    bundle = {"pages": [{"location": "PDF p.1", "text": TEXT}],
              "metadata": {"publisher": "Example", "url": "https://example.org/paper"},
              "as_of": "2026-09-29", "links": [], "commercial": False}
    value = bound_brief()
    value["author_results"].extend([
        {"text": text, "basis": "source", "span_ids": ["p1-s1"]}
        for text in ("검증 결과를 보고했다.", "학습 결과를 보고했다.", "비용을 추정하지 않았다.", "실거래를 검증하지 않았다.")])
    value["limitations"] = [{"text": text, "basis": "source", "span_ids": ["p1-s1"]}
                            for text in ("비용을 추정하지 않았다.", "실거래를 검증하지 않았다.", "검증 성과가 약했다.")]
    proposed = response({"request": {"request_id": "quant-feed-atomic-capacity"}}, value)
    proposed.decision.artifacts[0].title = "quant_brief_v4"
    resolved = validate(proposed, bundle, "review")
    assert len(resolved.evidence) == 13
    assert {e.field_path for e in resolved.evidence if e.field_path.startswith("author_results")} == {
        f"author_results[{index}]" for index in range(6)}
    assert all(e.source_spans[0].quote in TEXT for e in resolved.evidence)
    value["limitations"][0]["span_ids"] = ["p99-s1"]
    proposed.decision.artifacts[0].content = json.dumps(value, ensure_ascii=False)
    with pytest.raises(ProposalValidationError, match="quant_statement_source_invalid"):
        validate(proposed, bundle, "review")


def test_clipped_original_gap_must_name_the_supplied_excerpts():
    bundle = {"pages": [{"location": "PDF p.1", "text": TEXT}], "context_clipped": True,
              "metadata": {"publisher": "Example", "url": "https://example.org/paper"},
              "as_of": "2026-09-29", "links": [], "commercial": False}
    value = bound_brief(costs_turnover=[{"text": "거래비용은 제공 원문에서 확인되지 않는다.",
                                           "basis": "qualified_gap", "span_ids": []}])
    proposed = response({"request": {"request_id": "quant-feed-clipped"}}, value)
    proposed.decision.artifacts[0].title = "quant_brief_v4"
    audit = []
    assert validate(proposed, bundle, "review", audit=audit).costs_turnover == "거래비용은 제공 발췌에서 확인되지 않는다."
    assert {"kind": "clipped_gap_scope_narrowed", "field": "costs_turnover[0]"} in audit
    value["costs_turnover"][0]["text"] = "제공된 원문 전체에 거래비용 검증이 없다."
    proposed.decision.artifacts[0].content = json.dumps(value, ensure_ascii=False)
    with pytest.raises(ProposalValidationError) as error:
        validate(proposed, bundle, "review")
    assert {"code": "quant_clipped_gap_requires_excerpt_scope", "field": "costs_turnover[0]"} in error.value.issues
    value["costs_turnover"][0]["text"] = "거래비용은 제공 발췌에서 확인되지 않는다."
    proposed.decision.artifacts[0].content = json.dumps(value, ensure_ascii=False)
    assert validate(proposed, bundle, "review").costs_turnover == "거래비용은 제공 발췌에서 확인되지 않는다."


def test_field_bound_revision_survives_real_postgres_without_source_draft_column(quant):
    original(quant)
    first = quant.prepare()
    with quant.db.transaction() as conn:
        frozen = conn.execute("SELECT bundle FROM quant_feed_calls WHERE id=%s", (first["request"]["request_id"],)).fetchone()["bundle"]
    ids = [span["span_id"] for span in source_spans(frozen["pages"])]
    value = bound_brief(first=ids[0], second=ids[1])
    draft = response(first, value)
    draft.decision.artifacts[0].title = "quant_brief_v4"
    assert quant.commit(draft)["document_state"] == "ready"
    requested = critique(disposition="revise", claims_supported=False, issues=["author_results[1]: qualify"])
    quant.commit(response(quant.prepare(), requested))
    revising = quant.prepare()
    view = json.loads(revising["request"]["prompt"].split("\nDATA:\n")[1])
    assert view["draft"] == value
    assert view["previous_critique"]["issues"] == requested["issues"]
    with quant.db.transaction() as conn:
        saved = conn.execute("SELECT brief FROM quant_feed_documents").fetchone()["brief"]
    assert saved["author_results"] == "학습 표본과 검증 표본을 비교했다. 검증 성과는 학습보다 약했다."


def test_page_budget_redistributes_short_pages_and_remains_bounded():
    pages = [{"location": "PDF p.1", "text": "A" * 50000},
             {"location": "PDF p.2", "text": "B" * 1000}]
    bounded, clipped = _bounded_pages(pages)
    total = sum(len(p["text"]) for p in bounded)
    assert clipped and 41997 <= total <= 42000
    assert bounded[-1] == pages[-1]
    small, clipped = _bounded_pages(pages * 10, budget=7)
    assert clipped and sum(len(p["text"]) for p in small) <= 7


def test_nul_in_extracted_text_is_replaced_and_receipted(quant):
    assert original(quant, text=TEXT + "\x00equation")["state"] == "ready"
    with quant.db.transaction() as conn:
        document = conn.execute("SELECT pages,receipt FROM quant_feed_documents").fetchone()
    assert "\x00" not in document["pages"][0]["text"]
    assert "\ufffdequation" in document["pages"][0]["text"]
    assert document["receipt"]["original_sha256"] == fingerprint(TEXT + "\x00equation")
    assert document["receipt"]["text_sanitization"] == {
        "nul_replacements": 1, "replacement": "U+FFFD",
    }


def test_new_work_does_not_require_prior_change_comparison_but_updates_do():
    ready = {"request": {"request_id": "quant-feed-critique"}}
    value = response(ready, critique(material_change_verified=False))
    bundle = {"prior": None, "draft": {"change": "new"}}
    assert validate(value, bundle, "critique").disposition == "pass"
    bundle = {"prior": {"publication_id": "prior"}, "draft": {"change": "material"}}
    with pytest.raises(ValueError, match="quant_unverified_material_change"):
        validate(value, bundle, "critique")


def test_quote_validation_ignores_pdf_layout_whitespace():
    ready = {"request": {"request_id": "quant-feed-review"}}
    bundle = {"pages": [{"location": "PDF p.1", "text": "a convex functionf on a B-bounded domain"}],
              "as_of": "2026-09-22T00:00:00+00:00", "links": [], "commercial": False, "prior": None,
              "metadata": {"publisher": "Example", "url": "https://example.org/paper"}}
    value = brief(evidence=[
        {"claim": "layout", "location": "PDF p.1", "quote": "convex function f on a B-bounded domain"},
        {"claim": "domain", "location": "PDF p.1", "quote": "B-bounded domain"},
    ])
    assert validate(response(ready, value), bundle, "review").disposition == "publish"
    value["evidence"][0]["quote"] = "convex function g on a B-bounded domain"
    with pytest.raises(ValueError, match="quant_quote_not_in_original_version"):
        validate(response(ready, value), bundle, "review")


def test_quote_initial_ascii_case_only_is_audited_without_rewording():
    ready = {"request": {"request_id": "quant-feed-review"}}
    text = ("In the model, we will ask the GMM to model all 17 factors in the lens. "
            "Transaction costs are not estimated.")
    bundle = {"pages": [{"location": "PDF p.1", "text": text}],
              "as_of": "2026-09-22", "links": [], "commercial": False, "prior": None,
              "metadata": {"publisher": "Example", "url": "https://example.org/paper"}}
    value = brief(evidence=[
        {"claim": "모형", "location": "PDF p.1", "quote": "We will ask the GMM to model all 17 factors in the lens."},
        {"claim": "비용", "location": "PDF p.1", "quote": "Transaction costs are not estimated."},
    ])
    corrections = []
    assert validate(response(ready, value), bundle, "review", audit=corrections).disposition == "publish"
    assert corrections == [{"kind": "initial_case_quote_match", "evidence_index": 0,
                            "location": "PDF p.1"}]
    value["evidence"][0]["quote"] = "We will ask the GMM to model all 18 factors in the lens."
    with pytest.raises(ValueError, match="quant_quote_not_in_original_version"):
        validate(response(ready, value), bundle, "review")
    value["evidence"][0]["quote"] = "WE will ask the GMM to model all 17 factors in the lens."
    with pytest.raises(ValueError, match="quant_quote_not_in_original_version"):
        validate(response(ready, value), bundle, "review")


def test_initial_case_quote_wrong_location_requires_unique_original_match():
    ready = {"request": {"request_id": "quant-feed-review"}}
    text = "we will ask the GMM to model all 17 factors in the lens. Transaction costs are not estimated."
    bundle = {"pages": [{"location": "PDF p.1", "text": text}],
              "as_of": "2026-09-22", "links": [], "commercial": False, "prior": None,
              "metadata": {"publisher": "Example", "url": "https://example.org/paper"}}
    value = brief(evidence=[
        {"claim": "모형", "location": "PDF p.2", "quote": "We will ask the GMM to model all 17 factors in the lens."},
        {"claim": "비용", "location": "PDF p.1", "quote": "Transaction costs are not estimated."},
    ])
    corrections = []
    validated = validate(response(ready, value), bundle, "review", audit=corrections)
    assert validated.evidence[0].location == "PDF p.1"
    assert [item["kind"] for item in corrections] == ["unique_quote_location", "initial_case_quote_match"]
    bundle["pages"].append({"location": "PDF p.3", "text": text})
    with pytest.raises(ValueError, match="quant_quote_not_in_original_version"):
        validate(response(ready, value), bundle, "review")


def test_ambiguous_quote_location_is_never_inferred():
    ready = {"request": {"request_id": "quant-feed-review"}}
    bundle = {"pages": [{"location": "PDF p.1", "text": TEXT},
                        {"location": "PDF p.2", "text": TEXT}],
              "as_of": "2026-09-22", "links": [], "commercial": False, "prior": None,
              "metadata": {"publisher": "Example", "url": "https://example.org/paper"}}
    value = brief(evidence=[{"claim": "기간", "location": "PDF p.3", "quote": "US equities from 2000 to 2020."},
                            {"claim": "한계", "location": "PDF p.2", "quote": "Transaction costs are not estimated."}])
    with pytest.raises(ValueError, match="quant_quote_not_in_original_version"):
        validate(response(ready, value), bundle, "review")
    value["evidence"][0]["quote"] = "         "
    with pytest.raises(ValueError, match="quant_quote_not_in_original_version"):
        validate(response(ready, value), bundle, "review")


def test_raw_json_control_characters_are_only_normalized_inside_strings():
    content = json.dumps(brief(), ensure_ascii=False).replace("검증 한계에 대한 연구적 가치", "검증 한계\n연구적 가치")
    value = ProviderResponse(request_id="quant-feed-review", provider="fixture",
                             decision=AgentDecision(status="complete", say="", artifacts=[
                                 ArtifactDraft(title="quant review", content=content)]))
    bundle = {"pages": [{"location": "PDF p.1", "text": TEXT}],
              "as_of": "2026-09-22T00:00:00+00:00", "links": [], "commercial": False, "prior": None,
              "metadata": {"publisher": "Example", "url": "https://example.org/paper"}}
    assert validate(value, bundle, "review").reason == "검증 한계 연구적 가치"


@pytest.mark.parametrize("source,quote,accepted", [
    ("Fixed set-\ntings cannot correct time-\nvarying bias.", "Fixed settings cannot correct time-varying bias.", True),
    ("The result is statis-\ntically inconclusive.", "The result is statistically inconclusive.", True),
    ("The result is statistically inconclusive.", "The result is statistically conclusive.", False),
    ("A long-term result was reported.", "A longterm result was reported.", False),
    ("The result was -\n12 percent.", "The result was 12 percent.", False),
    ("The equation is x-\ny in this study.", "The equation is xy in this study.", False),
    ("The result was inconclusive. Further data are needed.", "The result was inconclusive...data are needed.", False),
])
def test_pdf_line_wrap_normalization_does_not_allow_semantic_rewording(source, quote, accepted):
    value = response({"request": {"request_id": "quant-feed-layout"}},
                     brief(evidence=[{"claim": "원문 주장", "location": "PDF p.1", "quote": quote},
                                     {"claim": "한계", "location": "PDF p.1", "quote": "Transaction costs are not estimated."}]))
    bundle = {"pages": [{"location": "PDF p.1", "text": source + "\n" + TEXT}],
              "as_of": "2026-09-29", "links": [], "commercial": False,
              "metadata": {"publisher": "Example", "url": "https://example.org/paper"}}
    audit = []
    if accepted:
        assert validate(value, bundle, "review", audit=audit).disposition == "publish"
        assert audit == [{"kind": "pdf_line_wrap_hyphen_match", "evidence_index": 0, "location": "PDF p.1"}]
        assert bundle["pages"][0]["text"].startswith(source)
    else:
        with pytest.raises(ProposalValidationError):
            validate(value, bundle, "review", audit=audit)


def test_one_deterministic_proposal_repair_then_fail_closed(quant):
    original(quant)
    invalid = brief(evidence=[
        {"claim": "fabricated", "location": "PDF p.1", "quote": "Invented result one"},
        {"claim": "fabricated", "location": "PDF p.1", "quote": "Invented result two"},
    ])
    first = quant.prepare()
    repaired = quant.commit(response(first, invalid))
    assert repaired["document_state"] == "ready"
    assert repaired["validation_issue"] == "quant_quote_not_in_original_version"
    with quant.db.transaction() as conn:
        document = conn.execute("SELECT state,stage,revision,critique,brief FROM quant_feed_documents").fetchone()
    assert (document["state"], document["stage"], document["revision"]) == ("ready", "repair", 0)
    assert document["brief"]["evidence"] == [
        {**item, "span_id": "", "field_path": "", "source_spans": []} for item in invalid["evidence"]]
    assert [i["field"] for i in document["critique"]["issues"]] == ["evidence[0]", "evidence[1]"]
    second = quant.prepare()
    with pytest.raises(ValueError, match="quant_quote_not_in_original_version"):
        quant.commit(response(second, invalid))
    quant.fault(second["request"]["request_id"], "invalid_quant_proposal")
    assert quant.prepare()["state"] == "idle"


@pytest.mark.parametrize("repair_after_revision", [False, True])
def test_technical_repair_and_editorial_revision_have_separate_bounded_budgets(quant, repair_after_revision):
    original(quant)
    bad = brief(evidence=[{"claim": "bad", "location": "PDF p.1", "quote": "Invented unsupported result"}] * 2)
    critic = critique(disposition="revise", claims_supported=False, issues=["idea: qualify the conclusion"])
    if repair_after_revision:
        quant.commit(response(quant.prepare(), brief()))
        quant.commit(response(quant.prepare(), critic))
    ready = quant.prepare()
    assert ready["request"]["output_contract"] == "quant_brief_v4"
    quant.commit(response(ready, bad))
    with quant.db.transaction() as conn:
        doc = conn.execute("SELECT * FROM quant_feed_documents").fetchone()
        assert doc["revision"] == int(repair_after_revision)
        if repair_after_revision:
            assert doc["critique"]["editorial_critique"]["issues"] == critic["issues"]
    quant.commit(response(quant.prepare(), brief()))
    if not repair_after_revision:
        quant.commit(response(quant.prepare(), critic))
        quant.commit(response(quant.prepare(), brief()))
    final = quant.prepare()
    assert final["request"]["output_contract"] == "quant_critique_v2"
    assert quant.commit(response(final, critic))["document_state"] == "ready"
    quant.commit(response(quant.prepare(), brief()))
    assert quant.commit(response(quant.prepare(), critic))["document_state"] == "held"
    with quant.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM quant_feed_calls WHERE stage='repair'").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0


def test_unique_quote_location_correction_is_audited_before_critic(quant):
    original(quant)
    value = brief(evidence=[{"claim": "기간", "location": "PDF p.2", "quote": "US equities from 2000 to 2020."},
                            {"claim": "한계", "location": "PDF p.1", "quote": "Transaction costs are not estimated."}])
    result = quant.commit(response(quant.prepare(), value))
    assert result["document_state"] == "ready"
    assert [item["kind"] for item in result["source_corrections"]] == ["unique_quote_location"]
    with quant.db.transaction() as conn:
        document = conn.execute("SELECT brief FROM quant_feed_documents").fetchone()
        receipt = conn.execute("SELECT receipt FROM quant_feed_calls").fetchone()["receipt"]
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0
    assert document["brief"]["evidence"][0]["location"] == "PDF p.1"
    assert receipt["source_corrections"] == result["source_corrections"]


def test_long_card_is_rewritten_once_without_dropping_caveats(quant):
    original(quant)
    invalid = brief(idea="연구 방법 설명. " * 90, why_read="읽을 이유. " * 75,
                    limitations=["중요한 한계. " * 75] * 3)
    repaired = quant.commit(response(quant.prepare(), invalid))
    assert repaired["validation_issue"] == "quant_card_too_long"
    with quant.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0
    corrected = brief(limitations=["표본 외 검증 없음", "거래비용 미반영", "시장 충격 미반영"])
    quant.commit(response(quant.prepare(), corrected))
    assert quant.commit(response(quant.prepare(), critique()))["document_state"] == "queued"
    with quant.db.transaction() as conn:
        text = conn.execute("SELECT text FROM outbox").fetchone()["text"]
    assert len(text) <= 2400
    assert all(item in text for item in corrected["limitations"])


def test_old_css_contaminated_title_is_rewritten_before_publication(quant):
    original(quant)
    invalid = brief(title="@keyframes shimmer { background-position: 200% 0 }")
    repaired = quant.commit(response(quant.prepare(), invalid))
    assert repaired["validation_issue"] == "quant_malformed_title"
    with quant.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0


def test_shared_budget_quota_pause_and_same_request_retry(quant):
    original(quant)
    quant.company.settings.company_max_daily_turns = 1
    first = quant.prepare()
    quant.fault(first["request"]["request_id"], "quota", 120)
    assert quant.prepare() == {"state": "defer", "reason": "global_quota_pause"}
    with quant.db.transaction() as conn:
        conn.execute("UPDATE runtime_control SET paused_until=NULL")
        conn.execute("UPDATE quant_feed_calls SET next_at='2020-01-01'")
    assert quant.prepare() == first
    quant.commit(response(first, brief()))
    assert quant.prepare()["reason"] == "daily_model_budget"


def test_owner_account_switch_resumes_quant_without_global_pause(quant):
    from quant_company.accounts import resume_quota_waits
    original(quant)
    quant.company.settings.model_accounts_enabled = True
    first = quant.prepare()
    quant.fault(first["request"]["request_id"], "quota", 120)
    with quant.db.transaction() as conn:
        assert conn.execute("SELECT paused_until FROM runtime_control WHERE id=1").fetchone()["paused_until"] is None
        resume_quota_waits(conn, None, 1)
    assert quant.prepare() == first


def test_policy_edit_stales_frozen_request_without_new_call(quant):
    original(quant)
    first = quant.prepare()
    quant.company.settings.quant_feed_publish_enabled = False
    assert quant.prepare()["state"] == "blocked"
    assert quant.commit(response(first, brief()))["state"] == "stale"
    assert quant.prepare()["state"] == "idle"


def test_doi_arxiv_versions_and_cosmetic_dedupe(quant):
    original(quant, metadata={"doi": "10.1000/example"})
    original(quant, "-journal", metadata={"doi": "10.1000/EXAMPLE"})
    with quant.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM quant_feed_documents").fetchone()["n"] == 1
    assert "arxiv:2609.01234" in aliases("https://arxiv.org/pdf/2609.01234v2.pdf", {})
    assert "arxiv:2609.01234" in aliases("https://arxiv.org/abs/2609.01234v1", {})


async def test_slack_identity_pacing_uncertain_no_replay(quant):
    original(quant)
    publish(quant)
    with quant.db.transaction() as conn:
        conn.execute("UPDATE outbox SET next_at='2020-01-01'")
    credentials = {"quant_scout": {"app_id": "AQ", "bot_user_id": "UQ", "bot_token": "synthetic"}}
    sent = []

    def handle(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})  # Missing receipt is ambiguous, not success.

    outbox = SlackOutbox(quant.company, credentials, httpx.MockTransport(handle))
    assert await outbox.send_one()
    assert not await outbox.send_one()
    assert len(sent) == 1 and sent[0]["channel"] == "CQUANT"
    assert not sent[0]["text"].startswith("[지시")
    assert quant.status()["deliveries"][0]["status"] == "uncertain"
    ingress = SlackIngress(quant.company.settings, quant.company, credentials)
    assert ingress.accept("quant_scout", {"team_id": "TTEST", "api_app_id": "AQ", "event": {
        "type": "app_mention", "user": "UHUMAN", "channel": "CQUANT", "text": "hello", "ts": "1.0"}},
                          credentials["quant_scout"])["ignored"]


def test_source_failure_and_stale_lease_isolation(quant):
    source = quant.claim_source()
    assert quant.claim_source() is None
    bad = {**source, "lease_token": None}
    assert quant.save_source(bad, {"ok": True, "entries": []})["state"] == "stale"
    quant.save_source(source, {"ok": False, "error": "http_status", "http_status": 429, "retry_after": 7200})
    status = quant.status()["sources"][0]
    assert status["failures"] == 1 and status["last_success"] is None
    assert status["error"] == "http_status"


def fair_sources(quant, tmp_path):
    sources = [
        QuantSource(id="fair-a", publisher="A", kind="seed", url="https://a.example.org/index",
                    article_hosts=["a.example.org"]),
        QuantSource(id="fair-b", publisher="B", kind="seed", url="https://b.example.org/index",
                    article_hosts=["b.example.org"]),
    ]
    path = tmp_path / "fair-sources.json"
    path.write_text(json.dumps([source.model_dump() for source in sources]))
    quant.company.settings.quant_feed_sources_file = path
    with quant.db.transaction() as conn:
        quant.sync_sources(conn)
        for source, suffixes in ((sources[0], ["one", "two"]), (sources[1], ["one"])):
            for suffix in suffixes:
                quant.add_candidate(conn, source, {"url": f"https://{source.article_hosts[0]}/{suffix}",
                                                   "title": f"{source.id}-{suffix}", "metadata": {}})
        conn.execute("UPDATE quant_feed_candidates SET discovered_at='2020-01-01' WHERE source_id='fair-a'")
        conn.execute("UPDATE quant_feed_candidates SET discovered_at='2021-01-01' WHERE source_id='fair-b'")
    return sources


def test_candidate_fetch_rotates_across_sources(quant, tmp_path):
    fair_sources(quant, tmp_path)
    first = quant.claim_candidate()
    assert first["source_id"] == "fair-a"
    quant.save_original(first, {"ok": False, "error": "synthetic_hold"})
    assert quant.claim_candidate()["source_id"] == "fair-b"


def test_editor_rotates_across_sources(quant, tmp_path):
    fair_sources(quant, tmp_path)
    for _ in range(3):
        candidate = quant.claim_candidate()
        text = TEXT + candidate["url"]
        quant.save_original(candidate, {"ok": True, "url": candidate["url"],
                                       "original_sha256": fingerprint(text),
                                       "pages": [{"location": "PDF p.1", "text": text}],
                                       "metadata": {}, "links": [], "truncated": False})
    with quant.db.transaction() as conn:
        a = conn.execute("""SELECT d.id FROM quant_feed_documents d JOIN quant_feed_candidates c ON c.id=d.candidate_id
            WHERE c.source_id='fair-a' ORDER BY d.id""").fetchall()
        b = conn.execute("""SELECT d.id FROM quant_feed_documents d JOIN quant_feed_candidates c ON c.id=d.candidate_id
            WHERE c.source_id='fair-b'""").fetchone()
        # A deterministic/model-contract failure is held without reviewed_at; it
        # still consumed the source's editorial turn and must rotate fairly.
        conn.execute("UPDATE quant_feed_documents SET state='held' WHERE id=%s", (a[0]["id"],))
        conn.execute("UPDATE quant_feed_documents SET created_at='2020-01-01' WHERE id=%s", (a[1]["id"],))
        conn.execute("UPDATE quant_feed_documents SET created_at='2021-01-01' WHERE id=%s", (b["id"],))
    request = quant.prepare()
    with quant.db.transaction() as conn:
        selected = conn.execute("""SELECT c.source_id FROM quant_feed_calls q JOIN quant_feed_documents d ON d.id=q.document_id
            JOIN quant_feed_candidates c ON c.id=d.candidate_id WHERE q.id=%s""",
                                (request["request"]["request_id"],)).fetchone()
    assert selected["source_id"] == "fair-b"


def test_editor_prefers_complete_original_within_source_fairness(quant):
    html = original(quant, "-html", receipt_updates={
        "content_type": "text/html", "fulltext_status": "html_requires_evidence_check",
    })
    pdf = original(quant, "-pdf", receipt_updates={"content_type": "application/pdf"})
    request = quant.prepare()
    with quant.db.transaction() as conn:
        selected = conn.execute("SELECT document_id FROM quant_feed_calls WHERE id=%s",
                                (request["request"]["request_id"],)).fetchone()["document_id"]
    assert selected == pdf["document_id"]
    assert selected != html["document_id"]


def test_citation_title_replaces_generic_link_label(quant):
    saved = original(quant, receipt_updates={"metadata": {"citation_title": ["Specific research title"]}})
    with quant.db.transaction() as conn:
        title = conn.execute("SELECT metadata->>'title' AS title FROM quant_feed_documents WHERE id=%s",
                             (saved["document_id"],)).fetchone()["title"]
    assert title == "Specific research title"


def test_delivery_window_and_discovery_slots():
    at = datetime(2026, 9, 21, 17, tzinfo=UTC)
    assert schedule.delivery_time(at).hour == 6  # 06:00 KST next morning
    assert schedule.discovery_slot(at) is None
    assert schedule.discovery_slot(datetime(2026, 9, 22, 0, tzinfo=UTC)) == "2026-09-22-08"
    assert schedule.discovery_slot(datetime(2026, 9, 22, 11, tzinfo=UTC)) == "2026-09-22-20"


def pdf_bytes(page_count=1, text="Evidence original text " * 30):
    writer = PdfWriter()
    for _ in range(page_count):
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 10 10 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    data = io.BytesIO()
    writer.write(data)
    return data.getvalue()


@pytest.mark.skipif(sys.platform != "linux", reason="Hard PDF memory qualification requires Linux RLIMIT_AS")
def test_real_pdf_subprocess_limits_and_pages():
    assert extract_pdf(pdf_bytes())["pages"][0]["location"] == "PDF p.1"
    for content in (b"bad pdf", pdf_bytes(text=""), pdf_bytes(page_count=201)):
        with pytest.raises(ValueError, match="unreadable_or_resource_limit"):
            extract_pdf(content)


@pytest.mark.parametrize("url", ["http://example.org/a", "https://127.0.0.1/a", "https://a.local/a", "https://u:p@example.org/"])
def test_unsafe_originals_rejected_before_network(url):
    connection = Mock()
    with pytest.raises(ValueError):
        download(url, connection_factory=connection)
    connection.assert_not_called()


def test_registry_and_strict_feed_boundaries():
    sources = load_sources()
    assert len(sources) >= 15 and all(s.interval_hours >= 1 for s in sources)
    two_sigma = next(s for s in sources if s.id == "two-sigma")
    assert two_sigma.url == "https://www.twosigma.com/topic/markets-economy/"
    assert two_sigma.paths == ["/articles/"]
    assert not next(s for s in sources if s.id == "research-affiliates").enabled
    assert "Reject unrelated infrastructure, cloud security, careers" in INSTRUCTIONS
    source = sources[0]
    receipt = collect(source, lambda url: ({"ok": True}, b"<!DOCTYPE a><feed></feed>"))
    assert not receipt["ok"]
    assert not source.allows("https://arxiv.org.evil.example/paper")


def test_research_link_title_excludes_embedded_style_text():
    raw = (b'<a href="/paper"><style>@keyframes shimmer { 0% { color: red; } }</style>'
           b'An Evidence-Based Research Paper</a>')
    _, content, links = parse_html(raw, {"url": "https://example.org/index", "content_type": "text/html"})
    assert links == [{"url": "https://example.org/paper", "title": "An Evidence-Based Research Paper"}]
    assert "@keyframes" not in content


def test_two_sigma_collects_only_titles_inside_article_listing_cards():
    source = next(s for s in load_sources() if s.id == "two-sigma")
    raw = (b'<nav><a href="/articles/company-news/">Company News</a></nav>'
           b'<a href="/articles/new-ceo/">New CEO Named at Two Sigma</a>'
           b'<article class="pb-sm"><h3><a href="/articles/office-hours/">Office Hours with a Leader</a></h3></article>'
           b'<article class="pb-sm"><a href="/articles/market-model/">image</a>'
           b'<h3 class="h3"><a href="/articles/market-model/">A Market Risk Model</a></h3></article>'
           b'<footer><a href="/articles/careers/">Careers</a></footer>')
    receipt = collect(source, lambda url: ({"ok": True, "url": url, "content_type": "text/html"}, raw))
    assert receipt["ok"]
    assert receipt["entries"] == [{"url": "https://www.twosigma.com/articles/market-model/",
                                   "title": "A Market Risk Model", "metadata": {}}]


def test_corrupt_pdf_falls_back_only_to_existing_html_evidence():
    source = QuantSource(id="a", publisher="a", kind="seed", url="https://example.org/", article_hosts=["example.org"])
    html = ('<html><meta name="citation_pdf_url" content="/paper.pdf"><p>' + TEXT + '</p></html>').encode()

    def retrieve(url, **kwargs):
        return {"ok": True, "url": url, "content_type": "text/html"}, html if url.endswith("/") else b"%PDF-bad"

    result = fetch_original(source.url, source, downloader=retrieve, pdf_parser=Mock(side_effect=ValueError("bad")))
    assert result["ok"] and result["fulltext_status"] == "html_requires_evidence_check"
    assert result["pdf_errors"] == ["pdf_extraction_failed"]


def test_nber_landing_uses_predictable_public_pdf_original():
    source = QuantSource(id="nber", publisher="NBER", kind="seed", url="https://www.nber.org/papers/w35766",
                         article_hosts=["www.nber.org"])
    calls = []

    def retrieve(url, **kwargs):
        calls.append(url)
        if url.endswith("/papers/w35766"):
            return ({"ok": True, "url": url, "content_type": "text/html",
                     "original_sha256": "a" * 64}, ("<html><p>" + TEXT + "</p></html>").encode())
        return ({"ok": True, "url": url, "content_type": "application/pdf",
                 "original_sha256": "b" * 64}, b"%PDF-public")

    parsed = {"pages": [{"location": "PDF p.1", "text": TEXT}], "truncated": False}
    result = fetch_original(source.url, source, downloader=retrieve, pdf_parser=Mock(return_value=parsed))
    expected = "https://www.nber.org/system/files/working_papers/w35766/w35766.pdf"
    assert calls == [source.url, expected]
    assert result["ok"] and result["url"] == expected


GOLD_CASES = [
    ("empirical-with-limitations", {}, True),
    ("promising-hypothesis", {"kind": "hypothesis", "maturity": "hypothesis"}, True),
    ("negative-replication", {"kind": "replication", "author_results": "저자는 재현 실패 보고"}, True),
    ("theory-no-backtest", {"kind": "theory", "costs_turnover": "해당 없음: 이론 연구"}, True),
    ("methodology", {"kind": "methodology"}, True),
    ("institutional-transparent", {"kind": "institutional", "commercial_bias": "자산운용사의 상업적 이해관계"}, True),
    ("institutional-without-data-or-method", {"kind": "institutional", "maturity": "institutional_research",
                                              "data_period": "해당 없음: 시장 데이터 없음",
                                              "validation": "해당 없음: 검증 방법 없음"}, False),
    ("code-is-not-reproduction", {"kind": "data_code", "maturity": "reproduction_resource"}, True),
    ("classic-why-now", {"vintage": "classic", "published_on": "1993", "why_read": "현재 팩터 검증의 기준점"}, True),
    ("paywall-hold", {"disposition": "hold", "reason": "공개 원문 없음"}, True),
    ("scanned-hold", {"disposition": "hold", "reason": "스캔 PDF 원문 불충분"}, True),
    ("advertising-reject", {"disposition": "reject", "reason": "상품 광고"}, True),
    ("stock-pick-reject", {"disposition": "reject", "reason": "종목 추천"}, True),
    ("missing-costs-disclosed", {"costs_turnover": "거래비용·회전율 미기재"}, True),
    ("invalid-date", {"published_on": "2026-99-12"}, False),
    ("future-date", {"published_on": "2099-01-01"}, False),
    ("missing-date", {"published_on": ""}, False),
    ("missing-authors", {"authors": []}, False),
    ("missing-validation", {"validation": ""}, False),
    ("missing-cost-field", {"costs_turnover": ""}, False),
    ("missing-limitations", {"limitations": []}, False),
    ("missing-evidence", {"evidence": []}, False),
    ("fabricated-quote", {"evidence": [{"claim": "x", "location": "PDF p.1", "quote": "Invented result"}] * 2}, False),
    ("wrong-page-uniquely-correctable", {"evidence": [{"claim": "x", "location": "PDF p.2", "quote": TEXT[:40]}] * 2}, True),
    ("invented-link", {"related_urls": ["https://example.org/made-up"]}, False),
    ("unsafe-link", {"related_urls": ["https://127.0.0.1/key"]}, False),
    ("correction-needs-change", {"change": "correction"}, False),
    ("material-update", {"change": "material", "change_summary": "검증 표본 변경"}, True),
    ("retraction", {"change": "retraction", "change_summary": "저자가 오류로 철회"}, True),
    ("cosmetic-not-new", {"change": "cosmetic"}, True),
    ("partial-original-date", {"published_on": "2026-09"}, True),
]


@pytest.mark.parametrize("name,updates,accepted", GOLD_CASES, ids=[c[0] for c in GOLD_CASES])
def test_thirty_golden_contract_cases(name, updates, accepted):
    # Synthetic proposals test deterministic gates, not model classification accuracy.
    bundle = {"pages": [{"location": "PDF p.1", "text": TEXT}], "as_of": "2026-09-22", "commercial": False, "links": [],
              "metadata": {"publisher": "Example", "url": "https://example.org/paper"}}
    ready = {"request": {"request_id": "quant-feed-gold"}}
    value = response(ready, brief(**copy.deepcopy(updates)))
    if accepted:
        assert validate(value, bundle, "review")
    else:
        with pytest.raises(ValueError):
            validate(value, bundle, "review")


@pytest.mark.parametrize("failed_check", ["claims_supported", "direct_quant_scope", "substantive_research"])
def test_critic_cannot_pass_unsupported_or_out_of_scope_research(failed_check):
    with pytest.raises(ValueError, match="quant_incomplete_critique"):
        EvidenceCritique.model_validate(critique(**{failed_check: False}))
    assert "General AI governance" in INSTRUCTIONS
    assert "direct_quant_scope and substantive_research separately" in prompt({}, "critique")


def test_editorial_suggestions_do_not_block_but_cannot_hide_material_errors():
    assert EditorialCritique.model_validate(critique(suggestions=["Optional shorter headline"])).disposition == "pass"
    with pytest.raises(ValueError, match="quant_incomplete_critique"):
        EditorialCritique.model_validate(critique(claims_supported=False, suggestions=["Invented return is a fact error"]))
    with pytest.raises(ValueError, match="quant_incomplete_critique"):
        EditorialCritique.model_validate(critique(issues=["Wrong confidence interval"], suggestions=[]))
    rules = prompt({}, "critique")
    assert "do not require a quotation proving absence" in rules
    assert "tradability or guarantees remain MATERIAL" in rules
    card = render(ResearchBrief.model_validate(brief(costs_turnover="비용 미기재")),
                  {"publisher": "Example", "url": "https://example.org/paper"})
    assert "미기재·확인 불가: 검토에 제공된 원문 텍스트·발췌 기준" in card


def test_arxiv_date_and_clipped_context_are_explicit_in_editor_prompt():
    bundle = {"metadata": {"publisher": "arXiv", "citation_date": ["2026/09/22"],
                           "citation_online_date": ["2026/09/22"], "published": "2026-09-24"}}
    instructions = prompt(bundle, "review")
    assert "DATE_GUARD: arXiv original citation date is 2026-09-22" in instructions
    assert "do not substitute feed published/updated" in instructions
    assert "제공 발췌에서 확인되지 않음" in instructions
    bundle["metadata"]["citation_online_date"] = ["2026/09/23"]
    assert "arXiv date metadata is incomplete or conflicting" in prompt(bundle, "review")


def test_editorial_validation_types_depend_on_design_not_authors_validation_word():
    # Prompt regression only; semantic model accuracy is checked separately on real originals.
    for stage in ("review", "revision", "critique"):
        rules = prompt({}, stage)
        assert "internal simulation diagnostics" in rules
        assert "do not erase an actual empirical comparison" in rules
        assert "authors' word 'validate' alone does not establish a market-data" in rules
    rules = prompt({}, "critique")
    assert "data or test design that contradicts the statement" in rules
    assert "adding another equivalent author assertion is optional" in rules


def test_confirmed_arxiv_original_date_is_checked_without_silent_rewriting():
    bundle = {"metadata": {"publisher": "arXiv", "url": "https://arxiv.org/abs/2609.12345",
                           "citation_date": ["2026/09/01"], "citation_online_date": ["2026/09/01"]},
              "pages": [{"location": "PDF p.1", "text": TEXT}], "as_of": "2026-09-29", "links": [],
              "commercial": False}
    value = response({"request": {"request_id": "quant-feed-date"}}, brief(published_on="2026-09-03"))
    with pytest.raises(ProposalValidationError) as failure:
        validate(value, bundle, "review")
    assert failure.value.issues == [{"code": "quant_original_publication_date_mismatch",
                                     "field": "published_on", "expected": "2026-09-01"}]
    assert failure.value.draft["published_on"] == "2026-09-03"


def test_editor_audits_uncertainty_and_entire_evidence_claim():
    review = prompt({}, "review")
    assert "uncertainty intervals and inconclusive results" in review
    assert "do not by themselves isolate dynamic or causal skill" in review
    assert "JOINTLY must support the WHOLE statement" in review
    assert "Never project a" in review
    assert "source statement and a scoped missing period" in review
    critique = prompt({}, "critique")
    assert "Audit EACH brief field and EACH evidence" in critique
    assert "Enumerate ALL material issues" in critique
    assert "quote the exact current sentence" in critique
    assert "After a revision, re-audit" in critique


def test_render_is_scan_friendly_and_does_not_duplicate_or_mislabel_links():
    value = brief(related_urls=["https://example.org/paper", "https://example.org/code"],
                  limitations=["거래비용 미반영", "표본 편향 가능성", "시장 충격 미기재"])
    rendered = render(ResearchBrief.model_validate(value),
                      {"publisher": "Example", "url": "https://example.org/paper"})
    assert "\n\n*핵심*\n" in rendered and "\n\n*왜 읽나*\n" in rendered
    assert "\n\n*연구 설계·결과*\n• *대상*" in rendered
    assert "\n• *데이터*" in rendered and "\n• *검증*" in rendered
    assert "\n• *저자 보고*" in rendered and "\n\n*주의점*\n" in rendered
    two_limits = render(ResearchBrief.model_validate(brief(limitations=["첫 번째", "두 번째"])),
                        {"publisher": "Example", "url": "https://example.org/paper"})
    assert "*주의점*\n• 첫 번째\n• 두 번째\n• *비용·회전율*" in two_limits
    assert "시장 충격 미기재" in rendered
    assert "*적용 전*" in rendered and "독립 재현·투자 검증 아님" in rendered
    assert rendered.count("<https://example.org/paper|") == 1
    assert "<https://example.org/code|추가 자료 1>" in rendered
    assert "관련 코드·데이터" not in rendered and "research_validity" not in rendered
    assert "Transaction costs" not in rendered  # Evidence quotes retained privately, not republished.


def test_slack_locations_preserve_page_coverage_without_repeating_extraction_chunks():
    from quant_company.quant_feed.editor import display_locations

    assert display_locations(["PDF p.19 [1/3]", "PDF p.1 [2/3]", "PDF p.2 [2/3]", "PDF p.2 [3/3]",
                              "PDF p.11 [2/2]", "PDF p.27 [3/3]", "PDF p.28 [2/3]"]) == "PDF p.1–2, 11, 19, 27–28"
    assert display_locations(["HTML section Results", "HTML section Results", "PDF p.8"]) == (
        "PDF p.8 · HTML section Results")
