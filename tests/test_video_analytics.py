"""Video performance analytics: pure metric code. Real PostgreSQL tests live in test_video_analytics_store.py."""

from datetime import UTC, date, datetime

import pytest

from quant_company.video.analytics_metrics import (
    baseline,
    hook_retention,
    in_series,
    parse_reach_csv,
    parse_title,
    pt_day,
    scene_drops,
    window,
)


@pytest.mark.parametrize("title,published,expected", [
    ("엔비디아 4% 급등, 반도체 랠리 이어질까 | 10월 13일 아침 · 오늘의 증시story",
     datetime(2026, 10, 12, 22, 1, tzinfo=UTC), (date(2026, 10, 13), "am")),
    ("코스피 2% 하락, 외국인 매도 이유는? | 10월 12일 마감 · 오늘의 증시story",
     datetime(2026, 10, 12, 9, 0, tzinfo=UTC), (date(2026, 10, 12), "close")),
    # Published on New Year's morning for the 31 December close.
    ("연말 마감 정리 | 12월 31일 마감 · 오늘의 증시story",
     datetime(2026, 12, 31, 16, 30, tzinfo=UTC), (date(2026, 12, 31), "close")),
    ("연말 마감 정리 | 12월 31일 마감 · 오늘의 증시story",
     datetime(2027, 1, 1, 0, 30, tzinfo=UTC), (date(2026, 12, 31), "close")),
    # The format used until 12 October: series tag first, "브리핑" after the edition.
    ("[오늘의 증시story] 삼성전자 영업익 100조 첫 돌파에도 2%대 급락, 코스피 2.62% 하락 | 10월 8일 마감 브리핑",
     datetime(2026, 10, 8, 8, 36, tzinfo=UTC), (date(2026, 10, 8), "close")),
    ("[오늘의 증시story] 미국 10년물 금리 24년 만의 고점, 오늘 한국 증시는? | 10월 8일 아침 브리핑",
     datetime(2026, 10, 7, 21, 41, tzinfo=UTC), (date(2026, 10, 8), "am")),
])
def test_title_suffix_names_the_edition_day_and_kind(title, published, expected):
    assert parse_title(title, published) == expected


@pytest.mark.parametrize("title", [
    "[오늘의 증시story] 엔비디아 실적 발표 전 체크포인트",  # retired prefix of the hand-made episodes
    "엔비디아 4% 급등 | 13일 아침 · 오늘의 증시story",
    "Shorts: 오늘의 한 줄",
    "엔비디아 | 13월 40일 아침 · 오늘의 증시story",
    "반도체 매수는 어디로 갔나? | 10월 7일 마감 브리핑",  # edition without the series tag
    "[역사story] 한글날은 왜 10월 9일일까? | 10월 9일 아침 브리핑",
])
def test_titles_without_the_series_suffix_are_not_matched(title):
    assert parse_title(title, datetime(2026, 10, 13, tzinfo=UTC)) is None


@pytest.mark.parametrize("title,expected", [
    ("코스피 5.46% 급락, 대체 뭐 때문에 빠졌을까? | 10월 13일 마감 · 오늘의 증시story", True),
    ("[오늘의 증시story] 반도체 매수는 어디로 갔나? | 10월 7일 마감 브리핑", True),
    ("[오늘의 증시story] 엔비디아 실적 발표 전 체크포인트", True),
    ("[역사story] 한글날은 왜 10월 9일일까? 날짜를 바꾼 책 한 권", False),
    ("Shorts: 오늘의 한 줄", False),
])
def test_only_the_brief_series_is_measured(title, expected):
    assert in_series(title) is expected


# Watch ratio falls from 1.0 at the start to 0.6 at a quarter of the video, then to 0.45 at the end.
CURVE = [(0.0, 1.0), (0.25, 0.6), (1.0, 0.45)]


def test_hook_retention_interpolates_the_watch_ratio_at_thirty_seconds():
    # 30 s of 200 s is ratio 0.15: 1.0 - 0.4 * 0.15 / 0.25 = 0.76
    assert hook_retention(CURVE, 200) == pytest.approx(0.76)
    # A video shorter than the hook window has no hook figure.
    assert hook_retention(CURVE, 25) is None


