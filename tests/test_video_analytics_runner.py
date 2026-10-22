"""Real PostgreSQL; YouTube reads, claude-runtime and Slack are explicitly simulated fixtures."""

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from psycopg.types.json import Jsonb

from quant_company.company import PolicyError
from quant_company.contracts import AgentDecision, ProviderFault, ProviderResponse
from quant_company.slack import SlackIngress
from quant_company.video.analytics import AnalyticsRunner, shorts_summary
from quant_company.video.analytics_metrics import watch_ratio, window
from quant_company.video.analytics_slack import blocks
from quant_company.video.analytics_store import AnalyticsStore

from .test_briefing import brief  # noqa: F401
from .test_video import video  # noqa: F401
from .test_video_analytics_store import (  # noqa: F401
    PUBLISHED,
    SHORTS_TITLE,
    analytics,
    delivered_job,
    delivered_shorts_for,
    seed_lessons,
    upload,
)

# Saturday 21:00 KST, after the d3 window of 21-23 September: no strategy report is due (Thursday's caught up by 09:00).
AFTER_D3 = datetime(2026, 9, 26, 12, tzinfo=UTC)
MONDAY = datetime(2026, 9, 28, 0, 30, tzinfo=UTC)  # 09:30 KST, Monday 28 September
THURSDAY = datetime(2026, 10, 1, 0, 30, tzinfo=UTC)  # 09:30 KST, Thursday 1 October
METRICS = {"totals": {"views": 820, "watch_minutes": 2200.5, "avg_view_s": 161, "avg_view_pct": 55.2, "subscribers": 8,
                      "likes": 30, "comments": 4, "shares": 2},
           "curve": [[0.0, 1.0], [0.25, 0.6], [1.0, 0.45]], "relative_retention": 0.5,
           "traffic": [{"source": "SUBSCRIBER", "views": 400, "share": 400 / 820},
                       {"source": "YT_SEARCH", "views": 300, "share": 300 / 820}],
           "search_terms": [{"term": "엔비디아 실적", "views": 120}]}
BASIC = "date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
COMBINED = ("date,channel_id,video_id,traffic_source_type,traffic_source_detail,operating_system,device_type,"
            "video_thumbnail_impressions,video_thumbnail_impressions_ctr\n")


class SimulatedClient:
    def __init__(self, uploads=(), jobs=None):
        self.items, self.jobs, self.created, self.metric_calls = list(uploads), dict(jobs or {}), [], []
        self.reports_by_job, self.files = {}, {}
        # The Monday benchmark's public reads: none listed unless a test lists them.
        self.popular_by_category, self.channel_info, self.comments = {}, {}, {}
        self.popular_calls, self.channel_calls, self.comment_calls = [], [], []

    async def uploads(self):
        return self.items

    async def report_jobs(self):
        return dict(self.jobs)

    async def create_job(self, report_type):
        self.created.append(report_type)
        self.jobs[report_type] = "job-" + report_type
        return {"id": "job-" + report_type, "reportTypeId": report_type}

    async def reports(self, job_id):
        return self.reports_by_job.get(job_id, [])

    async def download(self, url):
        return self.files[url]

    async def video_metrics(self, youtube_id, start, end):
        self.metric_calls.append((youtube_id, start, end))
        return METRICS

    async def thumbnail(self, url):
        return b"\xff\xd8fixture-thumbnail", "image/jpeg"

    async def popular(self, category=None):
        self.popular_calls.append(category)
        return self.popular_by_category.get(category, [])

    async def channel_stats(self, ids):
        self.channel_calls.append(list(ids))
        return {i: self.channel_info[i] for i in ids if i in self.channel_info}

    async def top_comments(self, youtube_id, limit=5):
        self.comment_calls.append(youtube_id)
        return self.comments.get(youtube_id, [])

    def report(self, report_id, day, text, report_type="channel_reach_basic_a1"):
        self.reports_by_job.setdefault("job-" + report_type, []).append(
            {"id": report_id, "day": day, "created": datetime(2026, 9, 25, tzinfo=UTC), "url": "u-" + report_id})
        self.files["u-" + report_id] = text


NEW_TITLE = "엔비디아 실적 앞두고 반도체 강세 확산될까 | 9월 22일 아침 · 오늘의 증시story"


def performance(**extra):
    return {"summary": "노출은 많았지만 썸네일 문구가 제목을 반복해 클릭이 적었습니다.", "strengths": ["검색 유입이 높음"],
            "weaknesses": ["오프닝 30초 이탈"], "hypotheses": [{"stage": "ctr", "claim": "썸네일 문구가 제목과 같음",
                                                          "evidence": "CTR 4.0%, 기준 부족", "confidence": "low"}],
            "changes": [{"target": "title", "text": NEW_TITLE, "reason": "실제 검색어 '엔비디아 실적'이 제목에 없음"},
                        # Names another day: the upload would no longer read as its own edition.
                        {"target": "title", "text": "반도체 강세 | 9월 23일 아침 · 오늘의 증시story", "reason": "다른 회차 제목"},
                        {"target": "tags", "text": "엔비디아 실적, 반도체 전망", "reason": "검색 유입 300회 중 120회가 '엔비디아 실적'"}],
            **extra}


class SimulatedProvider:
    def __init__(self, weekly=None, fault=None):
        self.calls, self.weekly, self.fault = [], weekly, fault

    async def run(self, request):
        self.calls.append(request)
        if self.fault:
            raise ProviderFault(self.fault, "simulated " + self.fault)
        value = self.weekly if request.output_contract == "video_weekly_v1" else performance()
        return ProviderResponse(request_id=request.request_id, provider="claude", usage={"actual_model": request.model},
                                decision=AgentDecision(status="complete", say="", artifacts=[
                                    {"title": "Simulated", "content": json.dumps(value, ensure_ascii=False)}]))


def comparables(analytics, kind="am", n=3, views=300, before=PUBLISHED):  # noqa: F811
    """`n` earlier uploads of `kind` ('am' or 'shorts') with their three-day numbers: what a card is compared with."""
    ids = [f"{kind[:2]}cmp{i:06d}" for i in range(n)]
    analytics.upsert_publications([upload(youtube_id, f"쇼츠 {i}" if kind == "shorts"
                                          else f"훅 {i} | 9월 {18 + i}일 아침 · 오늘의 증시story",
                                          before - timedelta(days=4 - i)) for i, youtube_id in enumerate(ids)])
    if kind == "shorts":
        with analytics.db.transaction() as conn:
            conn.execute("UPDATE video_publications SET edition_kind='shorts' WHERE youtube_id=ANY(%s)", (ids,))
    for i, youtube_id in enumerate(ids):
        analytics.save_snapshot(youtube_id, "d3", *window(before - timedelta(days=4 - i), "d3"),
                                {"views": views, "ctr": 0.04, "hook_30s": 0.84, "avg_view_pct": 50.0})


