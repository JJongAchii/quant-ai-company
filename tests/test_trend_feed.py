import asyncio
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from html import escape
from pathlib import Path

import httpx
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from quant_company.cli import manifests
from quant_company.company import Company, load_roles
from quant_company.config import Settings
from quant_company.contracts import AgentDecision, ArtifactDraft, ProviderFault, ProviderResponse, Role
from quant_company.news.contracts import NewsSource
from quant_company.slack import SlackIngress, SlackOutbox
from quant_company.trend_feed import schedule
from quant_company.trend_feed.editor import render, validate_draft
from quant_company.trend_feed.runner import TrendFeedCollector, TrendFeedEditor
from quant_company.trend_feed.sources import (
    NaverClient,
    parse_rss,
    traffic_floor,
    trend_payload,
    trend_summary,
)
from quant_company.trend_feed.store import TrendFeedStore


def rss(at, title="신기술 발표", traffic="10,000+", url="https://example.org/story"):
    return (f'<rss xmlns:ht="https://trends.google.com/trending/rss"><channel><item>'
            f'<title>{escape(title)}</title><pubDate>{format_datetime(at)}</pubDate>'
            f'<ht:approx_traffic>{traffic}</ht:approx_traffic><ht:news_item>'
            f'<ht:news_item_title>신기술 공개</ht:news_item_title><ht:news_item_url>{url}</ht:news_item_url>'
            '<ht:news_item_source>Example</ht:news_item_source></ht:news_item></item></channel></rss>').encode()


@pytest.fixture
def trend(company, tmp_path, monkeypatch):
    s = company.settings
    s.trend_feed_enabled = s.trend_feed_publish_enabled = True
    s.trend_feed_channel_id, s.trend_feed_owner_user = "CTRENDS", "UHUMAN"
    s.slack_allowed_channels.append("CTRENDS")
    s.news_sources_file = tmp_path / "news-sources.json"
    source = NewsSource(id="example", publisher="Example", origin_group="example", kind="media",
                        feed_url="https://example.org/rss", article_hosts=["example.org"], enabled=True,
                        use_for_summary=True, allow_attributed_reporting=True, usage_note="Synthetic fixture")
    s.news_sources_file.write_text(json.dumps([source.model_dump()]))
    company.roles["trend_scout"] = Role(id="trend_scout", name="Trend Scout", mission="Test", model="unused",
                                        instructions="Outbound only", tools=[], can_delegate_to=[])
    company.roles["reporter"] = Role(id="reporter", name="Reporter", mission="Test", model="fixture-model",
                                     reasoning_effort="low", instructions="Fixture", tools=[], can_delegate_to=[])
    store = TrendFeedStore(company)
    store.clock = [datetime(2026, 10, 6, 22, 20, tzinfo=UTC)]  # 10/07 07:20 KST
    monkeypatch.setattr(schedule, "utcnow", lambda: store.clock[0])
    return store


def ingest(store, *, title="신기술 발표", traffic="10,000+", receipt=None):
    with store.db.transaction() as conn:
        conn.execute("UPDATE trend_feed_source SET next_at=%s", (store.clock[0],))
    claimed = store.claim_source()
    if receipt is None:
        raw = rss(store.clock[0], title, traffic)
        entries, _ = parse_rss(raw)
        receipt = {"ok": True, "entries": entries, "raw_xml": raw.decode()}
    return store.save_snapshot(claimed, receipt)


def morning(store):
    store.clock[0] = schedule.times(store.clock[0])[0].astimezone(UTC)


def outgoing(store):
    with store.db.transaction() as conn:
        return conn.execute("SELECT * FROM outbox ORDER BY created_at,id").fetchall()


def force_due(store):
    with store.db.transaction() as conn:
        conn.execute("UPDATE outbox SET next_at=now()-interval '1 second'")