def test_scene_drops_rank_the_steepest_scene_per_minute():
    scenes = [{"id": "s1", "type": "cold_open", "heading": "오늘의 질문", "t0": 0, "t1": 50},
              {"id": "s2", "type": "market_board", "heading": "시장판", "t0": 50, "t1": 200}]
    drops = scene_drops(CURVE, 200, scenes)
    assert [d["id"] for d in drops] == ["s1", "s2"]
    assert drops[0]["drop"] == pytest.approx(0.4)
    assert drops[0]["per_minute"] == pytest.approx(0.48)
    assert drops[1]["drop"] == pytest.approx(0.15)
    assert drops[1]["per_minute"] == pytest.approx(0.06)
    assert drops[0]["steepest"] is True and drops[1]["steepest"] is False


def test_chapters_stand_in_for_scene_timings_on_older_episodes():
    chapters = [{"start": 0, "title": "오늘의 질문"}, {"start": 50, "title": "시장판"}]
    drops = scene_drops(CURVE, 200, chapters=chapters)
    assert [(d["heading"], d["start"], d["end"]) for d in drops] == [("오늘의 질문", 0, 50), ("시장판", 50, 200)]


def test_watch_ratio_before_the_first_point_uses_the_first_value():
    # The API's first point is at 1% of the video; rewatched openings can exceed 1.0.
    curve = [(0.01, 1.2), (1.0, 0.5)]
    assert hook_retention(curve, 1000) == pytest.approx(1.2 - 0.7 * 0.02 / 0.99)
    opening = scene_drops(curve, 1000, [{"id": "a", "type": "x", "heading": "h", "t0": 0, "t1": 5}])
    assert opening[0]["drop"] == pytest.approx(0.0)


def test_baseline_needs_five_comparable_videos():
    assert baseline([0.05, 0.04, 0.06, 0.07]) is None
    assert baseline([0.05, 0.04, 0.06, 0.07, None]) is None
    assert baseline([0.05, 0.04, 0.06, 0.07, 0.30]) == pytest.approx(0.06)
    assert baseline([1, 2, 3, 4, 5, 6]) == pytest.approx(3.5)


def test_fixed_windows_use_the_pacific_publish_day():
    # 07:00 KST on 13 October is still 12 October in California.
    published = datetime(2026, 10, 12, 22, 0, tzinfo=UTC)
    assert pt_day(published) == date(2026, 10, 12)
    assert window(published, "d3") == (date(2026, 10, 12), date(2026, 10, 14))
    assert window(published, "d7") == (date(2026, 10, 12), date(2026, 10, 18))
    assert window(published, "d28") == (date(2026, 10, 12), date(2026, 11, 8))


BASIC = """date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr
20261012,UCfixture,aaaaaaaaaaa,1200,0.05
20261013,UCfixture,aaaaaaaaaaa,800,0.0375
20261013,UCfixture,bbbbbbbbbbb,40,0
"""

COMBINED = """date,channel_id,video_id,traffic_source_type,traffic_source_detail,operating_system,device_type,\
video_thumbnail_impressions,video_thumbnail_impressions_ctr
20261012,UCfixture,aaaaaaaaaaa,3,,1,1,900,0.04
20261012,UCfixture,aaaaaaaaaaa,3,,2,2,100,0.08
20261012,UCfixture,aaaaaaaaaaa,5,엔비디아 실적,1,1,200,0.1
"""


def test_basic_reach_rows_keep_impressions_and_click_ratio():
    rows = parse_reach_csv(BASIC, "channel_reach_basic_a1")
    assert rows == [
        {"day": date(2026, 10, 12), "video_id": "aaaaaaaaaaa", "source": "ALL", "impressions": 1200, "clicks": 60.0},
        {"day": date(2026, 10, 13), "video_id": "aaaaaaaaaaa", "source": "ALL", "impressions": 800, "clicks": 30.0},
        {"day": date(2026, 10, 13), "video_id": "bbbbbbbbbbb", "source": "ALL", "impressions": 40, "clicks": 0.0},
    ]


def test_combined_reach_rows_add_up_per_traffic_source():
    rows = parse_reach_csv(COMBINED, "channel_reach_combined_a1")
    assert rows == [
        {"day": date(2026, 10, 12), "video_id": "aaaaaaaaaaa", "source": "browse", "impressions": 1000, "clicks": 44.0},
        {"day": date(2026, 10, 12), "video_id": "aaaaaaaaaaa", "source": "search", "impressions": 200, "clicks": 20.0},
    ]


def test_percentage_scaled_reports_are_read_as_ratios():
    text = BASIC.replace(",0.05\n", ",5.0\n").replace(",0.0375\n", ",3.75\n")
    rows = parse_reach_csv(text, "channel_reach_basic_a1")
    assert [r["clicks"] for r in rows] == [60.0, 30.0, 0.0]