def linked_upload(brief, video, comparable=3):  # noqa: F811
    """The delivered AM episode and its upload, with `comparable` earlier AM uploads so its 820 views stand out."""
    store, _ = video
    job = delivered_job(brief, video)
    artifacts = {**job["artifacts"], "scenes": [
        {"id": "open", "type": "cold_open", "heading": "반도체 강세, 어디까지", "t0": 0, "t1": 73},
        {"id": "board", "type": "market_board", "heading": "시장 한눈에", "t0": 73, "t1": 292}]}
    store.save(job, "delivered", artifacts=artifacts)
    client = SimulatedClient([upload()], jobs={"channel_reach_basic_a1": "job-channel_reach_basic_a1",
                                               "channel_reach_combined_a1": "job-channel_reach_combined_a1"})
    for day, rows in ((date(2026, 9, 21), "1000,0.05"), (date(2026, 9, 22), "400,0.025"), (date(2026, 9, 23), "100,0")):
        client.report(f"b{day.day}", day, BASIC + f"{day:%Y%m%d},UCfixture,aaaaaaaaaaa,{rows}\n")
        client.report(f"c{day.day}", day, COMBINED + f"{day:%Y%m%d},UCfixture,aaaaaaaaaaa,3,,1,1,{rows}\n",
                      "channel_reach_combined_a1")
    if comparable:
        comparables(AnalyticsStore(store.company), n=comparable)
    return job, client


def outbox(store):
    with store.db.transaction() as conn:
        return conn.execute("""SELECT o.*,m.kind FROM outbox o JOIN messages m ON m.id=o.id
            WHERE m.kind='video_analytics' ORDER BY o.created_at""").fetchall()


def deliver(store, row, ts):
    with store.db.transaction() as conn:
        conn.execute("UPDATE outbox SET status='delivered',sent_ts=%s WHERE id=%s", (ts, row["id"]))


async def test_first_tick_creates_each_missing_reporting_job_once(analytics):  # noqa: F811
    client = SimulatedClient()
    runner = AnalyticsRunner(analytics.company, client=client, provider=SimulatedProvider())
    await runner.tick(AFTER_D3)
    await runner.tick(AFTER_D3)
    assert client.created == ["channel_reach_basic_a1", "channel_reach_combined_a1"]
    assert analytics.report_job("channel_reach_combined_a1") == "job-channel_reach_combined_a1"


async def test_the_three_day_card_carries_reach_retention_search_and_the_diagnosis(brief, video, analytics):  # noqa: F811
    _, client = linked_upload(brief, video)
    provider = SimulatedProvider()
    result = await AnalyticsRunner(analytics.company, client=client, provider=provider).tick(AFTER_D3)
    assert result["cards"] == 1
    assert client.metric_calls == [("aaaaaaaaaaa", date(2026, 9, 21), date(2026, 9, 23))]
    request = provider.calls[0]
    assert (request.request_id, request.output_contract) == ("video-analytics-aaaaaaaaaaa-d3", "video_performance_v1")
    assert [a.media_type for a in request.attachments] == ["image/jpeg"]
    assert "반도체가 이끈 상승" in request.prompt and '"impressions": 1500' in request.prompt
    [card] = outbox(analytics)
    assert (card["channel"], card["thread_ts"], card["agent"]) == ("CPERF", None, "market_brief")
    # The card itself is the numbers, what to paste into YouTube Studio and the one-line diagnosis.
    for piece in ("노출 1,500", "CTR 4.0%", "조회 820", "기준 부족", "30초 유지 84%", "✏️ YouTube에서 바꿔볼 것",
                  f"1. 제목 → `{NEW_TITLE}`", "왜: 실제 검색어 '엔비디아 실적'이 제목에 없음",
                  "2. 태그 추가 → `엔비디아 실적, 반도체 전망`", "썸네일 문구가 제목을 반복해", "스레드"):
        assert piece in card["text"], piece
    for detail in ("9월 23일", "반도체 강세, 어디까지", "썸네일 문구가 제목과 같음"):
        assert detail not in card["text"], detail
    snapshot = analytics.snapshot("aaaaaaaaaaa", "d3")
    assert snapshot["metrics"]["impressions"] == 1500 and snapshot["metrics"]["ctr"] == pytest.approx(0.04)
    # Nothing is asked or posted twice.
    await AnalyticsRunner(analytics.company, client=client, provider=provider).tick(AFTER_D3)
    assert len(provider.calls) == 1 and len(outbox(analytics)) == 1


async def test_shorts_are_measured_on_their_own_scenes(brief, video, analytics):  # noqa: F811
    store, _ = video
    job, client = linked_upload(brief, video)
    delivered_shorts_for(video, job)
    lessons = [{"id": "l-episode", "scope": "thumbnail", "text": "본편 교훈"},
               {"id": "l-shorts", "scope": "shorts", "text": "쇼츠 교훈"}]
    with store.db.transaction() as conn:
        conn.execute("UPDATE video_jobs SET policy=policy||%s WHERE id=%s", (Jsonb({"lessons": lessons}), job["id"]))
    client.items.append(upload("sssssssssss", SHORTS_TITLE, PUBLISHED + timedelta(hours=3), duration_s=46))
    await AnalyticsRunner(analytics.company, client=client, provider=SimulatedProvider()).tick(AFTER_D3)
    snap = analytics.snapshot("sssssssssss", "d3")
    assert [d["id"] for d in snap["metrics"]["scene_drops"]] == ["opener", "agenda", "chip", "outro"]
    assert snap["metrics"]["scene_drops"][2]["heading"] == "issue"  # a Shorts manifest names its scenes by type
    assert snap["metrics"]["hook_30s"] == pytest.approx(watch_ratio(METRICS["curve"], 30 / 46))
    assert snap["taken_at"] == AFTER_D3
    rows = {e["youtube_id"]: e for e in analytics.episodes()}
    cut, episode = rows["sssssssssss"], rows["aaaaaaaaaaa"]
    assert cut["edition_kind"] == "shorts" and cut["plan"]["hook"]["headline"] == "반도체 5% 급등"
    assert cut["artifacts"]["duration"] == 46.0 and episode["artifacts"]["duration"] == 292.0
    assert (cut["lessons"], episode["lessons"]) == (["l-shorts"], ["l-episode"])  # each kind is judged on its own
    assert cut["d3_taken"] == AFTER_D3 and cut["d3"]["views"] == 820 and cut["window_end"] == date(2026, 9, 23)
    assert cut["craft"] == episode["craft"] == 1
    assert shorts_summary(cut["plan"]) == {"hook": "반도체 5% 급등", "question": "이유는?",
                                           "hook_narration": "반도체가 5% 올랐습니다.", "issues": ["반도체"]}
    assert shorts_summary({}) == {"hook": None, "question": None, "hook_narration": None, "issues": []}