def response(identity, bundle, *, background=False):
    items = []
    for c in bundle["candidates"]:
        article = c["articles"][0] if c["articles"] else None
        items.append({"member_ids": [c["id"]], "category": "기술",
                      "background": "Example 보도에 따르면 신기술이 공개됐습니다." if background and article else "",
                      "evidence": [{"article_id": article["id"], "quote": article["content"][:30]}]
                      if background and article else []})
    return ProviderResponse(request_id=identity, decision=AgentDecision(status="complete", say="",
                            artifacts=[ArtifactDraft(title="TrendBriefDraft", content=json.dumps({"items": items}))]))


class FixtureNaver:
    def __init__(self):
        self.calls = []

    def credentials(self):
        return {"synthetic": "fixture"}

    def request(self, kind, payload):
        self.calls.append((kind, payload))
        if kind == "news":
            return {"ok": True, "data": {"items": []}}
        end = datetime.fromisoformat(payload["endDate"]).date()
        return {"ok": True, "data": {"results": [
            {"title": g["groupName"], "keywords": g["keywords"], "data": [
                {"period": str(end - timedelta(days=n)), "ratio": 80 if n < 7 else 40} for n in range(28)]}
            for g in payload["keywordGroups"]]}}


def test_google_parser_unicode_approximate_units_and_unsafe_inputs():
    at = datetime.now(UTC)
    entries, skipped = parse_rss(rss(at, title="ＮＢＡ  결승", traffic="1.5K+"))
    assert not skipped and entries[0]["keyword"] == "nba 결승"
    assert entries[0]["traffic_floor"] == 1500 and entries[0]["traffic"] == "1.5K+"
    assert entries[0]["published_at"] == at.replace(microsecond=0)
    assert traffic_floor("1만+") == 10000 and traffic_floor("unknown") is None
    assert traffic_floor("1..2+") is None
    assert parse_rss(rss(at).replace(b"<pubDate>", b"<unknown>").replace(b"</pubDate>", b"</unknown>")) == ([], 1)
    for raw in (b'<!DOCTYPE rss [<!ENTITY x "abc">]><rss/>', b'<html/>', b'x' * (1024 * 1024 + 1)):
        with pytest.raises(ValueError):
            parse_rss(raw)


def test_naver_within_response_comparison_and_missing_zero_data():
    cutoff = datetime(2026, 10, 7, 7, 30, tzinfo=schedule.KST)
    payload = trend_payload([{"id": "one", "title": "야구"}], cutoff)
    result = FixtureNaver().request("trend", payload)["data"]["results"][0]
    assert trend_summary(result, payload)["change_percent"] == 100
    scaled = {**result, "data": [{**p, "ratio": p["ratio"] / 2} for p in result["data"]]}
    assert trend_summary(scaled, payload)["change_percent"] == 100
    missing = {**result, "data": result["data"][1:]}
    assert trend_summary(missing, payload)["as_of"] == "2026-10-05"
    gap = {**result, "data": result["data"][:6] + result["data"][7:]}
    assert trend_summary(gap, payload)["reason"] == "incomplete_comparison"
    zero = {**result, "data": [{**p, "ratio": 0} for p in result["data"]]}
    assert trend_summary(zero, payload)["reason"] == "zero_baseline"
    bad = {**result, "data": [{"period": "2026-10-06", "ratio": float("nan")}]}
    assert trend_summary(bad, payload)["reason"] == "invalid_series"


def test_naver_uses_hub_paths_redacts_keys_and_refuses_redirects(tmp_path):
    credentials = tmp_path / "naver.json"
    credentials.write_text(json.dumps({"client_id": "synthetic-id", "client_secret": "synthetic-secret"}))
    settings = Settings(trend_feed_naver_enabled=True, trend_feed_naver_credentials_file=credentials)
    seen = []

    def handle(request):
        seen.append(request)
        assert request.headers["X-NCP-APIGW-API-KEY"] == "synthetic-secret"
        return httpx.Response(200, json={"items": []})

    client = NaverClient(settings, httpx.MockTransport(handle))
    receipt = client.request("news", {"query": "야구"})
    assert seen[0].url.path == "/search/v1/news" and receipt["ok"]
    assert "synthetic" not in json.dumps(receipt)
    client = NaverClient(settings, httpx.MockTransport(lambda _: httpx.Response(302, headers={"Location": "https://evil.org/"})))
    assert client.request("news", {})["http_status"] == 302
    settings.trend_feed_naver_enabled = False
    assert client.request("news", {})["error"] == "naver_not_configured"


