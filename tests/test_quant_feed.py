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
from quant_company.quant_feed.contracts import EvidenceCritique, QuantSource, ResearchBrief, load_sources
from quant_company.quant_feed.editor import render, validate
from quant_company.quant_feed.feeds import aliases, collect
from quant_company.quant_feed.originals import download, extract_pdf, fetch_original
from quant_company.quant_feed.store import QuantFeedStore
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


def critique(**updates):
    value = dict(disposition="pass", reason="원문과 주장 일치", original_sufficient=True, claims_supported=True,
                 dates_authors_verified=True, limitations_honest=True, relevance_and_value=True,
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


def test_one_revision_then_hold(quant):
    original(quant)
    quant.commit(response(quant.prepare(), brief()))
    revision = critique(disposition="revise", claims_supported=False, issues=["claim overstatement"])
    quant.commit(response(quant.prepare(), revision))
    quant.commit(response(quant.prepare(), brief()))
    assert quant.commit(response(quant.prepare(), revision))["document_state"] == "held"
    assert quant.prepare()["state"] == "idle"


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


def test_quote_validation_ignores_only_pdf_layout_whitespace():
    ready = {"request": {"request_id": "quant-feed-review"}}
    bundle = {"pages": [{"location": "PDF p.1", "text": "a convex functionf on a B-bounded domain"}],
              "as_of": "2026-09-22T00:00:00+00:00", "links": [], "commercial": False, "prior": None}
    value = brief(evidence=[
        {"claim": "layout", "location": "PDF p.1", "quote": "convex function f on a B-bounded domain"},
        {"claim": "domain", "location": "PDF p.1", "quote": "B-bounded domain"},
    ])
    assert validate(response(ready, value), bundle, "review").disposition == "publish"
    value["evidence"][0]["quote"] = "convex function g on a B-bounded domain"
    with pytest.raises(ValueError, match="quant_quote_not_in_original_version"):
        validate(response(ready, value), bundle, "review")


def test_raw_json_control_characters_are_only_normalized_inside_strings():
    content = json.dumps(brief(), ensure_ascii=False).replace("검증 한계에 대한 연구적 가치", "검증 한계\n연구적 가치")
    value = ProviderResponse(request_id="quant-feed-review", provider="fixture",
                             decision=AgentDecision(status="complete", say="", artifacts=[
                                 ArtifactDraft(title="quant review", content=content)]))
    bundle = {"pages": [{"location": "PDF p.1", "text": TEXT}],
              "as_of": "2026-09-22T00:00:00+00:00", "links": [], "commercial": False, "prior": None}
    assert validate(value, bundle, "review").reason == "검증 한계 연구적 가치"


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
        document = conn.execute("SELECT state,stage,revision,critique FROM quant_feed_documents").fetchone()
    assert (document["state"], document["stage"], document["revision"]) == ("ready", "revision", 1)
    assert document["critique"]["issues"] == ["quant_quote_not_in_original_version"]
    second = quant.prepare()
    with pytest.raises(ValueError, match="quant_quote_not_in_original_version"):
        quant.commit(response(second, invalid))
    quant.fault(second["request"]["request_id"], "invalid_quant_proposal")
    assert quant.prepare()["state"] == "idle"


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
    source = sources[0]
    receipt = collect(source, lambda url: ({"ok": True}, b"<!DOCTYPE a><feed></feed>"))
    assert not receipt["ok"]
    assert not source.allows("https://arxiv.org.evil.example/paper")


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
    ("wrong-page", {"evidence": [{"claim": "x", "location": "PDF p.2", "quote": TEXT[:40]}] * 2}, False),
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
    bundle = {"pages": [{"location": "PDF p.1", "text": TEXT}], "as_of": "2026-09-22", "commercial": False, "links": []}
    ready = {"request": {"request_id": "quant-feed-gold"}}
    value = response(ready, brief(**copy.deepcopy(updates)))
    if accepted:
        assert validate(value, bundle, "review")
    else:
        with pytest.raises(ValueError):
            validate(value, bundle, "review")


def test_critic_cannot_pass_unsupported_claim_and_render_labels():
    with pytest.raises(ValueError):
        EvidenceCritique.model_validate(critique(claims_supported=False))
    rendered = render(ResearchBrief.model_validate(brief()), {"publisher": "Example", "url": "https://example.org/"})
    assert "저자 보고 결과" in rendered and "≠ 독립 재현" in rendered
    assert "Transaction costs" not in rendered  # Evidence quotes retained privately, not republished.