@pytest.mark.parametrize("count,views,cards", [(3, 300, 1), (3, 600, 0), (2, 300, 0)])
async def test_only_an_outlier_gets_a_card_and_a_diagnosis(brief, video, analytics, count, views, cards):  # noqa: F811
    _, client = linked_upload(brief, video, comparable=0)
    comparables(analytics, n=count, views=views)
    provider = SimulatedProvider()
    runner = AnalyticsRunner(analytics.company, client=client, provider=provider)
    result = await runner.tick(AFTER_D3)
    assert result["cards"] == cards and len(provider.calls) == cards and len(outbox(analytics)) == cards
    if cards:
        assert "튄 지표: 조회 2.7배 (같은 아침 최근 28일 3편 중앙값 대비)" in outbox(analytics)[0]["text"]
        assert '"compared_with": 3' in provider.calls[0].prompt
        extra = analytics.analysis("video-analytics-aaaaaaaaaaa-d3")["request"]["extra"]
        assert (extra["outlier"], extra["outlier_n"]) == ([{"metric": "views", "ratio": 2.73}], 3)
    else:  # no card, so no seven-day update in its thread either
        await runner.tick(datetime(2026, 10, 3, 12, tzinfo=UTC))
        assert analytics.snapshot("aaaaaaaaaaa", "d7") and not provider.calls and not outbox(analytics)


async def test_outlier_checks_look_only_at_the_last_28_days(brief, video, analytics):  # noqa: F811
    _, client = linked_upload(brief, video)
    # Reportable once but never a card: an upload like this stays unposted for good.
    old = PUBLISHED - timedelta(days=29)
    analytics.upsert_publications([upload("ooooooooooo", "옛 회차 | 8월 23일 아침 · 오늘의 증시story", old)])
    analytics.save_snapshot("ooooooooooo", "d3", *window(old, "d3"), {"views": 300, "report": True})
    runner = AnalyticsRunner(analytics.company, client=client, provider=SimulatedProvider())
    asked, baselines = [], runner.store.baselines
    runner.store.baselines = lambda row, *args, **kwargs: asked.append(row["youtube_id"]) or baselines(row, *args,
                                                                                                         **kwargs)
    result = await runner.tick(AFTER_D3)
    assert result["cards"] == 1 and "aaaaaaaaaaa" in asked and "ooooooooooo" not in asked
    assert analytics.posted("aaaaaaaaaaa:d3") and not analytics.posted("ooooooooooo:d3")


async def test_an_outlier_shorts_card_reads_the_shorts_plan(brief, video, analytics):  # noqa: F811
    job, client = linked_upload(brief, video, comparable=0)
    delivered_shorts_for(video, job)
    client.items.append(upload("sssssssssss", SHORTS_TITLE, PUBLISHED + timedelta(hours=3), duration_s=46))
    comparables(analytics, kind="shorts")
    provider = SimulatedProvider()
    result = await AnalyticsRunner(analytics.company, client=client, provider=provider).tick(AFTER_D3)
    [request] = provider.calls
    assert result["cards"] == 1 and request.request_id == "video-analytics-sssssssssss-d3"
    assert "반도체 5% 급등" in request.prompt and '"question": "이유는?"' in request.prompt
    assert '"cold_open"' not in request.prompt  # not the episode's plan
    [card] = outbox(analytics)
    assert "Shorts" in card["text"]
    assert "튄 지표: 조회 2.7배 · 30초 유지 0.6배 (같은 Shorts 최근 28일 3편 중앙값 대비)" in card["text"]


async def test_no_analysis_is_requested_while_an_episode_is_in_production(brief, video, analytics):  # noqa: F811
    store, _ = video
    job, client = linked_upload(brief, video)
    store.save(job, "rendering")
    provider = SimulatedProvider()
    runner = AnalyticsRunner(analytics.company, client=client, provider=provider)
    await runner.tick(AFTER_D3)
    assert analytics.snapshot("aaaaaaaaaaa", "d3") and not provider.calls and not outbox(analytics)
    store.save(store.get(job["id"]), "delivered")
    await runner.tick(AFTER_D3)
    assert len(provider.calls) == 1 and len(outbox(analytics)) == 1


async def test_old_uploads_are_measured_for_baselines_without_cards(analytics):  # noqa: F811
    client = SimulatedClient([upload()])
    provider = SimulatedProvider()
    # Wednesday 21 October 10:00 KST: no report is due; d28 (21 Sep - 18 Oct) still waits for reach data.
    await AnalyticsRunner(analytics.company, client=client, provider=provider).tick(datetime(2026, 10, 21, 1, tzinfo=UTC))
    assert {h for h in ("d3", "d7", "d28") if analytics.snapshot("aaaaaaaaaaa", h)} == {"d3", "d7"}
    assert not provider.calls and not outbox(analytics)


async def test_a_busy_runtime_defers_the_card(brief, video, analytics):  # noqa: F811
    _, client = linked_upload(brief, video)

    def reserved():
        with analytics.db.transaction() as conn:
            row = conn.execute("SELECT reserved FROM daily_usage WHERE day=CURRENT_DATE").fetchone()
        return row["reserved"] if row else 0

    before = reserved()
    await AnalyticsRunner(analytics.company, client=client, provider=SimulatedProvider(fault="busy")).tick(AFTER_D3)
    assert not outbox(analytics) and reserved() == before
    await AnalyticsRunner(analytics.company, client=client, provider=SimulatedProvider()).tick(AFTER_D3)
    assert "썸네일 문구가 제목을 반복해" in outbox(analytics)[0]["text"]