def test_cutoff_uses_observations_not_sum_and_first_day_is_partial(trend):
    ingest(trend)
    trend.clock[0] += timedelta(minutes=5)
    ingest(trend, traffic="20,000+")
    morning(trend)
    row = trend.freeze()
    c = row["bundle"]["candidates"][0]
    assert c["traffic_floor"] == 20000 and row["bundle"]["partial_history"]
    ingest(trend, title="마감 이후", traffic="1M+")
    assert trend.freeze()["bundle"] == row["bundle"]
    draft = validate_draft(response("fixture", row["bundle"]), row["bundle"]).model_dump()
    text = render(row["bundle"], draft)
    assert "20,000+" in text and "관측 이력 부족" in text
    assert "주제 분류를 확인하지 못해" in trend.preview()["text"]


def test_304_keeps_observation_and_restart_retry_is_idempotent(trend):
    ingest(trend)
    trend.clock[0] += timedelta(minutes=1)
    assert ingest(trend, receipt={"ok": True, "not_modified": True})["observations"] == 1
    trend.clock[0] += timedelta(minutes=1)
    with trend.db.transaction() as conn:
        conn.execute("UPDATE trend_feed_source SET next_at=%s", (trend.clock[0],))
    claim = trend.claim_source()
    assert trend.claim_source() is None
    receipt = {"ok": True, "entries": parse_rss(rss(trend.clock[0]))[0]}
    assert trend.save_snapshot(claim, receipt)["state"] == "collected"
    assert TrendFeedStore(trend.company).save_snapshot(claim, receipt)["state"] == "stale"


def test_internal_collection_gap_is_visible_even_after_recovery(trend):
    trend.clock[0] -= timedelta(hours=4)
    ingest(trend)
    trend.clock[0] += timedelta(hours=4)
    ingest(trend)
    morning(trend)
    assert trend.freeze()["bundle"]["collection_gap"]
    assert "30분을 넘는 수집 공백" in trend.preview()["text"]


def test_message_limit_preserves_complete_cards_and_coverage_footer(trend):
    ingest(trend)
    morning(trend)
    bundle = trend.freeze()["bundle"]
    candidate = bundle["candidates"][0]
    candidate["articles"] = [{"id": "original", "url": "https://example.org/" + "x" * 12000,
                              "publisher": "Example", "content": "원문으로 확인한 신기술 소식"}]
    draft = {"items": [{"member_ids": [candidate["id"]], "category": "기술",
                        "background": "Example에 따르면 신기술 소식이 전해졌습니다.",
                        "evidence": [{"article_id": "original", "quote": "신기술 소식"}]}]}
    text = render(bundle, draft)
    assert len(text) < 12000 and "길이 제한" in text and "자료 마감" in text


@pytest.mark.parametrize("title,search_term", [("신기술 발표", "신기술 발표"),
                                             ("<@here> 기술 & 소비|문화", "< @here> 기술 & 소비|문화")])
def test_unverified_news_links_do_not_imply_a_related_article(trend, title, search_term):
    from urllib.parse import parse_qs, urlparse

    ingest(trend, title=title)
    morning(trend)
    bundle = trend.freeze()["bundle"]
    bundle["candidates"][0]["news"] = [{"url": "https://example.org/unrelated",
                                       "title": "한화 선수의 멀티이닝 등판", "publisher": "Example"}]
    draft = validate_draft(response("fixture", bundle), bundle).model_dump()
    text = render(bundle, draft)
    assert "한화 선수의 멀티이닝 등판" not in text and "example.org/unrelated" not in text
    assert "검증된 관련 원문 없음" in text and "뉴스 검색>" in text
    link = next(line.split("|", 1)[0][1:] for line in text.splitlines() if line.startswith("<https://search.naver.com/"))
    assert parse_qs(urlparse(link).query) == {"where": ["news"], "query": [search_term]}
    assert "<@here>" not in text