def test_unknown_report_shape_is_refused():
    with pytest.raises(ValueError):
        parse_reach_csv("date,video_id,views\n20261012,aaaaaaaaaaa,3\n", "channel_reach_basic_a1")


# ---- Analysis contracts and the production prompt --------------------------------------------------------------

TITLE_CHANGE = {"target": "title", "text": "엔비디아 실적 앞두고 반도체 강세 확산될까 | 9월 22일 아침 · 오늘의 증시story",
                "reason": "실제 검색어 '엔비디아 실적'이 제목에 없음"}


def performance_output(**extra):
    return {"summary": "노출은 기준 이상이지만 클릭률이 낮았습니다.", "strengths": ["검색 유입 비중이 높음"],
            "weaknesses": ["썸네일 문구가 제목을 반복함"],
            "hypotheses": [{"stage": "ctr", "claim": "썸네일 문구가 제목과 같아 새 정보가 없음",
                            "evidence": "CTR 3.1%, 아침 회차 중앙값 4.6%", "confidence": "medium"}],
            "changes": [TITLE_CHANGE, {"target": "tags", "text": "엔비디아 실적, 반도체 전망",
                                       "reason": "검색 유입 300회 중 '엔비디아 실적' 120회"}], **extra}


def test_performance_changes_are_paste_ready_studio_edits():
    from pydantic import ValidationError

    from quant_company.video.analytics_contracts import VideoPerformance

    output = VideoPerformance.model_validate(performance_output())
    assert [c.target for c in output.changes] == ["title", "tags"]
    assert VideoPerformance.model_validate(performance_output(changes=[])).changes == []
    for change in ({"target": "thumbnail"},  # an image cannot be pasted in Studio; lessons cover it
                   {"text": "엔비디아 실적 앞두고 반도체 강세 확산될까"},  # drops the series suffix
                   {"text": "가" * 80 + " | 9월 22일 아침 · 오늘의 증시story"},  # over YouTube's 100 characters
                   {"reason": ""}):
        with pytest.raises(ValidationError):
            VideoPerformance.model_validate(performance_output(changes=[{**TITLE_CHANGE, **change}]))
    with pytest.raises(ValidationError):
        VideoPerformance.model_validate(performance_output(changes=[TITLE_CHANGE] * 4))
    with pytest.raises(ValidationError):
        VideoPerformance.model_validate({k: v for k, v in performance_output().items() if k != "changes"})


def test_the_performance_prompt_asks_for_studio_edits_in_plain_words():
    from quant_company.video.analytics_contracts import performance_prompt

    prompt = performance_prompt({"video": {"title": "t"}})
    for rule in ("changes", "YouTube Studio", "100", "· 오늘의 증시story", "field names"):
        assert rule in prompt, rule


def test_performance_output_requires_a_staged_hypothesis():
    from pydantic import ValidationError

    from quant_company.video.analytics_contracts import VideoPerformance

    assert VideoPerformance.model_validate(performance_output()).hypotheses[0].stage == "ctr"
    with pytest.raises(ValidationError):
        VideoPerformance.model_validate(performance_output(hypotheses=[]))
    with pytest.raises(ValidationError):
        VideoPerformance.model_validate(performance_output(
            hypotheses=[{**performance_output()["hypotheses"][0], "stage": "revenue"}]))


def test_lesson_proposals_cite_episodes_and_stay_in_packaging_scope():
    from pydantic import ValidationError

    from quant_company.video.analytics_contracts import WeeklyReview

    proposal = {"text": "썸네일 문구는 제목에 없는 숫자 하나와 질문으로 쓴다", "scope": "thumbnail",
                "evidence": "CTR 상위 3편 모두 제목과 다른 썸네일 문구", "videos": ["aaaaaaaaaaa", "bbbbbbbbbbb"],
                "expected": "CTR", "confidence": "low"}
    review = {"summary": "이번 주는 아침 회차 노출이 늘었습니다.", "patterns": [], "top": [], "bottom": [],
              "proposals": [proposal], "verdicts": []}
    assert WeeklyReview.model_validate(review).proposals[0].scope == "thumbnail"
    for change in ({"scope": "facts"}, {"videos": []}, {"videos": ["no video id"]}, {"text": "짧음"}):
        with pytest.raises(ValidationError):
            WeeklyReview.model_validate({**review, "proposals": [{**proposal, **change}]})
    with pytest.raises(ValidationError):
        WeeklyReview.model_validate({**review, "proposals": [proposal] * 4})