async def test_a_failed_analysis_still_posts_the_numbers(brief, video, analytics):  # noqa: F811
    _, client = linked_upload(brief, video)
    await AnalyticsRunner(analytics.company, client=client,
                          provider=SimulatedProvider(fault="invalid_output")).tick(AFTER_D3)
    [card] = outbox(analytics)
    assert "노출 1,500" in card["text"] and "분석 실행 실패(invalid_output)" in card["text"]


async def test_the_seven_day_update_replies_in_the_card_thread_after_its_receipt(brief, video, analytics):  # noqa: F811
    _, client = linked_upload(brief, video)
    runner = AnalyticsRunner(analytics.company, client=client, provider=SimulatedProvider())
    await runner.tick(AFTER_D3)
    later = datetime(2026, 10, 3, 12, tzinfo=UTC)  # d7 window 21-27 September is over, no reach for the 27th
    await runner.tick(later)
    assert len(outbox(analytics)) == 1  # waits for the card's Slack receipt
    deliver(analytics, outbox(analytics)[0], "300.001")
    await runner.tick(later)
    card, detail, update = outbox(analytics)
    assert update["thread_ts"] == "300.001" and "7일" in update["text"] and "조회 820" in update["text"]


async def test_the_full_diagnosis_follows_in_the_card_thread_after_its_receipt(brief, video, analytics):  # noqa: F811
    _, client = linked_upload(brief, video)
    runner = AnalyticsRunner(analytics.company, client=client, provider=SimulatedProvider())
    first = await runner.tick(AFTER_D3)
    assert len(outbox(analytics)) == 1 and first["details"] == 0  # no thread before Slack's receipt
    deliver(analytics, outbox(analytics)[0], "300.001")
    result = await runner.tick(AFTER_D3)
    card, detail = outbox(analytics)
    assert result["details"] == 1 and detail["thread_ts"] == "300.001"
    for piece in ("자세한 진단", "같은 아침 회차", "유입:", "\"엔비디아 실적\" 120", "반도체 강세, 어디까지",
                  "+ 검색 유입이 높음", "− 오프닝 30초 이탈", "썸네일 문구가 제목과 같음 — CTR 4.0%"):
        assert piece in detail["text"], piece
    await runner.tick(AFTER_D3)
    assert len(outbox(analytics)) == 2


async def test_a_posted_card_and_its_detail_are_no_longer_pending(brief, video, analytics):  # noqa: F811
    _, client = linked_upload(brief, video)
    runner = AnalyticsRunner(analytics.company, client=client, provider=SimulatedProvider())
    await runner.tick(AFTER_D3)
    assert analytics.unposted("d3") == []
    deliver(analytics, outbox(analytics)[0], "300.001")
    await runner.tick(AFTER_D3)
    assert len(outbox(analytics)) == 2 and analytics.undetailed() == []


async def test_a_card_posted_before_the_studio_edits_does_not_stop_the_tick(brief, video, analytics):  # noqa: F811
    # Production 10/15-10/19: cards posted before #164 have receipts without changes.
    _, client = linked_upload(brief, video)
    runner = AnalyticsRunner(analytics.company, client=client, provider=SimulatedProvider())
    await runner.tick(AFTER_D3)
    with analytics.db.transaction() as conn:
        conn.execute("UPDATE video_analyses SET receipt=receipt #- '{output,changes}',output=output-'changes'")
    result = await runner.tick(AFTER_D3)
    assert (result["state"], result["cards"], result["details"]) == ("ok", 0, 0) and len(outbox(analytics)) == 1


async def test_a_card_whose_analysis_failed_has_no_detail_thread(brief, video, analytics):  # noqa: F811
    _, client = linked_upload(brief, video)
    runner = AnalyticsRunner(analytics.company, client=client, provider=SimulatedProvider(fault="invalid_output"))
    await runner.tick(AFTER_D3)
    deliver(analytics, outbox(analytics)[0], "300.001")
    await runner.tick(AFTER_D3)
    [card] = outbox(analytics)
    assert "유입:" in card["text"] and "\"엔비디아 실적\" 120" in card["text"]  # the numbers stay in the card


def strategy_output(active_id, **extra):
    return {"headline": "아침 회차는 검색 유입이 늘었고 CTR은 기준 부족입니다.",
            "findings": [{"factor": "title", "text": "질문형 제목이 사실형보다 30초 유지가 높았습니다", "effect": "worked",
                          "n": 6, "confidence": "low"},
                         {"factor": "length", "text": "길이 차이는 아직 비교할 편수가 적습니다", "effect": "worked", "n": 2,
                          "confidence": "low"}],
            "viewers": ["시장판 장면에서 이탈이 가장 적었습니다"], "traffic": "탐색(홈·구독) 비중이 가장 컸습니다",
            "keywords": ["엔비디아 실적을 제목 앞에"], "shorts": "아직 본편과 짝지은 Shorts가 없습니다",
            "strategies": [{"text": "썸네일 문구는 제목에 없는 숫자 하나와 질문으로 쓴다", "scope": "thumbnail",
                            "evidence": "검색 유입이 높은 회차의 공통점", "videos": ["aaaaaaaaaaa"], "expected": "CTR",
                            "confidence": "low"},
                           {"text": "존재하지 않는 영상을 근거로 든 제안입니다", "scope": "title",
                            "evidence": "채널에 없는 영상을 근거로 들었음", "videos": ["zzzzzzzzzzz"], "expected": "CTR",
                            "confidence": "low"},
                           {"text": "쇼츠 첫 문장은 질문으로 열고 숫자를 먼저 보여 준다", "scope": "shorts",
                            "evidence": "질문형 훅 회차의 30초 유지가 높았음", "videos": ["aaaaaaaaaaa"],
                            "expected": "30초 유지", "confidence": "low"}],
            "experiment": {"change": "썸네일 문구를 질문형으로 쓴다", "compare": "사실형 문구 회차", "episodes": 6,
                           "metric": "CTR"},
            "verdicts": [{"lesson_id": active_id, "verdict": "too_early", "reason": "적용 회차가 1편"},
                         {"lesson_id": "00000000-0000-0000-0000-000000000000", "verdict": "retire",
                          "reason": "없는 교훈"}],
            "benchmark": [], **extra}


CONTRACT_ERROR = "Video output failed its contract: findings.0.text: String should have at most 500 characters"