def test_sports_are_filtered_before_ten_topic_limit_and_keep_frozen_order(trend):
    ingest(trend)
    morning(trend)
    bundle = trend.freeze()["bundle"]
    original = bundle["candidates"][0]
    sports = ["한국 대 우즈베키스탄", "삼성 대 kia", "ssg 대 한화", "두산 대 롯데",
              "카를로스 알카라스", "선수 영입", "대표팀 명단", "리그 경기 결과", "테니스 기록"]
    titles = sports + [f"신기술 발표 {i}" for i in range(1, 12)]
    bundle["candidates"] = [{**original, "id": str(i), "title": title} for i, title in enumerate(titles)]
    result = response("fixture", bundle)
    draft = json.loads(result.decision.artifacts[0].content)
    for item in draft["items"][:len(sports)]:
        item["category"] = "스포츠"
    result.decision.artifacts[0].content = json.dumps(draft)
    classified = validate_draft(result, bundle).model_dump()
    assert len(classified["items"]) == 20  # All candidates remain accounted for in the typed proposal.
    text = render(bundle, classified)
    for title in sports:
        assert title not in text
    for i in range(1, 11):
        assert f"*{i}. 신기술 발표 {i} · 기술*" in text
    assert "신기술 발표 11" not in text and "스포츠 주제 제외" in text
    draft["items"] = draft["items"][len(sports):]
    result.decision.artifacts[0].content = json.dumps(draft)
    with pytest.raises(ValueError, match="covered_once"):
        validate_draft(result, bundle)


def test_all_sports_candidates_produce_an_honest_empty_brief(trend):
    ingest(trend, title="카를로스 알카라스")
    morning(trend)
    bundle = trend.freeze()["bundle"]
    draft = validate_draft(response("fixture", bundle), bundle).model_dump()
    draft["items"][0]["category"] = "스포츠"
    text = render(bundle, draft)
    assert "모두 스포츠로 분류" in text and "자료 마감" in text
    assert "카를로스 알카라스" not in text and "뉴스 검색>" not in text
    assert "유효한 관측이 없어" not in text


def test_unclassified_fallback_withholds_topics_without_requiring_sports_title_tokens(trend):
    ingest(trend, title="카를로스 알카라스")
    ingest(trend, title="유해진")
    morning(trend)
    trend.freeze()
    trend.clock[0] = schedule.times(trend.clock[0])[1].astimezone(UTC)
    assert trend.finalize()["items"] == 0
    text = trend.preview()["text"]
    assert "주제 분류를 확인하지 못해" in text and "자료 마감" in text
    assert "카를로스 알카라스" not in text and "유해진" not in text
    assert "뉴스 검색>" not in text and "관련 배경:" not in text
    assert len(outgoing(trend)) == 1 and outgoing(trend)[0]["status"] == "pending"


def test_editorial_policy_change_invalidates_pending_sports_brief(trend, monkeypatch):
    from quant_company.trend_feed import store as store_module

    current = store_module.EDITORIAL_POLICY_VERSION
    monkeypatch.setattr(store_module, "EDITORIAL_POLICY_VERSION", current - 1)
    ingest(trend)
    morning(trend)
    trend.freeze()
    trend.clock[0] = schedule.times(trend.clock[0])[1].astimezone(UTC)
    trend.finalize()
    monkeypatch.setattr(store_module, "EDITORIAL_POLICY_VERSION", current)
    with trend.db.transaction() as conn:
        message = {**outgoing(trend)[0], "agent": "trend_scout"}
        assert not trend.gate(conn, message)
    assert outgoing(trend)[0]["status"] == "stale"


