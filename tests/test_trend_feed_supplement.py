import json
from datetime import UTC, datetime, timedelta

import pytest
from psycopg.types.json import Jsonb

from quant_company.company import as_json, fingerprint
from quant_company.contracts import AgentDecision, ArtifactDraft, ProviderRequest, ProviderResponse
from quant_company.news import store as news_store_module
from quant_company.news.store import NewsStore
from quant_company.trend_feed import schedule
from quant_company.trend_feed.editor import prompt, publication_items, render, validate_draft
from quant_company.trend_feed.runner import TrendFeedCollector

from .test_trend_feed import FixtureNaver, ingest, morning, response, trend  # noqa: F401


def accepted_news(trend, number=1):  # noqa: F811
    company = trend.company
    company.settings.company_news_enabled = company.settings.news_publish_enabled = True
    company.settings.news_channel_id, company.settings.news_owner_user = "CQUANT", "UHUMAN"
    company.roles["reporter"].active = True
    store = NewsStore(company)
    store.sync_sources()
    content = f"Example 보도에 따르면 신기술 {number}이 공개됐습니다. " * 10
    at = trend.clock[0]-timedelta(minutes=10)
    identity = f"original-{number}"
    with store.db.transaction() as conn:
        source = conn.execute("SELECT * FROM news_sources WHERE id='example'").fetchone()
        conn.execute("""INSERT INTO news_articles(id,source_id,url,title,summary,feed_digest,published_at,
            source_digest,state,content,retrieval,collected_at) VALUES(%s,'example',%s,%s,'','fixture',%s,%s,'ready',%s,%s,%s)""",
            (identity, f"https://example.org/news-{number}", f"신기술 {number} 발표", at,
             source["config_digest"], content, Jsonb({"ok": True, "retrieved_at": at.isoformat()}), at))
        # Seed a prepared fixture at the feed's clock; commit through the real publisher below.
        request = ProviderRequest(request_id=f"fixture-review-{number}", model="fixture-model", prompt="Synthetic news review")
        bundle = {"primary_ids": [identity], "events": [], "articles": [{"id": identity,
            "url": f"https://example.org/news-{number}", "title": f"신기술 {number} 발표", "content": content,
            "published_at": at, "publisher": "Example", "kind": "media", "origin_group": "example",
            "allow_attributed_reporting": True}]}
        conn.execute("INSERT INTO news_reviews(id,request,bundle,policy_digest) VALUES(%s,%s,%s,%s)",
                     (request.request_id, Jsonb(request.model_dump()), Jsonb(as_json(bundle)), store.policy()))
    item = {"disposition": "publish", "article_ids": [identity], "reason": "Original verified",
            "headline": f"Example: 신기술 {number} 공개", "facts": f"Example에 따르면 신기술 {number}이 공개됐습니다.",
            "significance": "기술 발전과 관련한 주요 소식입니다.", "category": "기술",
            "verification": "attributed_report", "evidence": [{"article_id": identity, "quote": content[:40]}]}
    reply = ProviderResponse(request_id=request.request_id, provider="fixture",
        decision=AgentDecision(status="complete", say="", artifacts=[ArtifactDraft(title="NewsReview",
                              content=json.dumps({"items": [item]}))]))
    class PublisherClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return trend.clock[0].astimezone(tz) if tz else trend.clock[0].replace(tzinfo=None)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(news_store_module, "datetime", PublisherClock)
        assert store.commit_review(reply)["items"][0]["state"] == "queued"
    with store.db.transaction() as conn:
        conn.execute("UPDATE news_reviews SET created_at=%s,completed_at=%s WHERE id=%s",
                     (trend.clock[0], trend.clock[0], request.request_id))
        conn.execute("""UPDATE messages SET created_at=%s WHERE id IN
            (SELECT id FROM news_publications WHERE review_id=%s)""", (trend.clock[0], request.request_id))
    return identity


