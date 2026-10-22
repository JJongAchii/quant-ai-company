"""Video performance analytics: pure metric code. Real PostgreSQL tests live in test_video_analytics_store.py."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from quant_company.video.analytics import (
    compact,
    next_report_day,
    previous_report_day,
    report_day,
    report_start,
)
from quant_company.video.analytics_metrics import (
    BENCHMARK_CATEGORIES,
    BENCHMARK_PICK,
    YOUNG_CHANNEL_DAYS,
    baseline,
    benchmark_pick,
    comparisons,
    hook_retention,
    in_series,
    outlier,
    parse_reach_csv,
    parse_title,
    pt_day,
    report_stats,
    scene_drops,
    search_terms,
    shorts_pairs,
    title_hook,
    traffic_shares,
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
    assert baseline([1, 2, 3], minimum=3) == 2 and baseline([1, 2, None], minimum=3) is None


def test_an_outlier_is_far_from_its_same_kind_median():
    base = {"views": 300, "ctr": 0.04, "hook_30s": 0.8, "n": 3}
    assert outlier({"views": 820, "ctr": 0.04, "hook_30s": 0.8}, base) == [{"metric": "views", "ratio": 2.73}]
    assert outlier({"views": 150}, base) == [{"metric": "views", "ratio": 0.5}]
    assert outlier({"views": 160, "ctr": 0.06}, base) == [{"metric": "ctr", "ratio": 1.5}]
    assert outlier({"ctr": 0.02}, {**base, "ctr": 0.03}) == [{"metric": "ctr", "ratio": 0.67}]  # 2/3 in floats
    assert outlier({"hook_30s": 0.616}, base) == [{"metric": "hook_30s", "ratio": 0.77}]
    assert outlier({"views": 820}, {**base, "n": 2}) == []  # fewer than three same-kind episodes
    assert outlier({"views": 500, "ctr": 0.05, "hook_30s": 0.9}, base) == []
    assert outlier({"views": 820, "ctr": 0.1}, {**base, "views": None, "ctr": 0}) == []  # no median to compare with


# ---- The channel strategy report: schedule and figures ---------------------------------------------------------

KST_ = ZoneInfo("Asia/Seoul")


def at(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=KST_)


def test_reports_fall_on_monday_and_thursday_mornings_with_two_days_to_catch_up():
    assert report_day(at(2026, 9, 28, 9)) == date(2026, 9, 28)
    assert report_day(at(2026, 9, 28, 8, 59)) is None
    assert report_day(at(2026, 9, 30, 8, 59)) == date(2026, 9, 28) and report_day(at(2026, 9, 30, 9)) is None
    assert report_day(at(2026, 10, 1, 9)) == date(2026, 10, 1) and report_day(at(2026, 10, 3, 8, 59)) == date(2026, 10, 1)
    assert report_day(at(2026, 10, 3, 9)) is None and report_day(at(2026, 10, 4, 12)) is None
    assert previous_report_day(date(2026, 10, 1)) == date(2026, 9, 28) and previous_report_day(date(2026, 9, 28)) == date(2026, 9, 24)
    assert next_report_day(date(2026, 9, 28)) == date(2026, 10, 1) and next_report_day(date(2026, 10, 1)) == date(2026, 10, 5)
    assert report_start(date(2026, 9, 28)) == at(2026, 9, 28, 9)
    assert report_day(datetime(2026, 9, 28, 0, 30, tzinfo=UTC)) == date(2026, 9, 28)  # any time zone


START, PREVIOUS, BEFORE = at(2026, 9, 28, 9), at(2026, 9, 24, 9), at(2026, 9, 21, 9)
AM_TITLE = "반도체 강세 확산될까 | 9월 22일 아침 · 오늘의 증시story"
AM_PUBLISHED, D3_TAKEN = at(2026, 9, 22, 7), at(2026, 9, 26, 21)


def report_row(youtube_id, title=AM_TITLE, kind="am", job=None, published=AM_PUBLISHED, taken=D3_TAKEN,
               report=True, plan=None, craft=1, lessons=(), duration_s=292, metrics=None, window_end=date(2026, 9, 23),
               **d3):
    """An episodes() row as the report reads it: the d3 snapshot (taken at `taken`) and the latest metrics."""
    d3 = {"views": 300, "report": report, **d3}
    return {"youtube_id": youtube_id, "title": title, "edition_kind": kind, "job_id": job, "published_at": published,
            "duration_s": duration_s, "plan": {"thumbnail": "반도체 질주"} if plan is None else plan, "craft": craft,
            "lessons": list(lessons), "d3": d3, "d3_taken": taken, "window_end": window_end,
            "metrics": d3 if metrics is None else metrics}


def test_report_figures_split_episodes_and_shorts_and_compare_periods():
    rows = [report_row("aaaaaaaaaaa", views=300, impressions=1000, ctr=0.05, hook_30s=0.6, avg_view_pct=40, subscribers=2),
            report_row("bbbbbbbbbbb", views=500, impressions=3000, ctr=0.03, hook_30s=0.8, avg_view_pct=50, subscribers=3,
                       window_end=date(2026, 9, 25)),
            report_row("sssssssssss", "반도체 5% 급등, 이유는 하나", kind="shorts", views=900, subscribers=4),
            report_row("ccccccccccc", "마감 | 9월 21일 마감 · 오늘의 증시story", kind="close", taken=at(2026, 9, 23, 21),
                       views=200, subscribers=1),
            # Measured in this period but long after its window (a backfill): baselines only.
            report_row("ddddddddddd", taken=at(2026, 9, 27, 21), report=False, views=5000, subscribers=50)]
    stats = report_stats(rows, START, PREVIOUS, BEFORE)
    assert stats["this"]["episodes"] == {"n": 2, "views": 800, "subscribers": 5, "impressions": 4000,
                                         "ctr": pytest.approx(0.035), "hook_30s": pytest.approx(0.7),
                                         "avg_view_pct": pytest.approx(45)}
    assert stats["this"]["shorts"]["views"] == 900 and stats["this"]["shorts"]["n"] == 1
    assert stats["previous"]["episodes"]["n"] == 1 and stats["previous"]["episodes"]["views"] == 200
    assert stats["previous"]["episodes"]["impressions"] is None and stats["previous"]["shorts"]["n"] == 0
    assert stats["new"] == ["aaaaaaaaaaa", "bbbbbbbbbbb", "sssssssssss"]  # not the backfill
    assert stats["data_day"] == "2026-09-25"
    assert set(stats) == {"data_day", "new", "this", "previous", "traffic", "search_terms", "shorts_pairs",
                          "comparisons"}
    import json

    json.dumps(stats)  # frozen with the request as it is
    from quant_company.video.analytics_slack import figure_lines

    assert figure_lines(stats)[1:] == [
        "본편 2편 · 조회 800 (200) · 구독 +5 (+1) · 노출 4,000 (–) · CTR 3.5% (–) · 30초 유지 70% (–) · 평균 시청 45% (–)",
        "Shorts 1편 · 조회 900 (–) · 구독 +4 (–)"]  # a metric measured in neither period is left out
    empty = {"episodes": {"n": 0}, "shorts": {"n": 0}}
    assert figure_lines({"this": empty, "previous": empty})[1:] == [
        "본편 · 이번 기간 새로 측정한 영상 없음", "Shorts · 이번 기간 새로 측정한 영상 없음"]


def test_search_terms_and_traffic_shares_add_up_across_videos():
    rows = [report_row("aaaaaaaaaaa", traffic=[{"source": "SUBSCRIBER", "views": 400}, {"source": "YT_SEARCH", "views": 300}],
                       metrics={"search_terms": [{"term": "엔비디아 실적", "views": 120}, {"term": "코스피", "views": 150}]}),
            report_row("bbbbbbbbbbb", traffic=[{"source": "SUBSCRIBER", "views": 100}, {"source": "SHORTS", "views": 200}],
                       metrics={"search_terms": [{"term": "엔비디아 실적", "views": 80}]}),
            {**report_row("ccccccccccc"), "metrics": None}]  # no snapshot yet
    assert search_terms(rows) == [{"term": "엔비디아 실적", "views": 200, "videos": 2},
                                  {"term": "코스피", "views": 150, "videos": 1}]
    assert search_terms(rows, limit=1) == [{"term": "엔비디아 실적", "views": 200, "videos": 2}]
    shares = traffic_shares(rows)
    assert shares == {"SUBSCRIBER": 0.5, "YT_SEARCH": 0.3, "SHORTS": 0.2} and list(shares) == ["SUBSCRIBER", "YT_SEARCH",
                                                                                              "SHORTS"]
    assert traffic_shares([]) == {}


def test_shorts_pair_with_the_episode_of_their_job():
    rows = [report_row("eeeeeeeeeee", job="j0", published=at(2026, 9, 21, 7), views=100),  # before any pair
            report_row("aaaaaaaaaaa", job="j1", views=300, traffic=[{"source": "SHORTS", "views": 30},
                                                                    {"source": "SUBSCRIBER", "views": 270}]),
            report_row("sssssssssss", "반도체 5% 급등, 이유는 하나", kind="shorts", job="j1",
                       published=at(2026, 9, 22, 10), views=900, subscribers=4),
            report_row("bbbbbbbbbbb", job="j2", published=at(2026, 9, 23, 7), views=500),
            report_row("ccccccccccc", published=at(2026, 9, 24, 7), views=700)]  # unlinked: no Shorts known either
    assert shorts_pairs(rows) == {
        "pairs": [{"day": "2026-09-22", "shorts": "sssssssssss", "episode": "aaaaaaaaaaa", "shorts_views": 900,
                   "shorts_subscribers": 4, "episode_views": 300, "episode_traffic": {"SUBSCRIBER": 0.9, "SHORTS": 0.1}}],
        "with_shorts": {"n": 1, "views": 300}, "without": {"n": 2, "views": 600}}
    assert shorts_pairs(rows[:1]) == {"pairs": [], "with_shorts": {"n": 0, "views": None},
                                      "without": {"n": 0, "views": None}}


def test_comparisons_count_each_side():
    rows = [report_row("aaaaaaaaaaa", "반도체 왜 올랐나? | 9월 22일 아침 · 오늘의 증시story", views=600, duration_s=300),
            report_row("bbbbbbbbbbb", "[오늘의 증시story] 코스피 2% 하락, 이유는? | 9월 23일 마감 브리핑", kind="close",
                       views=400, plan={"thumbnail": "외국인 매도 폭탄, 코스피 어디까지"}, duration_s=200),
            report_row("ccccccccccc", "반도체 강세 확산 | 9월 24일 아침 · 오늘의 증시story", views=200, plan={},
                       craft=None, duration_s=280),
            report_row("ddddddddddd", "엔비디아 실적 발표 | 9월 25일 아침 · 오늘의 증시story", views=100,
                       lessons=["l1"], duration_s=250),
            report_row("sssssssssss", "반도체 5% 급등, 이유는?", kind="shorts", views=900)]  # Shorts are their own kind
    rows.append({**report_row("eeeeeeeeeee"), "d3": None})  # not measured at three days
    sides = {c["feature"]: c for c in comparisons(rows)}
    assert list(sides) == ["question_title", "number_in_title", "short_thumbnail", "close_edition", "long", "craft",
                           "lessons"]
    assert (sides["question_title"]["with"]["n"], sides["question_title"]["without"]["n"]) == (2, 2)
    assert sides["question_title"]["with"]["views"] == 500 and sides["question_title"]["without"]["views"] == 150
    assert (sides["number_in_title"]["with"]["n"], sides["number_in_title"]["without"]["n"]) == (1, 3)
    assert (sides["short_thumbnail"]["with"]["n"], sides["short_thumbnail"]["without"]["n"]) == (2, 1)  # c has none
    assert (sides["long"]["with"]["n"], sides["craft"]["without"]["n"], sides["lessons"]["with"]["n"]) == (2, 1, 1)
    assert set(sides["close_edition"]["with"]) == {"n", "views", "ctr", "hook_30s", "avg_view_pct"}
    assert title_hook("[오늘의 증시story] 코스피 2% 하락, 이유는? | 9월 23일 마감 브리핑") == "코스피 2% 하락, 이유는?"


def test_compact_names_the_gentlest_scenes_beside_the_steepest():
    drops = [{"heading": h, "type": "x", "start": n, "end": n + 1, "per_minute": p, "drop": 0, "id": h, "steepest": False}
             for n, (h, p) in enumerate((("a", 0.5), ("b", 0.1), ("c", 0.3), ("d", 0.05), ("e", 0.2)))]
    kept = compact({"scene_drops": drops})
    assert [d["heading"] for d in kept["steepest_scenes"]] == ["a", "c", "e"]
    assert [d["heading"] for d in kept["gentlest_scenes"]] == ["d", "b", "e"]
    assert set(kept["gentlest_scenes"][0]) == {"heading", "type", "start", "end", "per_minute"}


def candidate(youtube_id, channel, views):
    return {"youtube_id": youtube_id, "channel_id": channel, "channel_title": "채널 " + channel, "title": "인기 영상",
            "views": views, "likes": None, "comments": 3}


def test_benchmark_picks_views_per_subscriber_and_young_channels_first():
    a, b, c, c2, d, own, gone, unknown = (f"{x}{'v' * 10}" for x in "abcCdoxu")
    at_ = datetime(2026, 9, 28, tzinfo=UTC)
    old, young = datetime(2015, 1, 1, tzinfo=UTC), datetime(2026, 6, 20, tzinfo=UTC)  # 100 days before
    channels = {"UCa": {"subscribers": 1_000_000, "created": old}, "UCb": {"subscribers": 10_000, "created": young},
                "UCc": {"subscribers": 1_000, "created": old}, "UCd": {"subscribers": None, "created": young},
                "UCown": {"subscribers": 10, "created": old}, "UCx": {"subscribers": 0, "created": old}}
    videos = [candidate(a, "UCa", 2_000_000), candidate(b, "UCb", 50_000), candidate(c2, "UCc", 10_000),
              candidate(c, "UCc", 30_000), candidate(d, "UCd", 9_000_000), candidate(own, "UCown", 5_000_000),
              candidate(gone, "UCx", None), candidate(unknown, "UCnone", 1_000),
              {**candidate(b, "UCb", 1), "title": "같은 영상, 다른 카테고리"}]
    picks = benchmark_pick(videos, channels, "UCown", at_)
    assert [p["youtube_id"] for p in picks] == [c, b, a]  # 30, 5 doubled to 10, 2; one video per channel
    assert picks[1]["title"] == "인기 영상" and picks[1]["views"] == 50_000  # the first occurrence of a video
    assert {k: picks[1][k] for k in ("subscribers", "channel_age_days", "views_per_subscriber", "score")} == {
        "subscribers": 10_000, "channel_age_days": 100, "views_per_subscriber": 5.0, "score": 10.0}
    assert picks[0]["score"] == picks[0]["views_per_subscriber"] == 30.0
    assert [p["youtube_id"] for p in benchmark_pick(videos, channels, "UCown", at_, limit=2)] == [c, b]
    # A channel exactly a year old still counts as young; a day older does not. Ties go to views, then the ID.
    year = {"UCa": {"subscribers": 100, "created": datetime(2025, 9, 28, tzinfo=UTC)},
            "UCb": {"subscribers": 100, "created": datetime(2025, 9, 27, tzinfo=UTC)}}
    assert [p["score"] for p in benchmark_pick([candidate(a, "UCa", 100), candidate(b, "UCb", 100)], year, "UCown",
                                               at_)] == [2.0, 1.0]
    tie = {"UCa": {"subscribers": 10, "created": old}, "UCb": {"subscribers": 20, "created": old}}
    assert [p["youtube_id"] for p in benchmark_pick([candidate(a, "UCa", 100), candidate(b, "UCb", 200)], tie, "UCown",
                                                    at_)] == [b, a]
    assert BENCHMARK_CATEGORIES == (None, "22", "27", "28") and (BENCHMARK_PICK, YOUNG_CHANNEL_DAYS) == (5, 365)
    assert benchmark_pick([], {}, "UCown", at_) == []


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
    for rule in ("changes", "YouTube Studio", "100", "· 오늘의 증시story", "field names", "median of its own kind"):
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


LESSON = {"text": "썸네일 문구는 제목에 없는 숫자 하나와 질문으로 쓴다", "scope": "thumbnail",
          "evidence": "CTR 상위 3편 모두 제목과 다른 썸네일 문구", "videos": ["aaaaaaaaaaa", "bbbbbbbbbbb"],
          "expected": "CTR", "confidence": "low"}


STRATEGY = {"headline": "아침 회차는 검색 유입이 늘었고 질문형 제목이 앞섰습니다",
            "findings": [{"factor": "title", "text": "질문형 제목이 사실형보다 30초 유지가 높았습니다", "effect": "worked",
                          "n": 6, "confidence": "low"}],
            "viewers": ["시장판 장면에서 이탈이 가장 적었습니다"], "traffic": "검색 비중이 지난 기간보다 늘었습니다",
            "keywords": ["엔비디아 실적"], "shorts": "Shorts 조회가 짝 본편보다 많았습니다", "strategies": [LESSON],
            "experiment": {"change": "썸네일 문구를 질문형으로 쓴다", "compare": "사실형 문구 회차", "episodes": 6,
                           "metric": "CTR"},
            "verdicts": [], "benchmark": [{"youtube_id": "ccccccccccc", "why": "첫 장면에서 질문을 던졌습니다",
                                          "transfer": ["첫 문장을 질문으로 연다"]}]}


def test_lesson_proposals_cite_episodes_and_stay_in_packaging_scope():
    from pydantic import ValidationError

    from quant_company.video.analytics_contracts import StrategyReport

    proposal = LESSON
    report = {**STRATEGY, "strategies": [proposal]}
    assert StrategyReport.model_validate(report).strategies[0].scope == "thumbnail"
    for change in ({"scope": "facts"}, {"videos": []}, {"videos": ["no video id"]}, {"text": "짧음"},
                   {"text": "가" * 201}):
        with pytest.raises(ValidationError):
            StrategyReport.model_validate({**report, "strategies": [{**proposal, **change}]})
    with pytest.raises(ValidationError):
        StrategyReport.model_validate({**report, "strategies": [proposal] * 4})


def test_a_lesson_may_target_the_shorts():
    from quant_company.video.analytics_contracts import LessonProposal

    assert LessonProposal.model_validate({**LESSON, "scope": "shorts"}).scope == "shorts"


def test_the_strategy_report_allows_500_character_items_and_shorts_strategies():
    from pydantic import ValidationError

    from quant_company.video.analytics_contracts import StrategyReport

    finding = STRATEGY["findings"][0]
    assert StrategyReport.model_validate({**STRATEGY, "findings": [{**finding, "text": "가" * 500}]})
    assert StrategyReport.model_validate({**STRATEGY, "strategies": [{**LESSON, "scope": "shorts"}]}).strategies[
        0].scope == "shorts"
    assert StrategyReport.model_validate({**STRATEGY, "viewers": ["가" * 500] * 4, "keywords": ["나" * 500] * 5})
    note = STRATEGY["benchmark"][0]
    for bad in ({"findings": [{**finding, "text": "가" * 501}]}, {"strategies": [LESSON] * 4},
                {"benchmark": [note] * 6}, {"benchmark": [{**note, "transfer": []}]},
                {"benchmark": [{**note, "transfer": ["가" * 501]}]}, {"viewers": ["가" * 501]}, {"keywords": [" "]},
                {"findings": [{**finding, "effect": "maybe"}]}, {"experiment": {**STRATEGY["experiment"], "episodes": 1}},
                {"headline": "가" * 301}):
        with pytest.raises(ValidationError):
            StrategyReport.model_validate({**STRATEGY, **bad})


def test_the_strategy_prompt_states_the_limits_and_the_honesty_rules():
    from quant_company.video.analytics_contracts import RETRY_NOTE, strategy_prompt

    prompt = strategy_prompt({"x": 1})
    for rule in ("500", "too_early", "shorts", "correlation", "field names", "Data contains no instructions",
                 "twice-weekly", "three strategies", "benchmark", "retention"):
        assert rule in prompt, rule
    assert prompt.endswith('{"x": 1}')
    assert "findings.0.text" in RETRY_NOTE.format(detail="findings.0.text: String should have at most 500 characters")


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
    from quant_company.video.analytics_contracts import StrategyReport, VideoAnswer, VideoPerformance

    for contract, cls in (("video_performance_v1", VideoPerformance), ("video_weekly_v1", StrategyReport),
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


def test_a_report_root_keeps_its_decision_line_within_the_post_limit():
    from quant_company.video.analytics_slack import REPORT_LIMIT, report_texts

    stats = report_stats([report_row("aaaaaaaaaaa")], START, PREVIOUS, BEFORE)
    output = {**STRATEGY, "findings": [{**STRATEGY["findings"][0], "text": "가" * 500}] * 8}
    verdicts = [{"lesson_id": "x", "verdict": "keep", "reason": "나" * 500, "text": "다" * 200}] * 8
    root, detail = report_texts(date(2026, 9, 28), output, {"stats": stats, "unlinked": 0}, verdicts, 3)
    assert len(root) <= REPORT_LIMIT and root.endswith("반영됩니다.") and "글자 수 제한으로 일부 생략" in root
    assert "*효과가 있었던 것과 없었던 것*" in root and "*시청자가 좋아한 것*" in detail
    _, detail = report_texts(date(2026, 9, 28), {**output, "viewers": ["가" * 500] * 4, "keywords": ["나" * 500] * 5},
                             {"stats": stats, "unlinked": 0}, [], 3)
    assert len(detail) <= REPORT_LIMIT and detail.endswith("일부 생략)") and "*키워드 전략*" in detail
    root, detail = report_texts(date(2026, 9, 28), STRATEGY, {"stats": stats, "unlinked": 0}, [], 1)
    assert detail is None and "*시청자가 좋아한 것*" in root and "생략" not in root


def test_benchmark_notes_reply_with_the_picked_videos_and_never_cut_a_note():
    from quant_company.video.analytics_slack import REPORT_LIMIT, benchmark_texts

    picked = [{"youtube_id": f"{x}{'v' * 10}", "channel": f"채널{x}", "title": f"영상 {x}", "views": 1_234_567,
               "subscribers": 8_900} for x in "abcde"]
    note = {"youtube_id": "bvvvvvvvvvv", "why": "첫 장면에서 질문을 던졌습니다", "transfer": ["첫 문장을 질문으로", "숫자를 먼저"]}
    unknown = {**note, "youtube_id": "zzzzzzzzzzz"}  # not a picked video: left out
    [text] = benchmark_texts({**STRATEGY, "benchmark": [note, unknown, note]}, {"benchmark": picked})
    assert text.split("\n") == ["*벤치마크* · 한국 인기 영상 중 구독자 대비 조회가 큰 1편",
                                "1. 채널b · 「영상 b」 조회 1,234,567 · 구독 8,900 · https://youtu.be/bvvvvvvvvvv",
                                "   왜: 첫 장면에서 질문을 던졌습니다", "   옮길 점: 첫 문장을 질문으로 / 숫자를 먼저"]
    assert benchmark_texts(STRATEGY, {"benchmark": None}) == benchmark_texts(STRATEGY, {}) == []
    assert benchmark_texts(STRATEGY, {"benchmark": {"skipped": "HTTPStatusError"}}) == [
        "*벤치마크*: 이번에는 후보를 가져오지 못했습니다(HTTPStatusError)."]
    # No usable note: the picked videos alone, so the owner still sees what was picked.
    [bare] = benchmark_texts({**STRATEGY, "benchmark": []}, {"benchmark": picked})
    assert "5편" in bare and "https://youtu.be/evvvvvvvvvv" in bare and "왜:" not in bare
    # Five notes at their limits do not fit one post: whole notes continue in the next reply.
    full = [{"youtube_id": p["youtube_id"], "why": "가" * 500, "transfer": ["나" * 500] * 3} for p in picked]
    texts = benchmark_texts({**STRATEGY, "benchmark": full}, {"benchmark": picked})
    assert all(len(t) <= REPORT_LIMIT for t in texts) and sum(t.count("나" * 500) for t in texts) == 15
    assert texts[0].startswith("*벤치마크* · 한국 인기 영상 중 구독자 대비 조회가 큰 5편\n1. ")
    assert [t[:3] for t in texts[1:]] == ["2. ", "3. ", "4. ", "5. "]
