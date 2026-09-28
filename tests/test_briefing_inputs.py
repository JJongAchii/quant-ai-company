"""Synthetic public responses and the real PostgreSQL news-to-briefing boundary."""

import json
from datetime import date, timedelta

from psycopg.types.json import Jsonb

from quant_company.briefing.inputs import collect
from quant_company.briefing.schedule import editions
from quant_company.news.contracts import NewsSource
from quant_company.news.store import NewsStore


def setup_source(company, tmp_path):
    spec = NewsSource(id="cnbc-fixture", publisher="Synthetic publisher", origin_group="fixture", kind="media",
        feed_url="https://fixture.example.org/feed", article_hosts=["fixture.example.org"],
        usage_note="Synthetic tests only", enabled=True, use_for_summary=True)
    company.settings.news_sources_file = tmp_path / "sources.json"
    company.settings.news_sources_file.write_text(json.dumps([spec.model_dump()]))
    NewsStore(company).sync_sources()
    edition = editions(date(2026, 9, 22), "CQUANT", "UHUMAN")[0]
    return spec, edition


def original(url, edition, **changes):
    return {"ok": True, "url": url, "content": "Synthetic Nasdaq closing market report "*8,
            "original_sha256": "a"*64, "retrieved_at": (edition.cutoff-timedelta(minutes=1)).isoformat(),
            "published_at": (edition.cutoff-timedelta(hours=1)).isoformat(), **changes}


def no_calendar(url):
    return {"ok": False, "url": url, "error": "synthetic_unavailable"}, None


def test_collector_rejects_late_undated_and_redirected_originals(company, tmp_path):
    spec, edition = setup_source(company, tmp_path)
    requests = []

    def fetch_article(url):
        requests.append(url)
        receipt = original(url, edition)
        if url.endswith("late"):
            receipt["retrieved_at"] = (edition.cutoff+timedelta(seconds=1)).isoformat()
        if url.endswith("redirect"):
            receipt["url"] = "https://unregistered.example.org/report"
        if url.endswith("undated"):
            receipt["published_at"] = None
        if url.endswith("truncated"):
            receipt["content_truncated"] = True
        return receipt, None

    candidates = [{"url": "https://fixture.example.org/"+suffix, "title": "Nasdaq market close",
                   "snippet": "Search snippets are never evidence"}
                  for suffix in ("valid", "late", "redirect", "undated", "truncated")]
    candidates.append({"url": "https://unregistered.example.org/report", "title": "Market close"})
    result = collect(company, edition, candidates=candidates, at=edition.cutoff-timedelta(minutes=1),
                     article_fetch=fetch_article, feed_fetch=lambda _: {"ok": True, "entries": []},
                     page_fetch=no_calendar)
    assert [d["url"] for d in result["documents"]] == ["https://fixture.example.org/valid"]
    assert len(requests) == 5
    assert all("snippets" not in d["content"] for d in result["documents"])
    assert any(e["error"] == "redirect_not_registered" for e in result["collection_errors"])


def test_news_reuse_selects_latest_eligible_original_and_current_source_policy(company, tmp_path):
    spec, edition = setup_source(company, tmp_path)
    url = "https://fixture.example.org/close"
    with company.db.transaction() as conn:
        digest = conn.execute("SELECT config_digest FROM news_sources WHERE id=%s", (spec.id,)).fetchone()["config_digest"]
        for identity, minutes, policy in (("old", 5, digest), ("latest", 2, digest),
                                          ("after-cutoff", -1, digest), ("revoked", 1, "obsolete")):
            receipt = original(url, edition, original_sha256=identity,
                               retrieved_at=(edition.cutoff-timedelta(minutes=minutes)).isoformat())
            conn.execute("""INSERT INTO news_articles(id,source_id,url,title,summary,feed_digest,
                published_at,collected_at,source_digest,state,content,retrieval)
                VALUES(%s,%s,%s,'Nasdaq market close','',%s,%s,%s,%s,'ready',%s,%s)""",
                         (identity, spec.id, url, identity, edition.cutoff-timedelta(hours=1),
                          edition.cutoff-timedelta(minutes=minutes), policy, receipt["content"], Jsonb(receipt)))

    def unexpected_fetch(url):
        raise AssertionError("A cached original must not be fetched again")

    result = collect(company, edition, at=edition.cutoff, page_fetch=no_calendar,
                     article_fetch=unexpected_fetch, feed_fetch=lambda _: {"ok": True, "entries": []})
    assert len(result["documents"]) == 1
    assert result["documents"][0]["sha256"] == "latest"