def test_classified_non_sports_are_the_only_topics_committed_to_outbox(trend):
    ingest(trend, title="카를로스 알카라스")
    ingest(trend, title="신기술 발표")
    morning(trend)
    claimed = trend.claim_enrichment()
    trend.save_enrichment(claimed, claimed["bundle"])
    call = trend.prepare_call()
    result = response(call["id"], claimed["bundle"])
    draft = json.loads(result.decision.artifacts[0].content)
    candidates = {c["id"]: c for c in claimed["bundle"]["candidates"]}
    for item in draft["items"]:
        if candidates[item["member_ids"][0]]["title"] == "카를로스 알카라스":
            item["category"] = "스포츠"
    result.decision.artifacts[0].content = json.dumps(draft)
    trend.finish_call(call, result)
    trend.clock[0] = schedule.times(trend.clock[0])[1].astimezone(UTC)
    assert trend.finalize()["items"] == 1
    with trend.db.transaction() as conn:
        text = conn.execute("SELECT text FROM messages WHERE id=%s", (outgoing(trend)[0]["id"],)).fetchone()["text"]
    assert "*1. 신기술 발표 · 기술*" in text and "카를로스 알카라스" not in text
    assert trend.preview()["text"] == text


async def test_cancelled_model_call_is_uncertain_and_not_replayed(trend):
    ingest(trend)
    morning(trend)
    claimed = trend.claim_enrichment()
    trend.save_enrichment(claimed, claimed["bundle"])
    entered, cancelled = asyncio.Event(), asyncio.Event()

    class Provider:
        async def run(self, request):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    task = asyncio.create_task(TrendFeedEditor(trend.company, Provider()).tick())
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set() and trend.prepare_call() is None
    assert trend.status()["model_calls"][0]["error"] == "uncertain"


async def test_full_producer_consumer_and_single_outbox(trend):
    ingest(trend)
    morning(trend)
    naver = FixtureNaver()

    def reader(link, settings):
        return {"ok": True, "content": "한국 기업의 신기술이 공식 발표됐습니다. " * 10,
                "publisher": "Example", "published_at": trend.clock[0].isoformat()}

    collector = TrendFeedCollector(trend.company, naver=naver, reader=reader)
    claimed = trend.claim_enrichment()
    await collector.enrich(claimed)
    bundle = trend.freeze()["bundle"]
    assert bundle["candidates"][0]["naver"]["change_percent"] == 100
    assert len(bundle["candidates"][0]["articles"]) == 1

    class Provider:
        async def run(self, request):
            assert request.request_id.startswith("news-trends-")
            assert request.model == "fixture-model" and request.reasoning_effort == "low"
            assert not request.web_search and "X-NCP" not in request.prompt
            return response(request.request_id, bundle, background=True)

    editor = TrendFeedEditor(trend.company, Provider())
    await asyncio.gather(editor.tick(), editor.tick())
    assert len(trend.status()["model_calls"]) == 1
    trend.clock[0] = schedule.times(trend.clock[0])[1].astimezone(UTC)
    assert trend.finalize()["state"] == "queued"
    assert TrendFeedStore(trend.company).finalize()["state"] == "queued"
    assert len(outgoing(trend)) == 1
    text = trend.preview()["text"]
    assert "관련 배경:" in text and "+100%" in text and "신기술 발표 · 기술" in text
    assert not trend.status()["model_calls"][0]["error"]
    sent = []

    def slack(request):
        body = json.loads(request.content)
        sent.append(body)
        return httpx.Response(200, json={"ok": True, "channel": "CTRENDS", "ts": "100.123"})

    force_due(trend)
    sender = SlackOutbox(trend.company, {"trend_scout": {"bot_token": "synthetic"}}, httpx.MockTransport(slack))
    assert await sender.send_one()
    assert sent[0]["text"] == text
    assert outgoing(trend)[0]["sent_ts"] == "100.123"