def test_analysis_requests_are_tool_free_and_may_carry_thumbnails():
    from quant_company.contracts import ProviderAttachment, ProviderRequest

    image = ProviderAttachment(name="thumb-aaaaaaaaaaa.jpg", media_type="image/jpeg", data="aGVsbG8=")
    request = ProviderRequest(request_id="video-analytics-aaaaaaaaaaa-d3", model="claude-opus-5-5", prompt="p",
                              output_contract="video_performance_v1", attachments=[image])
    assert request.attachments == [image]
    ProviderRequest(request_id="video-analytics-weekly-2026-10-12", model="claude-opus-5-5", prompt="p",
                    output_contract="video_weekly_v1", attachments=[image])
    for bad in ({"request_id": "video-1234-d3"}, {"web_search": True}):
        with pytest.raises(ValueError):
            ProviderRequest(**{"request_id": "video-analytics-aaaaaaaaaaa-d3", "model": "claude-opus-5-5",
                               "prompt": "p", "output_contract": "video_performance_v1", **bad})
    with pytest.raises(ValueError):
        ProviderRequest(request_id="video-x-plan", model="claude-opus-5-5", prompt="p",
                        output_contract="video_episode_v1", attachments=[image])


def test_an_answer_is_text_with_optional_studio_edits_and_one_lesson():
    from pydantic import ValidationError

    from quant_company.video.analytics_contracts import VideoAnswer

    lesson = {"text": "썸네일 큰 글씨에 그날 핵심 종목명을 넣는다", "scope": "thumbnail", "evidence": "소유자 지시, 탐색 CTR 2.9%",
              "videos": ["aaaaaaaaaaa"], "expected": "CTR", "confidence": "low"}
    answer = {"answer": "썸네일 문구 후보입니다.", "changes": [TITLE_CHANGE], "lesson": None}
    assert VideoAnswer.model_validate(answer).changes[0].target == "title"
    assert VideoAnswer.model_validate({**answer, "lesson": lesson}).lesson.scope == "thumbnail"
    for bad in ({"answer": ""}, {"answer": "가" * 1501}, {"changes": [TITLE_CHANGE] * 4},
                {"lesson": {**lesson, "scope": "facts"}}, {"lesson": {**lesson, "videos": []}}):
        with pytest.raises(ValidationError):
            VideoAnswer.model_validate({**answer, **bad})


def test_answer_requests_are_tool_free_analytics_requests_with_thumbnails():
    from quant_company.contracts import ProviderAttachment, ProviderRequest

    image = ProviderAttachment(name="thumb-aaaaaaaaaaa.jpg", media_type="image/jpeg", data="aGVsbG8=")
    ProviderRequest(request_id="video-analytics-answer-1", model="claude-opus-5-5", prompt="p",
                    output_contract="video_answer_v1", attachments=[image])
    for bad in ({"request_id": "video-answer-1"}, {"web_search": True}):
        with pytest.raises(ValueError):
            ProviderRequest(**{"request_id": "video-analytics-answer-1", "model": "claude-opus-5-5", "prompt": "p",
                               "output_contract": "video_answer_v1", **bad})


def test_the_answer_prompt_keeps_the_card_rules_and_routes_instructions_to_lessons():
    from quant_company.video.analytics_contracts import answer_prompt

    prompt = answer_prompt({"question": "q"})
    for rule in ("thread", "lesson", "YouTube Studio", "field names", "1500"):
        assert rule in prompt, rule


def test_the_answer_prompt_keeps_replies_short_and_says_the_edits_once():
    # 2026-10-15: the first live instruction answer restated its Studio edits and lesson in prose above the list.
    from quant_company.video.analytics_contracts import answer_prompt

    prompt = answer_prompt({"question": "q"})
    for rule in ("600 characters", "shown under the answer", "do not describe, list or repeat them",
                 "no headings", "one line"):
        assert rule in prompt, rule


def test_claude_runtime_schema_covers_the_analysis_contracts():
    from quant_company.contracts import ProviderRequest
    from quant_company.providers.claude_runner import output_schema
    from quant_company.video.analytics_contracts import VideoAnswer, VideoPerformance, WeeklyReview

    for contract, cls in (("video_performance_v1", VideoPerformance), ("video_weekly_v1", WeeklyReview),
                          ("video_answer_v1", VideoAnswer)):
        request = ProviderRequest(request_id="video-analytics-weekly-2026-10-12", model="claude-opus-5-5",
                                  prompt="p", output_contract=contract)
        assert output_schema(request)[0] is cls


def episode_source():
    from .test_video import body_source

    return body_source()


