"""Real PostgreSQL; YouTube reads, claude-runtime and Slack are explicitly simulated fixtures."""

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from psycopg.types.json import Jsonb

from quant_company.company import PolicyError
from quant_company.contracts import AgentDecision, ProviderFault, ProviderResponse
from quant_company.slack import SlackIngress
from quant_company.video.analytics import AnalyticsRunner, shorts_summary
from quant_company.video.analytics_metrics import watch_ratio
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

AFTER_D3 = datetime(2026, 9, 25, 12, tzinfo=UTC)  # Pacific 25 September; the d3 window ended on the 23rd
MONDAY = datetime(2026, 9, 28, 0, 30, tzinfo=UTC)  # 09:30 KST, Monday 28 September
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


def linked_upload(brief, video):  # noqa: F811
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
    # Friday 23 October (KST): no weekly review is due; d28 (21 Sep - 18 Oct) still waits for reach data.
    await AnalyticsRunner(analytics.company, client=client, provider=provider).tick(datetime(2026, 10, 23, tzinfo=UTC))
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


def weekly_output(active_id):
    return {"summary": "아침 회차는 검색 유입이 늘었고 CTR은 기준 부족입니다.", "patterns": ["질문형 썸네일이 반복됨"],
            "top": [{"youtube_id": "aaaaaaaaaaa", "reason": "검색어와 제목이 일치"}], "bottom": [],
            "proposals": [{"text": "썸네일 문구는 제목에 없는 숫자 하나와 질문으로 쓴다", "scope": "thumbnail",
                           "evidence": "검색 유입이 높은 회차의 공통점", "videos": ["aaaaaaaaaaa"], "expected": "CTR",
                           "confidence": "low"},
                          {"text": "존재하지 않는 영상을 근거로 든 제안입니다", "scope": "title",
                           "evidence": "채널에 없는 영상을 근거로 들었음", "videos": ["zzzzzzzzzzz"], "expected": "CTR", "confidence": "low"}],
            "verdicts": [{"lesson_id": active_id, "verdict": "too_early", "reason": "적용 회차가 1편"},
                         {"lesson_id": "00000000-0000-0000-0000-000000000000", "verdict": "retire",
                          "reason": "없는 교훈"}]}


async def weekly_posted(brief, video, analytics):  # noqa: F811
    _, client = linked_upload(brief, video)
    [active] = seed_lessons(analytics, ["active"])
    provider = SimulatedProvider(weekly=weekly_output(active))
    runner = AnalyticsRunner(analytics.company, client=client, provider=provider)
    await runner.tick(MONDAY)
    return runner, provider, active


async def test_the_monday_review_proposes_cited_lessons_with_owner_buttons(brief, video, analytics):  # noqa: F811
    runner, provider, active = await weekly_posted(brief, video, analytics)
    weekly = next(c for c in provider.calls if c.output_contract == "video_weekly_v1")
    assert weekly.request_id == "video-analytics-weekly-2026-09-28" and weekly.attachments
    assert "aaaaaaaaaaa" in weekly.prompt and active in weekly.prompt
    [proposed] = analytics.lessons(["proposed"])
    assert proposed["text"] == "썸네일 문구는 제목에 없는 숫자 하나와 질문으로 쓴다"
    review = outbox(analytics)[-1]
    assert "주간 성과 리뷰" in review["text"] and "판단 이르음" in review["text"] and "없는 교훈" not in review["text"]
    rendered = blocks(analytics.company, review)
    actions = [e["action_id"] for b in rendered if b["type"] == "actions" for e in b["elements"]]
    assert actions == ["video_lesson_adopt", "video_lesson_reject", "video_lesson_retire"]
    await runner.tick(MONDAY)
    assert sum(c.output_contract == "video_weekly_v1" for c in provider.calls) == 1


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
    [proposed] = analytics.lessons(["proposed"])
    result = lesson_click(analytics, str(proposed["id"]), str(review["id"]))
    assert result["outcome"] == "active" and len(analytics.active_lessons()) == 2
    assert lesson_click(analytics, str(proposed["id"]), str(review["id"]))["outcome"] == "active"  # Slack retry
    reply = outbox(analytics)[-1]
    assert reply["thread_ts"] == "400.001" and "채택" in reply["text"]
    assert len([r for r in outbox(analytics) if r["thread_ts"] == "400.001"]) == 1


@pytest.mark.parametrize("change", [{"user.id": "UOTHER"}, {"channel.id": "CQUANT"}, {"container.channel_id": "CQUANT"},
                                    {"container.message_ts": "999.999"}, {"message.user": "UFORGED"},
                                    {"actions.0.text": {"type": "plain_text", "text": "거절"}},
                                    {"actions.0.block_id": "video-lesson:other"}])
async def test_forged_lesson_clicks_are_refused(brief, video, analytics, change):  # noqa: F811
    await weekly_posted(brief, video, analytics)
    review = outbox(analytics)[-1]
    deliver(analytics, review, "400.001")
    [proposed] = analytics.lessons(["proposed"])
    with pytest.raises(PolicyError):
        lesson_click(analytics, str(proposed["id"]), str(review["id"]), **change)
    assert analytics.lessons(["proposed"])


async def test_a_click_on_an_undelivered_or_foreign_message_is_refused(brief, video, analytics):  # noqa: F811
    await weekly_posted(brief, video, analytics)
    review = outbox(analytics)[-1]
    [proposed] = analytics.lessons(["proposed"])
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
    assert actions == ["video_lesson_adopt", "video_lesson_reject", "video_lesson_retire"]
    analytics.post("aaaaaaaaaaa", "aaaaaaaaaaa:extra", "카드", "추가 글")
    analytics.settings.video_analytics_enabled = False
    assert not await slack.send_one()
    assert len(sent) == 2


async def test_dry_run_sync_reads_youtube_and_writes_nothing(brief, video, analytics):  # noqa: F811
    _, client = linked_upload(brief, video)
    client.jobs = {}
    preview = await AnalyticsRunner(analytics.company, client=client, provider=SimulatedProvider()).preview(AFTER_D3)
    assert preview["report_jobs"] == {} and client.created == []
    [item] = preview["uploads"]
    assert (item["youtube_id"], item["edition"], item["day"]) == ("aaaaaaaaaaa", "am", "2026-09-22")
    assert preview["latest_window"]["metrics"]["totals"]["views"] == 820
    assert analytics.publications() == [] and not outbox(analytics)


async def test_dry_run_skips_other_series_for_the_latest_window(brief, video, analytics):  # noqa: F811
    _, client = linked_upload(brief, video)
    client.items.insert(0, upload("hhhhhhhhhhh", "[역사story] 한글날은 왜 10월 9일일까?", PUBLISHED.replace(day=22)))
    preview = await AnalyticsRunner(analytics.company, client=client, provider=SimulatedProvider()).preview(AFTER_D3)
    assert [(i["youtube_id"], i["series"]) for i in preview["uploads"]] == [("hhhhhhhhhhh", False), ("aaaaaaaaaaa", True)]
    assert preview["latest_window"]["youtube_id"] == "aaaaaaaaaaa"


async def test_status_counts_links_reports_snapshots_and_lessons(brief, video, analytics):  # noqa: F811
    await weekly_posted(brief, video, analytics)
    status = analytics.status()
    assert status["publications"] == {"total": 1, "linked": 1, "other_series": 0, "unlinked": []}
    assert status["latest_reach_day"] == "2026-09-23" and status["snapshots"] == {"d3": 1}
    assert status["lessons"] == {"active": 1, "proposed": 1}
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
