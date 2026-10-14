"""Real PostgreSQL; YouTube data is an explicitly simulated fixture (no API is called here)."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from quant_company.company import PolicyError
from quant_company.video.analytics_store import AnalyticsStore

from .test_briefing import brief, complete  # noqa: F401
from .test_video import new_job, video  # noqa: F401

PUBLISHED = datetime(2026, 9, 21, 22, 0, tzinfo=UTC)  # 07:00 KST on 22 September, the fixture's AM edition


@pytest.fixture
def analytics(video):  # noqa: F811
    store, clock = video
    settings = store.settings
    settings.video_analytics_enabled = True
    settings.video_analytics_channel_id = "CPERF"
    settings.slack_allowed_channels = [*settings.slack_allowed_channels, "CPERF"]
    return AnalyticsStore(store.company)


def upload(youtube_id="aaaaaaaaaaa", title="반도체 강세 확산될까 | 9월 22일 아침 · 오늘의 증시story", at=PUBLISHED, **extra):
    return {"youtube_id": youtube_id, "title": title, "published_at": at, "duration_s": 292,
            "thumbnail_url": f"https://i.ytimg.com/vi/{youtube_id}/maxresdefault.jpg", **extra}


def delivered_job(brief, video, title="반도체가 이끈 상승 | 9월 22일 아침 · 오늘의 증시story"):  # noqa: F811
    store, _ = video
    job = new_job(brief, video)
    store.save(job, "delivered", artifacts={"title": title, "duration": 292.0, "chapters": [{"start": 0, "title": "질문"}]})
    return store.get(job["id"])


def publication(analytics, youtube_id="aaaaaaaaaaa"):
    with analytics.db.transaction() as conn:
        return conn.execute("SELECT * FROM video_publications WHERE youtube_id=%s", (youtube_id,)).fetchone()


def test_public_upload_links_to_its_job_by_the_title_suffix(brief, video, analytics):  # noqa: F811
    job = delivered_job(brief, video)
    # The owner shortened the hook before uploading; the suffix still names the edition.
    analytics.upsert_publications([upload(), upload("bbbbbbbbbbb", "[오늘의 증시story] 손으로 만든 회차")])
    row = publication(analytics)
    assert (row["job_id"], row["edition_kind"], row["matched_by"]) == (job["id"], "am", "title_suffix")
    assert row["proposed_title"] == "반도체가 이끈 상승 | 9월 22일 아침 · 오늘의 증시story"
    hand_made = publication(analytics, "bbbbbbbbbbb")
    assert hand_made["job_id"] is None and hand_made["matched_by"] is None


def test_exact_title_links_when_the_suffix_names_no_edition(brief, video, analytics):  # noqa: F811
    title = "반도체가 이끈 상승 | 9월 23일 아침 · 오늘의 증시story"  # owner typo in the day; the job title is the same
    job = delivered_job(brief, video, title)
    analytics.upsert_publications([upload(title=title)])
    row = publication(analytics)
    assert (row["job_id"], row["matched_by"]) == (job["id"], "exact_title")


def test_manual_link_survives_later_syncs(brief, video, analytics):  # noqa: F811
    job = delivered_job(brief, video)
    analytics.upsert_publications([upload(title="제목만 바꾼 업로드")])
    assert publication(analytics)["job_id"] is None
    analytics.link("aaaaaaaaaaa", str(job["id"]))
    analytics.upsert_publications([upload(title="제목을 한 번 더 바꿈")])
    row = publication(analytics)
    assert (row["job_id"], row["matched_by"], row["title"]) == (job["id"], "manual", "제목을 한 번 더 바꿈")
    with pytest.raises(PolicyError):
        analytics.link("ccccccccccc", str(job["id"]))


BASIC = "date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n{rows}"


def basic(day, impressions, ratio, youtube_id="aaaaaaaaaaa"):
    return BASIC.format(rows=f"{day:%Y%m%d},UCfixture,{youtube_id},{impressions},{ratio}\n")


def test_a_newer_report_replaces_its_day_and_older_or_repeated_reports_change_nothing(analytics):
    analytics.upsert_publications([upload()])
    day, created = date(2026, 9, 21), datetime(2026, 9, 23, tzinfo=UTC)
    assert analytics.record_report("channel_reach_basic_a1", "r1", day, created, basic(day, 1000, 0.05)) is True
    assert analytics.reach("aaaaaaaaaaa", day, day)["impressions"] == 1000
    assert analytics.record_report("channel_reach_basic_a1", "r1", day, created, basic(day, 1000, 0.05)) is False
    assert analytics.record_report("channel_reach_basic_a1", "r2", day, created + timedelta(days=1),
                                   basic(day, 1200, 0.04)) is True
    assert analytics.record_report("channel_reach_basic_a1", "r0", day, created - timedelta(days=1),
                                   basic(day, 5, 0.5)) is False
    totals = analytics.reach("aaaaaaaaaaa", day, day)
    assert (totals["impressions"], totals["clicks"], totals["ctr"]) == (1200, 48.0, pytest.approx(0.04))


def test_reach_adds_days_and_splits_sources(analytics):
    analytics.upsert_publications([upload()])
    created = datetime(2026, 9, 25, tzinfo=UTC)
    for n, (day, impressions, ratio) in enumerate([(date(2026, 9, 21), 1000, 0.05), (date(2026, 9, 22), 500, 0.02)]):
        analytics.record_report("channel_reach_basic_a1", f"b{n}", day, created, basic(day, impressions, ratio))
    combined = ("date,channel_id,video_id,traffic_source_type,traffic_source_detail,operating_system,device_type,"
                "video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
                "20260921,UCfixture,aaaaaaaaaaa,3,,1,1,800,0.05\n20260921,UCfixture,aaaaaaaaaaa,5,반도체,1,1,200,0.05\n")
    analytics.record_report("channel_reach_combined_a1", "c0", date(2026, 9, 21), created, combined)
    totals = analytics.reach("aaaaaaaaaaa", date(2026, 9, 21), date(2026, 9, 23))
    assert (totals["impressions"], totals["clicks"], totals["days"]) == (1500, 60.0, 2)
    assert totals["ctr"] == pytest.approx(0.04)
    assert totals["sources"] == [{"source": "browse", "impressions": 800, "ctr": pytest.approx(0.05)},
                                 {"source": "search", "impressions": 200, "ctr": pytest.approx(0.05)}]


def test_a_snapshot_waits_for_the_last_reach_day_of_its_window(analytics):
    analytics.upsert_publications([upload()])  # Pacific publish day 21 September: d3 window 21-23 September
    assert analytics.due_snapshots(datetime(2026, 9, 25, 12, tzinfo=UTC)) == []
    analytics.record_report("channel_reach_basic_a1", "b23", date(2026, 9, 23), datetime(2026, 9, 25, tzinfo=UTC),
                            basic(date(2026, 9, 23), 10, 0.1, "zzzzzzzzzzz"))
    due = analytics.due_snapshots(datetime(2026, 9, 25, 12, tzinfo=UTC))
    assert [(p["youtube_id"], h) for p, h in due] == [("aaaaaaaaaaa", "d3")]
    analytics.save_snapshot("aaaaaaaaaaa", "d3", date(2026, 9, 21), date(2026, 9, 23), {"views": 10})
    assert analytics.due_snapshots(datetime(2026, 9, 25, 12, tzinfo=UTC)) == []


def test_a_snapshot_without_reach_data_is_taken_four_days_late(analytics):
    analytics.upsert_publications([upload()])
    assert analytics.due_snapshots(datetime(2026, 9, 27, 12, tzinfo=UTC)) == []
    due = analytics.due_snapshots(datetime(2026, 9, 28, 12, tzinfo=UTC))
    assert [(p["youtube_id"], h) for p, h in due] == [("aaaaaaaaaaa", "d3")]


def test_other_series_uploads_are_kept_but_not_measured_until_linked(brief, video, analytics):  # noqa: F811
    job = delivered_job(brief, video)
    history = upload("hhhhhhhhhhh", "[역사story] 한글날은 왜 10월 9일일까? 날짜를 바꾼 책 한 권")
    analytics.upsert_publications([upload(), history])
    due = analytics.due_snapshots(datetime(2026, 9, 28, 12, tzinfo=UTC))
    assert [(p["youtube_id"], h) for p, h in due] == [("aaaaaaaaaaa", "d3")]
    status = analytics.status()["publications"]
    assert (status["total"], status["other_series"], status["unlinked"]) == (2, 1, [])
    # An operator link puts it back in (an episode uploaded under another title).
    analytics.link("hhhhhhhhhhh", str(job["id"]))
    due = analytics.due_snapshots(datetime(2026, 9, 28, 12, tzinfo=UTC))
    assert {p["youtube_id"] for p, _ in due} == {"aaaaaaaaaaa", "hhhhhhhhhhh"}


def test_baseline_uses_the_same_edition_kind_over_the_previous_28_days(analytics):
    rows = []
    for n in range(6):
        rows.append(upload(f"am{n:09d}", f"훅 | 9월 {10 + n}일 아침 · 오늘의 증시story", PUBLISHED - timedelta(days=12 - n)))
    rows.append(upload("closexxxxxx", "훅 | 9월 15일 마감 · 오늘의 증시story", PUBLISHED - timedelta(days=6, hours=-10)))
    rows.append(upload("oldamxxxxxx", "훅 | 8월 1일 아침 · 오늘의 증시story", PUBLISHED - timedelta(days=50)))
    rows.append(upload())
    analytics.upsert_publications(rows)
    for n, value in enumerate([0.01, 0.02, 0.03, 0.04, 0.05, 0.90]):
        analytics.save_snapshot(f"am{n:09d}", "d3", date(2026, 9, 1), date(2026, 9, 3), {"ctr": value})
    analytics.save_snapshot("closexxxxxx", "d3", date(2026, 9, 1), date(2026, 9, 3), {"ctr": 0.5})
    analytics.save_snapshot("oldamxxxxxx", "d3", date(2026, 8, 1), date(2026, 8, 3), {"ctr": 0.5})
    result = analytics.baselines(publication(analytics), "d3", ("ctr", "views"))
    assert result == {"ctr": pytest.approx(0.035), "views": None, "n": 6}


def seed_lessons(analytics, states):
    identity = uuid4()
    with analytics.db.transaction() as conn:
        conn.execute("""INSERT INTO video_analyses(id,kind,request_id,week,request_digest,state)
            VALUES(%s,'weekly',%s,%s,'d','completed')""", (identity, f"video-analytics-weekly-{identity}", date(2026, 9, 14)))
        ids = []
        for n, state in enumerate(states):
            lesson = uuid4()
            conn.execute("""INSERT INTO video_lessons(id,analysis_id,text,scope,evidence,state)
                VALUES(%s,%s,%s,'thumbnail','{}',%s)""", (lesson, identity, f"교훈 {n}: 썸네일 문구는 질문으로", state))
            ids.append(str(lesson))
    return ids


def test_new_video_jobs_freeze_only_adopted_lessons(brief, video, analytics):  # noqa: F811
    proposed, active, retired = seed_lessons(analytics, ["proposed", "active", "retired"])
    job = new_job(brief, video)
    assert job["policy"]["lessons"] == [{"id": active, "scope": "thumbnail", "text": "교훈 1: 썸네일 문구는 질문으로"}]


def test_lessons_are_not_frozen_while_analytics_is_off(brief, video, analytics):  # noqa: F811
    seed_lessons(analytics, ["active"])
    analytics.settings.video_analytics_enabled = False
    assert "lessons" not in new_job(brief, video)["policy"]


def test_owner_decisions_apply_once_and_respect_the_active_limit(analytics):
    analytics.settings.video_analytics_max_lessons = 2
    first, second, third = seed_lessons(analytics, ["proposed", "proposed", "proposed"])
    assert analytics.decide(first, "adopt", "UHUMAN", "e1") == "active"
    assert analytics.decide(first, "adopt", "UHUMAN", "e1") == "active"  # Slack retry of the same click
    assert analytics.decide(second, "adopt", "UHUMAN", "e2") == "active"
    assert analytics.decide(third, "adopt", "UHUMAN", "e3") == "full"
    assert analytics.decide(first, "retire", "UHUMAN", "e4") == "retired"
    assert analytics.decide(third, "adopt", "UHUMAN", "e5") == "active"
    assert analytics.decide(first, "adopt", "UHUMAN", "e6") == "unchanged"  # a retired lesson stays retired
    with pytest.raises(PolicyError):
        analytics.decide(second, "reject", "UOTHER", "e7")
    assert [x["id"] for x in analytics.active_lessons()] == [second, third]


def test_analysis_request_runs_once_and_reserves_the_daily_allowance(analytics):
    analytics.upsert_publications([upload()])
    request = {"prompt": "p"}
    assert analytics.begin_analysis("video-analytics-aaaaaaaaaaa-d3", "video", request, youtube_id="aaaaaaaaaaa") is None
    # Asked again while running: claude-runtime answers by request ID, never a second inference.
    assert analytics.begin_analysis("video-analytics-aaaaaaaaaaa-d3", "video", request, youtube_id="aaaaaaaaaaa") is None
    with pytest.raises(PolicyError):
        analytics.begin_analysis("video-analytics-aaaaaaaaaaa-d3", "video", {"prompt": "changed"}, youtube_id="aaaaaaaaaaa")
    analytics.finish_analysis("video-analytics-aaaaaaaaaaa-d3", {"output": {"summary": "s"}})
    assert analytics.begin_analysis("video-analytics-aaaaaaaaaaa-d3", "video", request,
                                    youtube_id="aaaaaaaaaaa") == {"output": {"summary": "s"}}
    with analytics.db.transaction() as conn:
        assert conn.execute("SELECT reserved FROM daily_usage WHERE day=CURRENT_DATE").fetchone()["reserved"] == 1


def test_busy_runtime_refunds_the_allowance_and_allows_the_same_request_later(analytics):
    analytics.begin_analysis("video-analytics-weekly-2026-09-21", "weekly", {"prompt": "p"}, week=date(2026, 9, 21))
    analytics.reject_analysis("video-analytics-weekly-2026-09-21", "busy")
    with analytics.db.transaction() as conn:
        assert conn.execute("SELECT reserved FROM daily_usage WHERE day=CURRENT_DATE").fetchone()["reserved"] == 0
    assert analytics.begin_analysis("video-analytics-weekly-2026-09-21", "weekly", {"prompt": "p"},
                                    week=date(2026, 9, 21)) is None


async def test_the_episode_writer_receives_the_lessons_frozen_at_job_creation(brief, video, analytics, tmp_path):  # noqa: F811
    import json

    from quant_company.video.runner import VideoRunner

    from .test_video_motion import EpisodeProvider

    store, _ = video
    store.settings.video_template = "motion-v2"
    store.settings.video_asset_dir = tmp_path / "assets"
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "manifest.json").write_text(json.dumps({"fixture-photo": {"tags": [], "kind": "자료사진"}}))
    seed_lessons(analytics, ["active"])
    job = new_job(brief, video)
    # Adopted after the job was created: this episode's prompt must not change.
    later = seed_lessons(analytics, ["active"])
    with analytics.db.transaction() as conn:
        conn.execute("UPDATE video_lessons SET text='나중에 채택한 교훈은 다음 회차부터' WHERE id=%s", (later[0],))
    provider = EpisodeProvider()
    await VideoRunner(store.company, provider=provider).tick()
    prompt = provider.calls[0].prompt
    assert "[thumbnail] 교훈 0: 썸네일 문구는 질문으로" in prompt
    assert "나중에 채택한 교훈" not in prompt
    assert job["policy"]["lessons"][0]["text"] == "교훈 0: 썸네일 문구는 질문으로"


def test_uploads_older_than_sixty_days_are_not_measured(analytics):
    analytics.upsert_publications([upload(at=datetime(2026, 7, 1, tzinfo=UTC)), upload("bbbbbbbbbbb")])
    due = analytics.due_snapshots(datetime(2026, 9, 28, 12, tzinfo=UTC))
    assert {p["youtube_id"] for p, _ in due} == {"bbbbbbbbbbb"}


def test_only_a_live_episode_in_production_holds_the_analysis(brief, video, analytics):  # noqa: F811
    store, _ = video
    job = new_job(brief, video)  # queued, about to be produced
    assert analytics.producing()
    with analytics.db.transaction() as conn:
        conn.execute("UPDATE video_jobs SET updated_at=now()-interval '7 hours' WHERE id=%s", (job["id"],))
    assert not analytics.producing()  # a job left queued for hours no longer holds the lane
    with analytics.db.transaction() as conn:
        conn.execute("UPDATE video_jobs SET updated_at=now() WHERE id=%s", (job["id"],))
    store.settings.video_enabled = False
    assert not analytics.producing()