def test_lessons_enter_the_episode_prompt_as_ranked_data():
    import json

    from quant_company.video.episode import episode_prompt

    lessons = [{"id": "11111111-1111-1111-1111-111111111111", "scope": "thumbnail",
                "text": "썸네일 문구는 제목에 없는 숫자 하나와 질문으로 쓴다"}]
    plain = episode_prompt(episode_source(), "", {})
    assert episode_prompt(episode_source(), "", {}, lessons=()) == plain
    assert "channel_lessons" not in plain
    taught = episode_prompt(episode_source(), "", {}, lessons=lessons)
    data = json.loads(taught[taught.index('{"day"'):])
    assert data["channel_lessons"] == ["[thumbnail] 썸네일 문구는 제목에 없는 숫자 하나와 질문으로 쓴다"]
    # The lesson rule says the fixed rules win; it sits outside the data block.
    rule = taught[:taught.index('{"day"')]
    assert "channel_lessons" in rule and "take precedence" in rule


ANALYTICS = dict(briefing_enabled=True, briefing_publish_enabled=True, briefing_channel_id="CBRIEF",
                 briefing_owner_user="UOWNER", slack_allowed_users=["UOWNER"], slack_allowed_channels=["CBRIEF", "CPERF"],
                 video_youtube_channel_id="UCfixture", video_analytics_enabled=True, video_analytics_channel_id="CPERF")


def test_analytics_settings_need_the_channel_owner_and_a_dedicated_slack_channel():
    from quant_company.config import Settings

    assert Settings(**ANALYTICS).video_analytics_max_lessons == 8
    for change in ({"video_youtube_channel_id": ""}, {"video_analytics_channel_id": ""},
                   {"video_analytics_channel_id": "CBRIEF"}, {"slack_allowed_channels": ["CBRIEF"]},
                   {"slack_allowed_users": []}, {"video_analytics_channel_id": "DPERF"}):
        with pytest.raises(ValueError):
            Settings(**{**ANALYTICS, **change})


def test_manifest_scene_timings_name_each_card():
    from quant_company.video.motion import scene_timings

    scenes = [{"id": "open", "chapter": "opening", "type": "cold_open", "narration": "n",
               "data": {"question": "반도체 강세, 어디까지 갈까", "kicker": "k"}, "t0": 0.0, "t1": 12.4},
              {"id": "board", "chapter": "market", "type": "market_board", "narration": "n",
               "data": {"title": "시장 한눈에 보기"}, "t0": 12.4, "t1": 30.0},
              {"id": "sig", "chapter": "signals", "type": "signals", "narration": "n", "data": {}, "t0": 30.0, "t1": 41.5}]
    assert scene_timings(scenes) == [
        {"id": "open", "type": "cold_open", "heading": "반도체 강세, 어디까지 갈까", "t0": 0.0, "t1": 12.4},
        {"id": "board", "type": "market_board", "heading": "시장 한눈에 보기", "t0": 12.4, "t1": 30.0},
        {"id": "sig", "type": "signals", "heading": "signals", "t0": 30.0, "t1": 41.5}]


def test_lesson_buttons_turn_on_analyst_interactivity(tmp_path):
    import json

    from quant_company.cli import manifests
    from quant_company.company import Company
    from quant_company.config import Settings

    manifests(Company(Settings(**ANALYTICS)), None, tmp_path, include_market_brief=True)
    assert json.loads((tmp_path / "market_brief.json").read_text())["settings"]["interactivity"] == {"is_enabled": True}


def test_a_suggested_title_must_still_name_the_uploads_own_edition():
    from quant_company.video.analytics import usable_changes

    legacy = {"title": "[오늘의 증시story] 삼성전자 영업익 100조 첫 돌파에도 2%대 급락 | 10월 8일 마감 브리핑",
              "published_at": datetime(2026, 10, 8, 8, 36, tzinfo=UTC), "edition_kind": "close"}
    same = {"target": "title", "text": "삼성전자 영업익 100조 첫 돌파에도 2%대 급락 | 10월 8일 마감 · 오늘의 증시story",
            "reason": "앞 20자가 시리즈 이름"}
    tags = {"target": "tags", "text": "삼성전자 3분기 실적", "reason": "검색어 '삼성3분기실적'"}
    assert usable_changes(legacy, [same, tags]) == [same, tags]  # the older format moves to the current one
    for title in ("삼성전자 급락 | 10월 9일 마감 · 오늘의 증시story", "삼성전자 급락 | 10월 8일 아침 · 오늘의 증시story"):
        assert usable_changes(legacy, [{**same, "text": title}, tags]) == [tags], title
    current = {**legacy, "title": same["text"]}
    assert usable_changes(current, [same]) == []  # no change at all