class InvalidReportProvider(SimulatedProvider):
    """claude-runtime refusing the report's output contract: the first ask only, or every ask."""

    def __init__(self, weekly=None, every=False):
        super().__init__(weekly=weekly)
        self.every = every

    async def run(self, request):
        if request.output_contract == "video_weekly_v1" and (self.every or not request.request_id.endswith("-r2")):
            self.calls.append(request)
            raise ProviderFault("invalid_output", CONTRACT_ERROR)
        return await super().run(request)


async def weekly_posted(brief, video, analytics, provider=None, prepare=None, **extra):  # noqa: F811
    """The card on Saturday, then the Monday report with one active lesson; `prepare` sets up the client first."""
    _, client = linked_upload(brief, video)
    if prepare:
        prepare(client)
    [active] = seed_lessons(analytics, ["active"])
    provider = provider or SimulatedProvider()
    provider.weekly = strategy_output(active, **extra)
    runner = AnalyticsRunner(analytics.company, client=client, provider=provider)
    await runner.tick(AFTER_D3)
    await runner.tick(MONDAY)
    return runner, provider, active


def report_calls(provider):
    return [c for c in provider.calls if c.output_contract == "video_weekly_v1"]


async def test_the_monday_report_measures_both_kinds_and_proposes_strategies_with_buttons(brief, video, analytics):  # noqa: F811
    runner, provider, active = await weekly_posted(brief, video, analytics)
    report = next(c for c in provider.calls if c.output_contract == "video_weekly_v1")
    assert report.request_id == "video-analytics-weekly-2026-09-28" and report.attachments
    assert "aaaaaaaaaaa" in report.prompt and active in report.prompt and '"new_videos": ["aaaaaaaaaaa"]' in report.prompt
    text = outbox(analytics)[-1]["text"]
    for piece in ("채널 전략 리포트 · 09/28(월)", "데이터 기준 09/23", "본편 1편 · 조회 820", "[효과 있음 · n=6",
                  "[아직 판단 이름 · n=2", "판단 이르음", "다음 전략 2가지"):
        assert piece in text, piece
    assert "없는 교훈" not in text and len(text) <= 2900
    assert "*시청자가 좋아한 것*" in text and "*검색어 상위*" in text  # short enough to carry the details itself
    assert {p["scope"] for p in analytics.lessons(["proposed"])} == {"thumbnail", "shorts"}
    actions = [e["action_id"] for b in blocks(analytics.company, outbox(analytics)[-1]) if b["type"] == "actions"
               for e in b["elements"]]
    assert actions == ["video_lesson_adopt", "video_lesson_reject"] * 2 + ["video_lesson_retire"]
    extra = analytics.analysis("video-analytics-weekly-2026-09-28")["request"]["extra"]
    assert extra["stats"]["this"]["episodes"]["views"] == 820 and extra["benchmark"] == {"skipped": "no_candidates"}
    await runner.tick(MONDAY)
    deliver(analytics, outbox(analytics)[-1], "600.001")
    await runner.tick(MONDAY + timedelta(minutes=30))  # the root holds the details: the benchmark note alone follows
    assert sum(c.output_contract == "video_weekly_v1" for c in provider.calls) == 1
    assert [r["text"] for r in outbox(analytics) if r["thread_ts"] == "600.001"] == [
        "*벤치마크*: 이번에는 후보를 가져오지 못했습니다(no_candidates)."]


async def test_an_invalid_report_is_asked_once_more_with_the_contract_errors(brief, video, analytics):  # noqa: F811
    runner, provider, _ = await weekly_posted(brief, video, analytics, InvalidReportProvider())
    first, again = report_calls(provider)
    assert (first.request_id, again.request_id) == ("video-analytics-weekly-2026-09-28",
                                                    "video-analytics-weekly-2026-09-28-r2")
    assert again.prompt.startswith(first.prompt) and "findings.0.text" in again.prompt
    assert (again.attachments, again.reasoning_effort, again.model) == (first.attachments, first.reasoning_effort,
                                                                        first.model)
    r2 = analytics.analysis("video-analytics-weekly-2026-09-28-r2")
    assert r2["message_id"] and analytics.analysis("video-analytics-weekly-2026-09-28")["message_id"] is None
    assert {p["analysis_id"] for p in analytics.lessons(["proposed"])} == {r2["id"]}
    assert "다음 전략 2가지" in outbox(analytics)[-1]["text"]
    await runner.tick(MONDAY)
    assert len(report_calls(provider)) == 2


async def test_a_pending_retry_is_asked_again_with_the_same_request(brief, video, analytics):  # noqa: F811
    class PendingRetry(InvalidReportProvider):
        async def run(self, request):
            if request.request_id.endswith("-r2"):
                self.calls.append(request)
                raise ProviderFault("pending", "simulated pending")
            return await super().run(request)

    runner, provider, _ = await weekly_posted(brief, video, analytics, PendingRetry())
    _, retry = report_calls(provider)
    assert "채널 전략 리포트" not in outbox(analytics)[-1]["text"]
    analytics.settings.video_review_effort = "max"  # inputs move on while claude-runtime still runs the retry
    finished = SimulatedProvider(weekly=provider.weekly)
    await AnalyticsRunner(analytics.company, client=runner.client, provider=finished).tick(MONDAY)
    [again] = report_calls(finished)  # the failed first ask is not asked again
    assert (again.request_id, again.prompt, again.reasoning_effort, again.attachments) == (
        retry.request_id, retry.prompt, retry.reasoning_effort, retry.attachments)
    assert "채널 전략 리포트" in outbox(analytics)[-1]["text"]


async def test_a_report_failing_twice_posts_its_code_numbers_and_next_date(brief, video, analytics):  # noqa: F811
    runner, provider, _ = await weekly_posted(brief, video, analytics, InvalidReportProvider(every=True))
    text = outbox(analytics)[-1]["text"]
    assert "분석 실행 실패(invalid_output)" in text and "다음 리포트: 10/01(목) 09:00" in text and "본편 1편 · 조회 820" in text
    assert analytics.lessons(["proposed"]) == []
    deliver(analytics, outbox(analytics)[-1], "600.001")
    await runner.tick(MONDAY)
    assert sum(c.output_contract == "video_weekly_v1" for c in provider.calls) == 2
    assert outbox(analytics)[-1]["thread_ts"] is None  # no detail without a report