@pytest.mark.parametrize("database_timezone", ["UTC", "Asia/Seoul"])
async def test_real_preview_validation_requires_receipts_and_immutable_body(trend, database_timezone):
    trend.db.url = make_conninfo(trend.db.url, options=f"-c timezone={database_timezone}")
    trend.company.settings.trend_feed_publish_enabled = False
    ingest(trend)
    morning(trend)
    collector = TrendFeedCollector(trend.company, naver=FixtureNaver(), reader=lambda *args: {"ok": False})
    await collector.enrich(trend.claim_enrichment())
    call = trend.prepare_call()
    trend.finish_call(call, response(call["id"], trend.freeze()["bundle"]))
    trend.clock[0] = schedule.times(trend.clock[0])[1].astimezone(UTC)
    assert trend.finalize()["state"] == "preview"
    path = Path(__file__).parents[1] / "docs/project/evidence/trend-feed-20261006/preview-validator.py"
    spec = importlib.util.spec_from_file_location("preview_validator", path)
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    with trend.db.transaction() as conn:
        row = conn.execute("SELECT * FROM trend_feed_digests").fetchone()
        checked = validator.validate_day(trend, conn, row)
        assert checked["valid"], checked["reasons"]
        row["content"] += "무단으로 추가한 배경 설명"
        assert "final_body_changed" in validator.validate_day(trend, conn, row)["reasons"]


def test_promotion_requires_three_consecutive_stable_real_previews():
    path = Path(__file__).parents[1] / "docs/project/evidence/trend-feed-20261006/preview-observer.py"
    spec = importlib.util.spec_from_file_location("preview_observer", path)
    observer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(observer)
    days = [{"day": "2026-10-07", "valid": True, "stable": True},
            {"day": "2026-10-08", "valid": True, "stable": True},
            {"day": "2026-10-09", "valid": True, "stable": False}]
    assert not observer.completed_days(days)
    days[2]["stable"] = True
    assert observer.completed_days(days) == days
    days[1]["valid"] = False
    assert not observer.completed_days(days)
    days[1].update(valid=True, day="2026-10-10")
    assert not observer.completed_days(days)


def test_shared_article_merge_and_unknown_quotes_are_rejected(trend):
    ingest(trend)
    ingest(trend, title="다른 사람")
    morning(trend)
    bundle = trend.freeze()["bundle"]
    good = response("news-trends-test", bundle)
    draft = json.loads(good.decision.artifacts[0].content)
    draft["items"] = [{"member_ids": [c["id"] for c in bundle["candidates"]], "category": "사회"}]
    good.decision.artifacts[0].content = json.dumps(draft)
    with pytest.raises(ValueError, match="shared_original"):
        validate_draft(good, bundle)
    draft["items"][0].update(background="근거가 없는 설명입니다.", evidence=[{"article_id": "invented", "quote": "없는 출처의 구절입니다. 가짜입니다."}])
    good.decision.artifacts[0].content = json.dumps(draft)
    with pytest.raises(ValueError, match="quote_not_in_original"):
        validate_draft(good, bundle)


def test_two_model_calls_limit_and_late_result_cannot_replace_fallback(trend):
    ingest(trend)
    morning(trend)
    claim = trend.claim_enrichment()
    trend.save_enrichment(claim, claim["bundle"])
    first = trend.prepare_call()
    bad = response(first["id"], claim["bundle"])
    bad.decision.artifacts[0].content = '{"items": []}'
    trend.finish_call(first, bad)
    second = trend.prepare_call()
    assert second["stage"] == 1 and second["id"] != first["id"]
    assert trend.prepare_call() is None
    trend.clock[0] = schedule.times(trend.clock[0])[1].astimezone(UTC)
    trend.finalize()
    text = trend.preview()["text"]
    trend.finish_call(second, response(second["id"], claim["bundle"]))
    assert trend.preview()["text"] == text and len(outgoing(trend)) == 1


def test_unknown_model_outcome_is_never_automatically_replaced(trend):
    ingest(trend)
    morning(trend)
    claim = trend.claim_enrichment()
    trend.save_enrichment(claim, claim["bundle"])
    first = trend.prepare_call()
    trend.clock[0] += timedelta(minutes=13)
    assert trend.prepare_call() is None
    assert trend.status()["model_calls"][0]["error"] == "uncertain"
    assert len(trend.status()["model_calls"]) == 1
    assert first["id"] == trend.status()["model_calls"][0]["id"]


