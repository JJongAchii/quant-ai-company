"""The owner's replies in analytics threads, answered by claude-runtime in the same thread. Real PostgreSQL; Slack
events, YouTube reads and claude-runtime are explicitly simulated fixtures."""

import json

import pytest

from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.slack import SlackIngress
from quant_company.video.analytics import AnalyticsRunner
from quant_company.video.analytics_slack import blocks

from .test_briefing import brief  # noqa: F401
from .test_video import video  # noqa: F401
from .test_video_analytics_runner import (
    AFTER_D3,
    CREDENTIAL,
    NEW_TITLE,
    SimulatedProvider,
    deliver,
    lesson_click,
    linked_upload,
    outbox,
    weekly_posted,
)
from .test_video_analytics_store import analytics  # noqa: F401

QUESTION = "썸네일 문구 후보 3개만 더 줘"


def answer_output(**extra):
    return {"answer": "썸네일 문구 후보입니다.\n1. 엔비디아 실적 앞두고 반도체는?\n2. 반도체 강세, 어디까지\n3. 실적 주간 첫날",
            "changes": [{"target": "title", "text": NEW_TITLE, "reason": "검색어 '엔비디아 실적'"}], "lesson": None, **extra}


class AnsweringProvider(SimulatedProvider):
    def __init__(self, answer=None, **kwargs):
        super().__init__(**kwargs)
        self.answer = answer or answer_output()

    async def run(self, request):
        if request.output_contract != "video_answer_v1":
            return await super().run(request)
        self.calls.append(request)
        return ProviderResponse(request_id=request.request_id, provider="claude", usage={"actual_model": request.model},
                                decision=AgentDecision(status="complete", say="", artifacts=[
                                    {"title": "Simulated", "content": json.dumps(self.answer, ensure_ascii=False)}]))


def reply(analytics, text=QUESTION, *, ts="300.200", thread_ts="300.001", user="UHUMAN", role="market_brief",  # noqa: F811
          channel="CPERF"):
    event = {"type": "message", "user": user, "channel": channel, "ts": ts, "text": text, "channel_type": "channel"}
    if thread_ts:
        event["thread_ts"] = thread_ts
    payload = {"type": "event_callback", "team_id": "TTEST", "api_app_id": "AVIDEO", "event_id": "EV" + ts,
               "event": event}
    return SlackIngress(analytics.settings, analytics.company, {role: CREDENTIAL}).accept(role, payload, CREDENTIAL)


async def card_delivered(brief, video, analytics, provider):  # noqa: F811
    job, client = linked_upload(brief, video)
    runner = AnalyticsRunner(analytics.company, client=client, provider=provider)
    await runner.tick(AFTER_D3)
    deliver(analytics, outbox(analytics)[0], "300.001")
    return job, runner


async def test_an_owner_reply_under_a_card_is_answered_in_its_thread(brief, video, analytics):  # noqa: F811
    provider = AnsweringProvider()
    _, runner = await card_delivered(brief, video, analytics, provider)
    asked = reply(analytics)
    assert asked["video_question"] and asked["duplicate"] is False
    assert reply(analytics)["duplicate"] is True  # Slack retries the event
    assert analytics.unstarted_questions() == [asked["video_question"]]
    assert await runner.answer(asked["video_question"]) == "answered"
    request = provider.calls[-1]
    assert (request.output_contract, request.request_id) == ("video_answer_v1",
                                                            f"video-analytics-answer-{asked['video_question']}")
    assert [a.media_type for a in request.attachments] == ["image/jpeg"]  # the thumbnail viewers saw
    for piece in (QUESTION, "썸네일 문구가 제목을 반복해", '"impressions": 1500', "반도체 강세 확산될까"):
        assert piece in request.prompt, piece
    answer = outbox(analytics)[-1]
    assert answer["thread_ts"] == "300.001" and answer["agent"] == "market_brief"
    assert "썸네일 문구 후보입니다." in answer["text"] and f"1. 제목 → `{NEW_TITLE}`" in answer["text"]
    calls = len(provider.calls)
    assert await runner.answer(asked["video_question"]) == "closed" and len(provider.calls) == calls
    assert analytics.question(asked["video_question"])["state"] == "answered"


async def test_a_follow_up_question_sees_the_earlier_answer(brief, video, analytics):  # noqa: F811
    provider = AnsweringProvider()
    _, runner = await card_delivered(brief, video, analytics, provider)
    first = reply(analytics)["video_question"]
    await runner.answer(first)
    second = reply(analytics, "2번으로 하면 제목은 어떻게 바꿔?", ts="300.300")["video_question"]
    await runner.answer(second)
    prompt = provider.calls[-1].prompt
    assert QUESTION in prompt and "엔비디아 실적 앞두고 반도체는?" in prompt and "2번으로 하면" in prompt