async def test_a_long_report_moves_details_to_the_first_thread_reply(brief, video, analytics):  # noqa: F811
    runner, _, _ = await weekly_posted(brief, video, analytics, viewers=["가" * 480] * 4, keywords=["나" * 480] * 5)
    root = outbox(analytics)[-1]
    assert len(root["text"]) <= 2900 and "*시청자가 좋아한 것*" not in root["text"] and "다음 전략" in root["text"]
    await runner.tick(MONDAY + timedelta(minutes=10))
    assert outbox(analytics)[-1]["id"] == root["id"]  # the reply waits for the root's Slack receipt
    deliver(analytics, root, "600.001")
    await runner.tick(MONDAY + timedelta(minutes=30))
    detail, note = outbox(analytics)[-2:]  # the details first, then Monday's benchmark reply
    assert detail["thread_ts"] == "600.001" and "*시청자가 좋아한 것*" in detail["text"] and "*검색어 상위*" in detail["text"]
    assert "\"엔비디아 실적\" 120" in detail["text"] and "탐색(홈·구독)" in detail["text"]
    assert note["thread_ts"] == "600.001" and note["text"].startswith("*벤치마크*")
    await runner.tick(MONDAY + timedelta(minutes=60))
    assert outbox(analytics)[-1]["id"] == note["id"]


def benchmark_candidates(client):
    """Eight public candidates over seven channels (channel 0 twice, one video listed in two categories)."""
    videos = [{"youtube_id": f"bench{n:06d}", "channel_id": f"UCb{n % 7}", "channel_title": f"채널 {n % 7}",
               "title": f"인기 영상 {n}", "description": "설명 앞부분", "published_at": datetime(2026, 9, 27, tzinfo=UTC),
               "duration_s": 300, "views": 10_000 * (n + 1), "likes": 100, "comments": 10,
               "thumbnail_url": f"https://i.ytimg.com/vi/bench{n:06d}/hqdefault.jpg"} for n in range(8)]
    client.popular_by_category = {None: videos[:4], "22": videos[4:6], "27": [videos[6], videos[0]], "28": [videos[7]]}
    client.channel_info = {f"UCb{n}": {"subscribers": 1_000 * (n + 1), "created": datetime(2015, 1, 1, tzinfo=UTC)}
                           for n in range(7)}
    client.comments = {v["youtube_id"]: ["댓글 반응: 정리가 깔끔해요"] for v in videos}


BENCHMARK_NOTES = [{"youtube_id": "bench000007", "why": "제목 첫 단어가 질문이었습니다", "transfer": ["첫 문장을 질문으로", "숫자 하나만"]},
                   {"youtube_id": "bench000006", "why": "첫 장면에 결론을 보여 줬습니다", "transfer": ["결론부터 보여 준다"]},
                   {"youtube_id": "bench000000", "why": "고르지 않은 영상입니다", "transfer": ["무시된다"]}]


