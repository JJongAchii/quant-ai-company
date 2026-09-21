import json
from datetime import UTC, datetime, timedelta

import pytest

from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.news.discovery import NewsDiscoveryStore
from quant_company.news.feeds import canonical_url, parse_feed
from quant_company.news.originals import ArticlePage, fetch_original

from .test_news import CONTENT, feed, news, ready_article, reply, source  # noqa: F401


def register(store, specs):
    store.company.settings.news_sources_file.write_text(json.dumps([s.model_dump() for s in specs]))
    store.sync_sources()


@pytest.mark.parametrize("claim", ["reported_fact", "allegation", "anonymous_claim", "casualty", "forecast", "opinion"])
def test_single_outlet_publication_labels_reporting_and_rejects_contested_claims(news, claim):  # noqa: F811
    spec = source(kind="media", allow_attributed_reporting=True)
    register(news, [spec])
    ready_article(news, spec)
    prepared = news.prepare_review()
    response = reply(prepared["request"], verification="attributed_report", claim_type=claim)
    if claim != "reported_fact":
        with pytest.raises(ValueError, match="news_attributed_reporting_not_allowed"):
            news.commit_review(response)
    else:
        assert news.commit_review(response)["items"][0]["state"] == "queued"
        with news.db.transaction() as conn:
            message = conn.execute("SELECT text FROM messages").fetchone()["text"]
        assert "보도 · 주요" in message and "official 보도에 따르면:" in message


@pytest.mark.parametrize("kind,opt_in", [("media", False), ("official", True)])
def test_attributed_reporting_requires_registered_media_permission(news, kind, opt_in):  # noqa: F811
    spec = source(kind=kind, allow_attributed_reporting=opt_in)
    register(news, [spec])
    ready_article(news, spec)
    with pytest.raises(ValueError, match="news_attributed_reporting_not_allowed"):
        news.commit_review(reply(news.prepare_review()["request"], verification="attributed_report"))


def test_same_media_article_in_two_category_feeds_is_one_candidate(news):  # noqa: F811
    first = source(kind="media", origin_group="shared-publisher")
    second = first.model_copy(update={"id": "second-feed"})
    register(news, [first, second])
    entries, _ = parse_feed(feed(first), first)
    for _ in range(2):
        news.save_feed(news.claim_source(), {"ok": True, "entries": entries})
    with news.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM news_articles").fetchone()["n"] == 1
    assert canonical_url("https://www.bbc.co.uk/news/articles/abc?at_campaign=rss&at_medium=RSS") == "https://www.bbc.co.uk/news/articles/abc"


def test_sbs_feed_and_search_links_have_one_article_identity(news):  # noqa: F811
    spec = source(kind="media").model_copy(update={"article_hosts": ["news.sbs.co.kr"]})
    register(news, [spec])
    plain = "https://news.sbs.co.kr/news/endPage.do?news_id=N1001234567"
    rss = plain + "&cooper=RSSREADER&plink=RSSLINK"
    raw = feed(spec).replace(b"https://official.example.org/policy?utm_source=rss", rss.replace("&", "&amp;").encode())
    entries, _ = parse_feed(raw, spec)
    assert entries[0]["url"] == canonical_url(plain) == plain
    assert canonical_url(plain.replace("endPage.do", "endPagePrintPopup.do")) == plain
    assert canonical_url(plain.replace("N1001234567", "N1007654321")) != plain
    assert canonical_url("https://other.example.org/?cooper=functional&plink=keep") == (
        "https://other.example.org/?cooper=functional&plink=keep")
    claimed = news.claim_source()
    news.save_feed(claimed, {"ok": True, "entries": entries})
    plain_entries, _ = parse_feed(raw.replace(rss.replace("&", "&amp;").encode(), plain.encode()), spec)
    news.save_feed(claimed, {"ok": True, "entries": plain_entries})
    print_entries, _ = parse_feed(raw.replace(b"endPage.do", b"endPagePrintPopup.do"), spec)
    news.save_feed(claimed, {"ok": True, "entries": print_entries})
    with news.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM news_articles").fetchone()["n"] == 1


def test_primary_batch_includes_other_outlets_despite_first_source_flood(news):  # noqa: F811
    specs = [source("media-one", kind="media"), source("media-two", kind="media"), source("media-three", kind="media")]
    register(news, specs)
    for i in range(8):
        ready_article(news, specs[0], suffix=f"story-{i}")
    for spec in specs[1:]:
        ready_article(news, spec)
    bundle = json.loads(news.prepare_review()["request"]["prompt"].split("NEWS DATA JSON:\n")[1])
    assert {a["origin_group"] for a in bundle["articles"]} == {s.origin_group for s in specs}
    assert len(bundle["primary_ids"]) == 6


def test_kbs_article_region_excludes_promotion_and_hidden_content():
    page = ArticlePage("world.kbs.co.kr")
    page.feed(f"<article>Sidebar promotion {CONTENT * 3}</article><div class='body_txt'><p>{CONTENT}</p>"
              f"<div style='display: none'>hidden {CONTENT}</div></div>")
    assert page.article() == (CONTENT.strip(), "kbs_body_txt")
    missing = ArticlePage("world.kbs.co.kr")
    missing.feed(f"<article>Sidebar promotion {CONTENT}</article>")
    assert missing.article() == ("", "missing")