async def test_accepted_news_fills_ten_without_naver_headline_queries(trend):  # noqa: F811
    ingest(trend, title="신기술 발표")
    for number in range(1, 12):
        accepted_news(trend, number)
    morning(trend)
    claim = trend.claim_enrichment()
    assert len(claim["bundle"]["candidates"]) == 12
    naver = FixtureNaver()
    await TrendFeedCollector(trend.company, naver=naver, reader=lambda *args: {"ok": False}).enrich(claim)
    bundle = trend.freeze()["bundle"]
    assert sum(kind == "trend" for kind, _ in naver.calls) == 1
    assert [payload["query"] for kind, payload in naver.calls if kind == "news"] == ["신기술 발표"]
    draft = validate_draft(response("fixture", bundle, background=True), bundle).model_dump()
    selected = publication_items(draft, bundle)
    assert len(selected) == 10
    text = render(bundle, draft)
    assert "검색 급상승 1개 · 주요 이슈 9개" in text and "10개 목표" in text
    supplement = text.split("*함께 볼 주요 이슈 · 순위 외*", 1)[1]
    cards = supplement.split("\n자료 마감", 1)[0].split("\n*• ")[1:]
    assert len(cards) == 9
    assert all("Google 규모" not in card and "네이버 최근" not in card for card in cards)
    assert "*2." not in text and "*1. 신기술 발표" in text
    # The producer's accepted IDs and evidence survive through enrichment and final outbox commitment.
    call = trend.prepare_call()
    trend.finish_call(call, response(call["id"], bundle, background=True))
    trend.clock[0] = schedule.times(trend.clock[0])[1].astimezone(UTC)
    assert trend.finalize()["items"] == 10
    with trend.db.transaction() as conn:
        delivered = conn.execute("SELECT text FROM messages WHERE author='trend_scout'").fetchone()
    assert delivered["text"] == text


@pytest.mark.parametrize("change", ["disabled", "policy", "unretrieved", "late", "old", "bad_quote", "not_accepted"])
def test_supplements_recheck_original_authority_and_cutoff(trend, change):  # noqa: F811
    identity = accepted_news(trend)
    morning(trend)
    with trend.db.transaction() as conn:
        if change == "disabled":
            conn.execute("UPDATE news_sources SET enabled=false")
        elif change == "policy":
            conn.execute("UPDATE news_sources SET config_digest='revoked'")
        elif change == "unretrieved":
            conn.execute("UPDATE news_articles SET retrieval='{}'")
        elif change == "late":
            conn.execute("UPDATE news_articles SET retrieval=%s", (Jsonb({"ok": True, "retrieved_at": trend.clock[0].isoformat()}),))
        elif change == "old":
            conn.execute("UPDATE news_articles SET published_at=%s", (trend.clock[0]-timedelta(days=2),))
        elif change == "bad_quote":
            conn.execute("UPDATE news_articles SET content=%s", ("원문이 바뀌어 검증된 인용이 없습니다. " * 10,))
        else:
            conn.execute("UPDATE news_reviews SET state='blocked'")
        assert trend.bundle(conn, trend.clock[0])["candidates"] == []
        assert conn.execute("SELECT state FROM news_articles WHERE id=%s", (identity,)).fetchone()["state"] == "queued"


def test_major_issue_events_and_shared_originals_do_not_duplicate_search_topics(trend):  # noqa: F811
    ingest(trend)
    accepted_news(trend, 1)
    accepted_news(trend, 2)
    morning(trend)
    bundle = trend.freeze()["bundle"]
    primary, supplemental, other = bundle["candidates"]
    primary["articles"] = supplemental["articles"]
    draft = validate_draft(response("fixture", bundle, background=True), bundle).model_dump()
    assert len(publication_items(draft, bundle)) == 2
    assert supplemental["title"] not in render(bundle, draft)
    other["event_id"] = supplemental["event_id"]
    primary["articles"] = []
    assert len(publication_items(draft, bundle)) == 2
    with trend.db.transaction() as conn:
        conn.execute("UPDATE news_events SET headline='마감 후 새 제목'")
    assert trend.freeze()["bundle"]["candidates"][1]["title"] != "마감 후 새 제목"


def test_expanded_google_pool_and_bounded_editor_input_keep_all_ids(trend):  # noqa: F811
    for number in range(40):
        ingest(trend, title=f"신기술 {number}", traffic=f"{number+1}K+")
    morning(trend)
    bundle = trend.freeze()["bundle"]
    assert len(bundle["candidates"]) == 30
    original = bundle["candidates"][0]
    bundle["candidates"] += [{**original, "id": fingerprint(["extra", n])[:24]} for n in range(20)]
    for candidate in bundle["candidates"]:
        candidate["articles"] = [{"id": candidate["id"], "url": "https://example.org/"+candidate["id"],
                                  "publisher": "Example", "content": "검증된 원문 자료. " * 300}]
    before = json.dumps(bundle)
    text = prompt(bundle)
    assert len(text) <= 80000
    assert json.dumps(bundle) == before
    assert all(candidate["id"] in text for candidate in bundle["candidates"])
    assert len(validate_draft(response("fixture", bundle), bundle).items) == 50


def test_supplement_owner_change_invalidates_frozen_delivery(trend):  # noqa: F811
    ingest(trend)
    morning(trend)
    trend.freeze()
    trend.company.settings.news_owner_user = "UOTHER"
    trend.clock[0] = schedule.times(trend.clock[0])[1].astimezone(UTC)
    assert trend.finalize()["state"] == "stale"
    with trend.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0