async def test_model_quota_wait_reuses_frozen_request(trend):
    ingest(trend)
    morning(trend)
    claim = trend.claim_enrichment()
    trend.save_enrichment(claim, claim["bundle"])
    seen = []

    class Provider:
        async def run(self, request):
            seen.append(request)
            raise ProviderFault("quota", "Synthetic quota", 60)

    editor = TrendFeedEditor(trend.company, Provider())
    await editor.tick()
    trend.clock[0] += timedelta(minutes=1)
    await editor.tick()
    assert seen[0] == seen[1] and len(trend.status()["model_calls"]) == 1


def test_reporter_assignment_is_frozen_during_quota_wait(trend):
    from quant_company.model_policy import targets

    trend.company.settings.model_assignments_enabled = True
    with trend.db.transaction() as conn:
        conn.execute("UPDATE model_assignment_policy SET revision=1,bindings=%s WHERE id=1",
                     (Jsonb({"reporter": {"model": "pinned-reporter", "reasoning_effort": "max"}}),))
    ingest(trend)
    morning(trend)
    claim = trend.claim_enrichment()
    trend.save_enrichment(claim, claim["bundle"])
    first = trend.prepare_call()
    assert first["request"]["model"] == "pinned-reporter"
    assert first["request"]["reasoning_effort"] == "max"
    assert "trend_scout" not in targets(trend.company)
    assert all(role["id"] != "trend_scout" for role in trend.company.runtime_context()["employees"])
    trend.fault_call(first, "quota", 60)
    with trend.db.transaction() as conn:
        conn.execute("UPDATE model_assignment_policy SET revision=2,bindings=%s WHERE id=1",
                     (Jsonb({"reporter": {"model": "later-reporter", "reasoning_effort": "low"}}),))
    trend.clock[0] += timedelta(minutes=1)
    resumed = trend.prepare_call()
    assert resumed["id"] == first["id"] and resumed["request"] == first["request"]
    with trend.db.transaction() as conn:
        binding = conn.execute("SELECT * FROM model_execution_bindings WHERE request_id=%s",
                               (first["id"],)).fetchone()
    assert binding["target"] == "reporter" and binding["selection"]["revision"] == 1


def test_naver_cache_and_atomic_daily_cap(trend):
    ingest(trend)
    morning(trend)
    digest = trend.freeze()
    client = FixtureNaver()
    a = trend.naver(digest["id"], "news", {"query": "a"}, client)
    assert trend.naver(digest["id"], "news", {"query": "a"}, client) == a
    assert len(client.calls) == 1
    with trend.db.transaction() as conn:
        conn.execute("UPDATE trend_feed_api_usage SET calls=100")
    assert trend.naver(digest["id"], "news", {"query": "b"}, client)["error"] == "naver_daily_cap"
    assert len(client.calls) == 1


def test_expiry_policy_change_preview_and_missing_data(trend):
    morning(trend)
    row = trend.freeze()
    assert row["bundle"]["collection_gap"]
    assert "유효한 관측이 없어" in render(row["bundle"])
    trend.company.settings.trend_feed_publish_enabled = False
    trend.clock[0] = schedule.times(trend.clock[0])[1].astimezone(UTC)
    assert trend.finalize()["state"] == "stale" and outgoing(trend) == []
    trend.clock[0] += timedelta(days=1, hours=1)
    assert trend.finalize()["state"] == "expired" and outgoing(trend) == []


def test_preview_never_becomes_a_backfill_on_enable(trend):
    trend.company.settings.trend_feed_publish_enabled = False
    ingest(trend)
    morning(trend)
    trend.freeze()
    trend.clock[0] = schedule.times(trend.clock[0])[1].astimezone(UTC)
    assert trend.finalize()["state"] == "preview" and not outgoing(trend)
    trend.company.settings.trend_feed_publish_enabled = True
    assert trend.finalize()["state"] == "preview" and not outgoing(trend)