@pytest.mark.parametrize("free", [True, False])
def test_original_jsonld_publication_byline_and_paywall(monkeypatch, free):
    metadata = {"@graph": [{"@type": "NewsArticle", "datePublished": "2026-09-20T16:00:00Z",
                            "author": [{"@type": "Person", "name": "Example Reporter"}], "isAccessibleForFree": free}]}
    raw = (f'<script type="application/ld+json">{json.dumps(metadata)}</script><main>{CONTENT}</main>').encode()
    monkeypatch.setattr("quant_company.news.originals.fetch", lambda _: (
        {"ok": True, "content": CONTENT, "content_type": "text/html", "publisher_host": "www.cnbc.com"}, raw))
    receipt, _ = fetch_original("https://www.cnbc.com/example")
    if free:
        assert receipt["ok"] and receipt["published_at"] == metadata["@graph"][0]["datePublished"]
        assert receipt["bylines"] == ["Example Reporter"]
    else:
        assert not receipt["ok"] and receipt["content"] == "" and receipt["error"] == "article_requires_subscription"


@pytest.fixture
def discovery(news):  # noqa: F811
    news.company.settings.news_search_enabled = True
    register(news, [source(kind="media", topics=["economy_finance"])])
    return NewsDiscoveryStore(news.company)


def search_reply(prepared, urls=None, native=True):
    urls = urls or ["https://official.example.org/discovered", "https://unregistered.example.org/article"]
    return ProviderResponse(request_id=prepared["request"]["request_id"], provider="fixture", decision=AgentDecision(
        say="Candidates only", status="complete", artifacts=[{"title": "Candidates", "source_ids": [],
          "content": json.dumps({"results": [{"url": u, "title": "Candidate report", "snippet": "Not evidence"} for u in urls]})}]),
        web_searches=[{"id": "search-fixture", "action": {"type": "search"}}] if native else [])


def test_search_is_frozen_bounded_and_candidates_require_dated_original(discovery):
    prepared = discovery.prepare()
    assert prepared == discovery.prepare() and prepared["request"]["web_search"]
    response = search_reply(prepared)
    assert discovery.commit(response) == {"state": "completed", "added": 1, "excluded_unregistered": 1}
    assert discovery.commit(response)["duplicate"]
    assert discovery.prepare() == {"state": "idle"}
    with discovery.db.transaction() as conn:
        assert conn.execute("SELECT reserved FROM daily_usage").fetchone()["reserved"] == 1
        article = conn.execute("SELECT * FROM news_articles").fetchone()
        assert article["summary"] == "" and article["content"] is None and article["published_at"] is None
        assert conn.execute("SELECT last_success FROM news_sources").fetchone()["last_success"] is None
    assert discovery.prepare_review() == {"state": "idle"}
    claimed = discovery.claim_article()
    discovery.save_original(claimed, {"ok": True, "content": CONTENT, "publisher_host": "official.example.org"})
    with discovery.db.transaction() as conn:
        article = conn.execute("SELECT * FROM news_articles").fetchone()
        assert article["state"] == "fetch_failed" and article["error"] == "publication_time_unconfirmed_or_old"
        assert article["retrieval"]["discovery_request_id"] == prepared["request"]["request_id"]


def test_search_native_execution_policy_and_ambiguous_receipts(discovery):
    prepared = discovery.prepare()
    assert discovery.commit(search_reply(prepared, native=False))["state"] == "blocked"
    assert discovery.prepare()["state"] == "blocked"
    with discovery.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM news_articles").fetchone()["n"] == 0
        assert conn.execute("SELECT reserved FROM daily_usage").fetchone()["reserved"] == 1


def test_search_quota_reuses_id_and_uncertain_never_spends_again(discovery):
    prepared = discovery.prepare()
    identity = prepared["request"]["request_id"]
    discovery.search_fault(identity, "quota", 3600)
    assert discovery.prepare()["state"] == "defer"
    with discovery.db.transaction() as conn:
        conn.execute("UPDATE news_searches SET next_at=now()")
    assert discovery.prepare() == prepared
    discovery.search_fault(identity, "uncertain")
    discovery.company.settings.news_publish_enabled = False
    assert discovery.prepare()["state"] == "blocked"
    with discovery.db.transaction() as conn:
        assert conn.execute("SELECT reserved FROM daily_usage").fetchone()["reserved"] == 1


def test_search_fresh_original_becomes_editorial_candidate_and_scope_rotates(discovery):
    first = discovery.prepare()
    discovery.commit(search_reply(first))
    article = discovery.claim_article()
    discovery.save_original(article, {"ok": True, "content": CONTENT, "publisher_host": "official.example.org",
                                      "published_at": (datetime.now(UTC)-timedelta(hours=1)).isoformat()})
    with discovery.db.transaction() as conn:
        assert conn.execute("SELECT state FROM news_articles").fetchone()["state"] == "ready"
        conn.execute("UPDATE news_searches SET completed_at=now()-interval '31 minutes'")
    next_search = discovery.prepare()
    assert next_search["request"]["request_id"] != first["request"]["request_id"]
    assert "inflation interest rates" in next_search["request"]["prompt"]
    status = discovery.status()
    assert status["sources"][0]["last_success"] is None and status["sources"][0]["fresh_count"] == 1
    assert status["deliveries"] == [] and len(status["searches"]) == 2