async def test_the_monday_report_brings_five_benchmark_videos_within_twenty_quota_units(brief, video, analytics):  # noqa: F811
    runner, provider, _ = await weekly_posted(brief, video, analytics, prepare=benchmark_candidates,
                                              benchmark=BENCHMARK_NOTES)
    client = runner.client
    assert client.popular_calls == [None, "22", "27", "28"]
    assert client.channel_calls == [[f"UCb{n}" for n in range(7)]]
    units = len(client.popular_calls) + sum(-(-len(ids) // 50) for ids in client.channel_calls) + len(client.comment_calls)
    assert units <= 20 and len(client.comment_calls) == 5
    # Views per subscriber: channel 0's second video (80) leads, then the ties by views.
    assert client.comment_calls == [f"bench00000{n}" for n in (7, 6, 5, 4, 3)]
    report = next(c for c in provider.calls if c.output_contract == "video_weekly_v1")
    assert sum(a.name.startswith("thumb-") for a in report.attachments) == 1 + 5 and "댓글 " in report.prompt
    assert [a.name for a in report.attachments][1:] == [f"thumb-bench00000{n}.jpg" for n in (7, 6, 5, 4, 3)]
    for piece in ('"thumbnail": "thumb-bench000007"', "KR mostPopular", '"views_per_subscriber": 80.0',
                  '"thumbnail_attachments": ["aaaaaaaaaaa"]'):
        assert piece in report.prompt, piece
    extra = analytics.analysis("video-analytics-weekly-2026-09-28")["request"]["extra"]
    assert extra["benchmark"][0] == {"youtube_id": "bench000007", "channel": "채널 0", "title": "인기 영상 7",
                                     "views": 80_000, "subscribers": 1_000} and len(extra["benchmark"]) == 5
    root = outbox(analytics)[-1]
    assert "벤치마크" not in root["text"]
    await runner.tick(MONDAY + timedelta(minutes=10))
    assert outbox(analytics)[-1]["id"] == root["id"]  # waits for the root's Slack receipt
    deliver(analytics, root, "600.001")
    await runner.tick(MONDAY + timedelta(minutes=30))
    note = outbox(analytics)[-1]
    assert note["thread_ts"] == "600.001"
    for piece in ("*벤치마크* · 한국 인기 영상 중 구독자 대비 조회가 큰 2편",
                  "1. 채널 0 · 「인기 영상 7」 조회 80,000 · 구독 1,000 · https://youtu.be/bench000007",
                  "왜: 제목 첫 단어가 질문이었습니다", "옮길 점: 첫 문장을 질문으로 / 숫자 하나만", "2. 채널 6"):
        assert piece in note["text"], piece
    assert "bench000000" not in note["text"]
    await runner.tick(MONDAY + timedelta(minutes=60))
    assert outbox(analytics)[-1]["id"] == note["id"] and len(client.popular_calls) == 4


async def test_thursday_reports_and_re_asks_fetch_no_benchmark(brief, video, analytics):  # noqa: F811
    _, client = linked_upload(brief, video)
    benchmark_candidates(client)
    [active] = seed_lessons(analytics, ["active"])
    await AnalyticsRunner(analytics.company, client=client, provider=SimulatedProvider()).tick(AFTER_D3)
    assert client.popular_calls == []
    pending = SimulatedProvider(fault="pending")
    await AnalyticsRunner(analytics.company, client=client, provider=pending).tick(MONDAY)
    [first] = report_calls(pending)
    assert len(client.popular_calls) == 4 and "채널 전략 리포트" not in outbox(analytics)[-1]["text"]
    finished = SimulatedProvider(weekly=strategy_output(active))
    runner = AnalyticsRunner(analytics.company, client=client, provider=finished)
    await runner.tick(MONDAY + timedelta(minutes=30))
    [again] = report_calls(finished)
    assert (again.prompt, again.attachments) == (first.prompt, first.attachments)
    assert len(client.popular_calls) == 4 and len(client.comment_calls) == 5  # the first ask's candidates
    assert "채널 전략 리포트 · 09/28(월)" in outbox(analytics)[-1]["text"]
    await runner.tick(THURSDAY)
    thursday = report_calls(finished)[-1]
    assert thursday.request_id == "video-analytics-weekly-2026-10-01" and '"benchmark": null' in thursday.prompt
    assert len(client.popular_calls) == 4 and not [a for a in thursday.attachments if "bench" in a.name]
    assert analytics.analysis("video-analytics-weekly-2026-10-01")["request"]["extra"]["benchmark"] is None
    deliver(analytics, outbox(analytics)[-1], "700.001")
    await runner.tick(THURSDAY + timedelta(minutes=30))
    assert not [r for r in outbox(analytics) if r["thread_ts"] == "700.001"]  # no benchmark reply on Thursday


async def test_a_failed_benchmark_fetch_leaves_a_note_and_the_report_goes_on(brief, video, analytics):  # noqa: F811
    import httpx

    async def refused(category=None):
        request = httpx.Request("GET", "https://www.googleapis.com/youtube/v3/videos")
        raise httpx.HTTPStatusError("forbidden", request=request, response=httpx.Response(403, request=request))

    def prepare(client):
        benchmark_candidates(client)
        client.popular = refused

    runner, provider, _ = await weekly_posted(brief, video, analytics, prepare=prepare, benchmark=BENCHMARK_NOTES)
    report = report_calls(provider)[0]
    assert '"benchmark": {"skipped": "HTTPStatusError"}' in report.prompt and len(report.attachments) == 1
    assert runner.client.comment_calls == [] and "다음 전략 2가지" in outbox(analytics)[-1]["text"]
    extra = analytics.analysis("video-analytics-weekly-2026-09-28")["request"]["extra"]
    assert extra["benchmark"] == {"skipped": "HTTPStatusError"}
    deliver(analytics, outbox(analytics)[-1], "600.001")
    await runner.tick(MONDAY + timedelta(minutes=30))
    note = outbox(analytics)[-1]
    assert note["thread_ts"] == "600.001" and note["text"] == "*벤치마크*: 이번에는 후보를 가져오지 못했습니다(HTTPStatusError)."


def lesson_click(analytics, lesson_id, message_id, action="adopt", **changes):  # noqa: F811
    labels = {"adopt": "채택", "reject": "거절", "retire": "은퇴"}
    payload = {"type": "block_actions", "team": {"id": "TTEST"}, "api_app_id": "AVIDEO", "user": {"id": "UHUMAN"},
               "channel": {"id": "CPERF"}, "message": {"user": "UBRIEF", "app_id": "AVIDEO", "ts": "400.001"},
               "container": {"type": "message", "message_ts": "400.001", "channel_id": "CPERF"},
               "actions": [{"action_id": "video_lesson_" + action, "type": "button", "block_id": "video-lesson:" + lesson_id,
                            "text": {"type": "plain_text", "text": labels[action]},
                            "value": json.dumps({"id": lesson_id, "message": message_id}), "action_ts": "400.002"}]}
    for path, value in changes.items():
        target = payload
        *parents, leaf = path.split(".")
        for part in parents:
            target = target[int(part)] if part.isdigit() else target[part]
        target[leaf] = value
    return SlackIngress(analytics.settings, analytics.company, {"market_brief": CREDENTIAL}).accept(
        "market_brief", payload, CREDENTIAL)


CREDENTIAL = {"app_id": "AVIDEO", "bot_user_id": "UBRIEF", "bot_token": "fake", "signing_secret": "s"}


async def test_the_owner_adopts_a_lesson_from_the_delivered_review(brief, video, analytics):  # noqa: F811
    await weekly_posted(brief, video, analytics)
    review = outbox(analytics)[-1]
    deliver(analytics, review, "400.001")
    proposed = analytics.lessons(["proposed"])[0]
    result = lesson_click(analytics, str(proposed["id"]), str(review["id"]))
    assert result["outcome"] == "active" and len(analytics.active_lessons()) == 2
    assert lesson_click(analytics, str(proposed["id"]), str(review["id"]))["outcome"] == "active"  # Slack retry
    reply = outbox(analytics)[-1]
    assert reply["thread_ts"] == "400.001" and "채택" in reply["text"]
    with analytics.db.transaction() as conn:
        project = conn.execute("SELECT p.title FROM projects p JOIN messages m ON m.project_id=p.id WHERE m.id=%s",
                               (reply["id"],)).fetchone()
    assert project["title"] == "채널 전략 리포트 2026-09-28"
    assert len([r for r in outbox(analytics) if r["thread_ts"] == "400.001"]) == 1


@pytest.mark.parametrize("change", [{"user.id": "UOTHER"}, {"channel.id": "CQUANT"}, {"container.channel_id": "CQUANT"},
                                    {"container.message_ts": "999.999"}, {"message.user": "UFORGED"},
                                    {"actions.0.text": {"type": "plain_text", "text": "거절"}},
                                    {"actions.0.block_id": "video-lesson:other"}])
async def test_forged_lesson_clicks_are_refused(brief, video, analytics, change):  # noqa: F811
    await weekly_posted(brief, video, analytics)
    review = outbox(analytics)[-1]
    deliver(analytics, review, "400.001")
    proposed = analytics.lessons(["proposed"])[0]
    with pytest.raises(PolicyError):
        lesson_click(analytics, str(proposed["id"]), str(review["id"]), **change)
    assert analytics.lessons(["proposed"])


async def test_a_click_on_an_undelivered_or_foreign_message_is_refused(brief, video, analytics):  # noqa: F811
    await weekly_posted(brief, video, analytics)
    review = outbox(analytics)[-1]
    proposed = analytics.lessons(["proposed"])[0]
    with pytest.raises(PolicyError):  # no Slack receipt yet
        lesson_click(analytics, str(proposed["id"]), str(review["id"]))
    deliver(analytics, review, "400.001")
    with pytest.raises(PolicyError):  # the three-day card is not a review message
        lesson_click(analytics, str(proposed["id"]), str(outbox(analytics)[0]["id"]))


def test_posts_go_stale_when_analytics_is_switched_off(analytics):  # noqa: F811
    message = analytics.post("aaaaaaaaaaa", "aaaaaaaaaaa:d3", "카드", "📊 카드")
    analytics.settings.video_analytics_enabled = False
    with analytics.db.transaction() as conn:
        row = conn.execute("SELECT * FROM outbox WHERE id=%s", (message,)).fetchone()
        assert AnalyticsStore(analytics.company).gate(conn, row) is False
        assert conn.execute("SELECT status FROM outbox WHERE id=%s", (message,)).fetchone()["status"] == "stale"


async def test_the_outbox_sends_the_review_with_its_buttons_and_stops_when_switched_off(brief, video, analytics):  # noqa: F811
    import httpx

    from quant_company.slack import SlackOutbox

    await weekly_posted(brief, video, analytics)
    card, review = outbox(analytics)
    with analytics.db.transaction() as conn:
        # Only the analytics posts are pending; the brief's own messages count as delivered.
        conn.execute("UPDATE outbox SET status='delivered',sent_ts='100.000' WHERE id<>ALL(%s)", ([card["id"], review["id"]],))
    sent = []

    def fake(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "ts": f"500.00{len(sent)}"})

    slack = SlackOutbox(analytics.company, {"market_brief": {"bot_token": "fake"}}, httpx.MockTransport(fake))
    assert await slack.send_one() and await slack.send_one()
    assert [s["channel"] for s in sent] == ["CPERF", "CPERF"] and "blocks" not in sent[0]
    actions = [e["action_id"] for b in sent[1]["blocks"] if b["type"] == "actions" for e in b["elements"]]
    assert actions == ["video_lesson_adopt", "video_lesson_reject"] * 2 + ["video_lesson_retire"]
    analytics.post("aaaaaaaaaaa", "aaaaaaaaaaa:extra", "카드", "추가 글")
    analytics.settings.video_analytics_enabled = False
    assert not await slack.send_one()
    assert len(sent) == 2


async def test_dry_run_sync_reads_youtube_and_writes_nothing(brief, video, analytics):  # noqa: F811
    _, client = linked_upload(brief, video, comparable=0)
    client.jobs = {}
    preview = await AnalyticsRunner(analytics.company, client=client, provider=SimulatedProvider()).preview(AFTER_D3)
    assert preview["report_jobs"] == {} and client.created == []
    [item] = preview["uploads"]
    assert (item["youtube_id"], item["edition"], item["day"]) == ("aaaaaaaaaaa", "am", "2026-09-22")
    assert preview["latest_window"]["metrics"]["totals"]["views"] == 820
    assert analytics.publications() == [] and not outbox(analytics)


async def test_dry_run_skips_other_series_for_the_latest_window(brief, video, analytics):  # noqa: F811
    _, client = linked_upload(brief, video, comparable=0)
    client.items.insert(0, upload("hhhhhhhhhhh", "[역사story] 한글날은 왜 10월 9일일까?", PUBLISHED.replace(day=22)))
    preview = await AnalyticsRunner(analytics.company, client=client, provider=SimulatedProvider()).preview(AFTER_D3)
    assert [(i["youtube_id"], i["series"]) for i in preview["uploads"]] == [("hhhhhhhhhhh", False), ("aaaaaaaaaaa", True)]
    assert preview["latest_window"]["youtube_id"] == "aaaaaaaaaaa"


async def test_status_counts_links_reports_snapshots_and_lessons(brief, video, analytics):  # noqa: F811
    await weekly_posted(brief, video, analytics)
    status = analytics.status()
    assert status["publications"] == {"total": 4, "linked": 1, "other_series": 0, "unlinked": [
        {"youtube_id": f"amcmp00000{i}", "title": f"훅 {i} | 9월 {18 + i}일 아침 · 오늘의 증시story"} for i in (2, 1, 0)]}
    assert status["latest_reach_day"] == "2026-09-23" and status["snapshots"] == {"d3": 4}
    assert status["lessons"] == {"active": 1, "proposed": 2}
    assert status["report_jobs"] == ["channel_reach_basic_a1", "channel_reach_combined_a1"]


async def test_one_unreadable_video_or_report_does_not_hold_up_the_rest(analytics):  # noqa: F811
    import httpx

    class Partly(SimulatedClient):
        async def video_metrics(self, youtube_id, start, end):
            if youtube_id == "aaaaaaaaaaa":
                request = httpx.Request("GET", "https://youtubeanalytics.googleapis.com/v2/reports")
                raise httpx.HTTPStatusError("not found", request=request, response=httpx.Response(404, request=request))
            return await super().video_metrics(youtube_id, start, end)

    client = Partly([upload(), upload("bbbbbbbbbbb")], jobs={"channel_reach_basic_a1": "job-channel_reach_basic_a1",
                                                             "channel_reach_combined_a1": "job-channel_reach_combined_a1"})
    client.report("broken", date(2026, 9, 22), "date,views\n20260922,3\n")
    for day in (21, 22, 23):
        client.report(f"b{day}", date(2026, 9, day), BASIC + f"202609{day},UCfixture,bbbbbbbbbbb,10,0.1\n")
        client.report(f"c{day}", date(2026, 9, day), COMBINED + f"202609{day},UCfixture,bbbbbbbbbbb,3,,1,1,10,0.1\n",
                      "channel_reach_combined_a1")
    result = await AnalyticsRunner(analytics.company, client=client, provider=SimulatedProvider()).tick(AFTER_D3)
    assert result["reports"] == 6 and result["snapshots"] == 1
    assert analytics.snapshot("bbbbbbbbbbb", "d3") and not analytics.snapshot("aaaaaaaaaaa", "d3")


async def test_a_still_running_analysis_is_asked_again_with_its_frozen_request(brief, video, analytics):  # noqa: F811
    _, client = linked_upload(brief, video)
    pending = SimulatedProvider(fault="pending")
    await AnalyticsRunner(analytics.company, client=client, provider=pending).tick(AFTER_D3)
    assert not outbox(analytics)
    # Inputs move on before the turn finishes: the owner retitles the upload and the effort setting changes.
    client.items = [{**client.items[0], "title": "새 제목 | 9월 22일 아침 · 오늘의 증시story"}]
    analytics.settings.video_reasoning_effort = "max"
    finished = SimulatedProvider()
    await AnalyticsRunner(analytics.company, client=client, provider=finished).tick(AFTER_D3)
    first, again = pending.calls[0], finished.calls[0]
    assert (again.prompt, again.reasoning_effort, again.attachments) == (first.prompt, first.reasoning_effort,
                                                                         first.attachments)
    assert "새 제목" not in again.prompt and len(outbox(analytics)) == 1