@pytest.mark.parametrize("result,expected", [
    ({"ok": True, "ts": "123.456", "channel": "CTRENDS"}, "delivered"),
    ({"ok": True}, "uncertain"),
    ({"ok": True, "ts": "123.456", "channel": "CWRONG"}, "uncertain"),
])
async def test_slack_receipts_and_no_replay(trend, credentials, result, expected):
    ingest(trend)
    morning(trend)
    trend.freeze()
    trend.clock[0] = schedule.times(trend.clock[0])[1].astimezone(UTC)
    trend.finalize()
    force_due(trend)
    credentials["trend_scout"] = {"app_id": "ATREND", "bot_user_id": "UBOTTREND", "bot_token": "synthetic"}
    sent = []

    def handle(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=result)

    sender = SlackOutbox(trend.company, credentials, transport=httpx.MockTransport(handle))
    assert await sender.send_one()
    assert outgoing(trend)[0]["status"] == expected and len(sent) == 1
    assert not await sender.send_one()
    assert not sent[0]["unfurl_links"]


async def test_slack_429_same_identity_then_expiry_and_before_send_policy(trend, credentials):
    ingest(trend)
    morning(trend)
    trend.freeze()
    trend.clock[0] = schedule.times(trend.clock[0])[1].astimezone(UTC)
    trend.finalize()
    force_due(trend)
    credentials["trend_scout"] = {"bot_token": "synthetic"}
    sender = SlackOutbox(trend.company, credentials,
                        transport=httpx.MockTransport(lambda _: httpx.Response(429, headers={"retry-after": "1"})))
    await sender.send_one()
    assert outgoing(trend)[0]["status"] == "pending"
    force_due(trend)
    claimed = sender.claim()
    trend.company.settings.trend_feed_publish_enabled = False
    assert not sender.before_send(claimed)
    assert outgoing(trend)[0]["status"] == "stale"


def test_outbound_identity_and_manifest_cannot_trigger_models(trend, credentials, tmp_path):
    credential = {"app_id": "ATREND", "bot_user_id": "UBOT", "bot_token": "synthetic"}
    ingress = SlackIngress(trend.company.settings, trend.company, {**credentials, "trend_scout": credential})
    result = ingress.accept("trend_scout", {"team_id": "TTEST", "api_app_id": "ATREND",
        "event": {"type": "app_mention", "user": "UHUMAN", "channel": "CTRENDS", "text": "run"}}, credential)
    assert result["ignored"]
    manifests(trend.company, None, tmp_path, include_trend_scout=True)
    manifest = json.loads((tmp_path / "trend_scout.json").read_text())
    assert manifest["oauth_config"]["scopes"]["bot"] == ["chat:write"]
    assert "event_subscriptions" not in manifest["settings"]
    trend.company.settings.trend_feed_on_demand_enabled = True
    manifests(trend.company, None, tmp_path, include_trend_scout=True)
    manifest = json.loads((tmp_path / "trend_scout.json").read_text())
    assert json.loads(Path("slack-apps/trend_scout.json").read_text())["settings"] == manifest["settings"]


def test_source_permission_change_invalidates_pending_brief_and_raw_retention(trend):
    ingest(trend)
    morning(trend)
    row = trend.freeze()
    trend.clock[0] = schedule.times(trend.clock[0])[1].astimezone(UTC)
    trend.finalize()
    sources = json.loads(trend.company.settings.news_sources_file.read_text())
    sources[0]["use_for_summary"] = False
    trend.company.settings.news_sources_file.write_text(json.dumps(sources))
    with trend.db.transaction() as conn:
        message = {**outgoing(trend)[0], "agent": "trend_scout"}
        assert not trend.gate(conn, message)
    trend.clock[0] += timedelta(days=100)
    trend.cleanup()
    with trend.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM trend_feed_snapshots").fetchone()["n"] == len(row["bundle"]["snapshot_ids"])


def test_production_role_and_safe_default_configuration():
    settings = Settings()
    role = load_roles(settings)["trend_scout"]
    assert not role.active and not role.tools and not role.can_delegate_to
    assert not settings.trend_feed_enabled and not settings.trend_feed_publish_enabled
    assert not TrendFeedStore(Company(settings)).authorized()