async def test_a_question_waits_while_an_episode_is_in_production(brief, video, analytics):  # noqa: F811
    store, _ = video
    provider = AnsweringProvider()
    job, runner = await card_delivered(brief, video, analytics, provider)
    store.save(store.get(job["id"]), "rendering")
    question = reply(analytics)["video_question"]
    assert await runner.answer(question) == "waiting"
    assert await runner.answer(question) == "waiting"
    notices = [r for r in outbox(analytics) if r["thread_ts"] == "300.001"]
    assert len(notices) == 1 and "제작" in notices[0]["text"]  # said once
    assert not [c for c in provider.calls if c.output_contract == "video_answer_v1"]
    store.save(store.get(job["id"]), "delivered")
    assert await runner.answer(question) == "answered"


async def test_a_question_left_unanswered_for_hours_says_so(brief, video, analytics):  # noqa: F811
    _, runner = await card_delivered(brief, video, analytics, AnsweringProvider())
    question = reply(analytics)["video_question"]
    await runner.give_up(question)
    last = outbox(analytics)[-1]
    assert last["thread_ts"] == "300.001" and "다시" in last["text"]
    assert await runner.answer(question) == "closed"


@pytest.mark.parametrize("change", [{"user": "UOTHER"}, {"thread_ts": None}, {"thread_ts": "999.999"},
                                    {"role": "director"}, {"channel": "CQUANT"}])
async def test_replies_outside_a_delivered_analytics_thread_are_not_questions(brief, video, analytics, change):  # noqa: F811
    await card_delivered(brief, video, analytics, AnsweringProvider())
    if change.get("user"):
        analytics.settings.slack_allowed_users = [*analytics.settings.slack_allowed_users, change["user"]]
    result = reply(analytics, **change)
    assert "video_question" not in result
    assert analytics.unstarted_questions() == []


async def test_an_instruction_comes_back_as_a_lesson_with_owner_buttons(brief, video, analytics):  # noqa: F811
    lesson = {"text": "썸네일 큰 글씨에 그날 핵심 종목명을 넣는다", "scope": "thumbnail",
              "evidence": "소유자 지시, 이 회차 탐색 CTR 2.9%", "videos": ["aaaaaaaaaaa"], "expected": "CTR",
              "confidence": "low"}
    provider = AnsweringProvider(answer_output(answer="다음 회차부터 반영할 교훈으로 제안합니다.", changes=[],
                                               lesson=lesson))
    _, runner = await card_delivered(brief, video, analytics, provider)
    question = reply(analytics, "다음 영상부터 썸네일에 회사명 넣어")["video_question"]
    assert await runner.answer(question) == "answered"
    [proposed] = analytics.lessons(["proposed"])
    assert proposed["text"] == lesson["text"]
    answer = outbox(analytics)[-1]
    assert "교훈 제안" in answer["text"]
    rendered = blocks(analytics.company, answer)
    actions = [e["action_id"] for b in rendered if b["type"] == "actions" for e in b["elements"]]
    assert actions == ["video_lesson_adopt", "video_lesson_reject"]
    deliver(analytics, answer, "400.001")
    result = lesson_click(analytics, str(proposed["id"]), str(answer["id"]))
    assert result["outcome"] == "active" and analytics.active_lessons()[-1]["text"] == lesson["text"]
    assert outbox(analytics)[-1]["thread_ts"] == "300.001" and "채택" in outbox(analytics)[-1]["text"]


async def test_a_lesson_citing_an_unknown_video_is_dropped(brief, video, analytics):  # noqa: F811
    lesson = {"text": "썸네일 큰 글씨에 그날 핵심 종목명을 넣는다", "scope": "thumbnail",
              "evidence": "채널에 없는 영상을 근거로 들었음", "videos": ["zzzzzzzzzzz"], "expected": "CTR", "confidence": "low"}
    provider = AnsweringProvider(answer_output(lesson=lesson))
    _, runner = await card_delivered(brief, video, analytics, provider)
    await runner.answer(reply(analytics)["video_question"])
    assert analytics.lessons(["proposed"]) == [] and "교훈 제안" not in outbox(analytics)[-1]["text"]


async def test_a_question_under_the_weekly_review_is_answered_from_the_review(brief, video, analytics):  # noqa: F811
    runner, provider, _ = await weekly_posted(brief, video, analytics)
    review = outbox(analytics)[-1]
    deliver(analytics, review, "500.001")
    answering = AnsweringProvider(weekly=provider.weekly)
    runner = AnalyticsRunner(analytics.company, client=runner.client, provider=answering)
    question = reply(analytics, "이번 주 제일 아쉬운 점만 짧게", thread_ts="500.001", ts="500.100")["video_question"]
    assert await runner.answer(question) == "answered"
    request = answering.calls[-1]
    assert "아침 회차는 검색 유입이 늘었고" in request.prompt and request.attachments
    assert '"report_day": "2026-09-28"' in request.prompt and '"figures": {' in request.prompt
    answer = outbox(analytics)[-1]
    # A title is pasted into one upload; the weekly thread names no single upload, so no Studio edits there.
    assert answer["thread_ts"] == "500.001" and "✏️" not in answer["text"]
