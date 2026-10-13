"""YouTube Data/Analytics/Reporting reads against a simulated HTTP transport (no Google API is called)."""

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import httpx
import pytest

from quant_company.video.analytics_youtube import AnalyticsClient, iso_seconds

SETTINGS = SimpleNamespace(video_youtube_channel_id="UCfixture")


def client(handler):
    return AnalyticsClient(SETTINGS, token="fixture-token", transport=httpx.MockTransport(handler))


def video_item(youtube_id, privacy="public", channel="UCfixture", thumbs=None):
    return {"id": youtube_id, "status": {"privacyStatus": privacy},
            "contentDetails": {"duration": "PT4M52S"},
            "snippet": {"channelId": channel, "title": f"제목 {youtube_id}", "publishedAt": "2026-10-12T22:00:05Z",
                        "thumbnails": thumbs or {"high": {"url": f"https://i.ytimg.com/vi/{youtube_id}/hqdefault.jpg"},
                                                 "maxres": {"url": f"https://i.ytimg.com/vi/{youtube_id}/maxresdefault.jpg"}}}}


def data_api(videos):
    def handler(request):
        path, params = request.url.path, dict(request.url.params)
        assert request.headers["authorization"] == "Bearer fixture-token"
        if path.endswith("/channels"):
            if params.get("mine") == "true":
                return httpx.Response(200, json={"items": [{"id": "UCfixture"}]})
            assert params["id"] == "UCfixture"
            return httpx.Response(200, json={"items": [{"id": "UCfixture", "contentDetails": {
                "relatedPlaylists": {"uploads": "UUfixture"}}}]})
        if path.endswith("/playlistItems"):
            assert params["playlistId"] == "UUfixture"
            if params.get("pageToken") == "p2":
                return httpx.Response(200, json={"items": [{"contentDetails": {"videoId": v["id"]}} for v in videos[1:]]})
            return httpx.Response(200, json={"items": [{"contentDetails": {"videoId": videos[0]["id"]}}],
                                             "nextPageToken": "p2"})
        if path.endswith("/videos"):
            wanted = params["id"].split(",")
            return httpx.Response(200, json={"items": [v for v in videos if v["id"] in wanted]})
        raise AssertionError(request.url)
    return handler


async def test_uploads_keep_the_public_videos_of_the_configured_channel():
    videos = [video_item("aaaaaaaaaaa"), video_item("bbbbbbbbbbb", privacy="private"),
              video_item("ccccccccccc", thumbs={"high": {"url": "https://i.ytimg.com/vi/ccccccccccc/hqdefault.jpg"}})]
    uploads = await client(data_api(videos)).uploads()
    assert uploads == [
        {"youtube_id": "aaaaaaaaaaa", "title": "제목 aaaaaaaaaaa", "published_at": datetime(2026, 10, 12, 22, 0, 5, tzinfo=UTC),
         "duration_s": 292, "thumbnail_url": "https://i.ytimg.com/vi/aaaaaaaaaaa/maxresdefault.jpg"},
        {"youtube_id": "ccccccccccc", "title": "제목 ccccccccccc", "published_at": datetime(2026, 10, 12, 22, 0, 5, tzinfo=UTC),
         "duration_s": 292, "thumbnail_url": "https://i.ytimg.com/vi/ccccccccccc/hqdefault.jpg"}]


async def test_uploads_of_another_channel_are_refused():
    with pytest.raises(ValueError):
        await client(data_api([video_item("aaaaaaaaaaa", channel="UCother")])).uploads()


def test_iso_durations():
    assert [iso_seconds(v) for v in ("PT4M52S", "PT1H2M3S", "PT45S", "P0D", "PT10M")] == [292, 3723, 45, 0, 600]


def analytics_api(calls):
    tables = {
        None: (["views", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage", "subscribersGained",
                "subscribersLost", "likes", "comments", "shares"], [[820, 2200.5, 161, 55.2, 9, 1, 30, 4, 2]]),
        "elapsedVideoTimeRatio": (["elapsedVideoTimeRatio", "audienceWatchRatio", "relativeRetentionPerformance"],
                                  [[0.01, 1.1, 0.4], [0.5, 0.6, 0.6], [1.0, 0.3, 0.5]]),
        "insightTrafficSourceType": (["insightTrafficSourceType", "views", "estimatedMinutesWatched"],
                                     [["SUBSCRIBER", 400, 1200], ["YT_SEARCH", 300, 700], ["RELATED_VIDEO", 120, 300]]),
        "insightTrafficSourceDetail": (["insightTrafficSourceDetail", "views"], [["엔비디아 실적", 120], ["코스피 전망", 80]]),
    }

    def handler(request):
        params = dict(request.url.params)
        assert request.url.host == "youtubeanalytics.googleapis.com" and request.url.path == "/v2/reports"
        calls.append(params)
        names, rows = tables[params.get("dimensions")]
        return httpx.Response(200, json={"columnHeaders": [{"name": n} for n in names], "rows": rows})
    return handler


async def test_video_metrics_ask_each_report_for_one_video_and_window():
    calls = []
    metrics = await client(analytics_api(calls)).video_metrics("aaaaaaaaaaa", date(2026, 10, 12), date(2026, 10, 14))
    assert {c["ids"] for c in calls} == {"channel==UCfixture"}
    assert {(c["startDate"], c["endDate"]) for c in calls} == {("2026-10-12", "2026-10-14")}
    search = next(c for c in calls if c.get("dimensions") == "insightTrafficSourceDetail")
    assert search["filters"] == "video==aaaaaaaaaaa;insightTrafficSourceType==YT_SEARCH"
    assert (search["sort"], search["maxResults"]) == ("-views", "25")
    assert metrics["totals"] == {"views": 820, "watch_minutes": 2200.5, "avg_view_s": 161, "avg_view_pct": 55.2,
                                 "subscribers": 8, "likes": 30, "comments": 4, "shares": 2}
    assert metrics["curve"] == [[0.01, 1.1], [0.5, 0.6], [1.0, 0.3]]
    assert metrics["relative_retention"] == pytest.approx(0.5)
    assert metrics["traffic"] == [{"source": "SUBSCRIBER", "views": 400, "share": pytest.approx(400 / 820)},
                                  {"source": "YT_SEARCH", "views": 300, "share": pytest.approx(300 / 820)},
                                  {"source": "RELATED_VIDEO", "views": 120, "share": pytest.approx(120 / 820)}]
    assert metrics["search_terms"] == [{"term": "엔비디아 실적", "views": 120}, {"term": "코스피 전망", "views": 80}]


async def test_reporting_jobs_and_reports_page_through_the_reporting_api():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, dict(request.url.params)))
        assert request.url.host == "youtubereporting.googleapis.com"
        if request.url.path == "/v1/jobs" and request.method == "GET":
            return httpx.Response(200, json={"jobs": [{"id": "job-basic", "reportTypeId": "channel_reach_basic_a1"}]})
        if request.url.path == "/v1/jobs" and request.method == "POST":
            body = json.loads(request.content)
            return httpx.Response(200, json={"id": "job-combined", **body})
        if request.url.path == "/v1/jobs/job-basic/reports":
            if request.url.params.get("pageToken") == "n2":
                return httpx.Response(200, json={"reports": [{"id": "r2", "startTime": "2026-10-13T07:00:00Z",
                                                              "createTime": "2026-10-15T01:00:00Z", "downloadUrl": "u2"}]})
            return httpx.Response(200, json={"reports": [{"id": "r1", "startTime": "2026-10-12T07:00:00Z",
                                                         "createTime": "2026-10-14T01:00:00Z", "downloadUrl": "u1"}],
                                             "nextPageToken": "n2"})
        raise AssertionError(request.url)

    api = client(handler)
    assert await api.report_jobs() == {"channel_reach_basic_a1": "job-basic"}
    created = await api.create_job("channel_reach_combined_a1")
    assert created["id"] == "job-combined" and created["reportTypeId"] == "channel_reach_combined_a1"
    reports = await api.reports("job-basic")
    assert [(r["id"], r["day"], r["created"]) for r in reports] == [
        ("r1", date(2026, 10, 12), datetime(2026, 10, 14, 1, tzinfo=UTC)),
        ("r2", date(2026, 10, 13), datetime(2026, 10, 15, 1, tzinfo=UTC))]


async def test_report_downloads_only_reach_the_reporting_host():
    def handler(request):
        assert request.url.host == "youtubereporting.googleapis.com"
        return httpx.Response(200, text="date,video_id\n")

    api = client(handler)
    assert await api.download("https://youtubereporting.googleapis.com/v1/media/x?alt=media") == "date,video_id\n"
    for url in ("https://example.com/v1/media/x", "http://youtubereporting.googleapis.com/v1/media/x"):
        with pytest.raises(ValueError):
            await api.download(url)


async def test_thumbnails_come_from_the_image_host_without_the_youtube_token():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"})

    api = client(handler)
    assert await api.thumbnail("https://i.ytimg.com/vi/aaaaaaaaaaa/maxresdefault.jpg") == (b"\xff\xd8jpeg", "image/jpeg")
    assert "authorization" not in seen[0].headers
    with pytest.raises(ValueError):
        await api.thumbnail("https://evil.example/vi/a.jpg")


def test_youtube_login_adds_analytics_read_access_to_the_upload_scopes(tmp_path, monkeypatch):
    import sys
    import types

    from quant_company.video.auth import login_youtube

    seen = {}

    class Flow:
        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            seen["scopes"] = scopes
            return cls()

        def run_local_server(self, **options):
            return types.SimpleNamespace(to_json=lambda: '{"token": "simulated"}')

    flow = types.ModuleType("google_auth_oauthlib.flow")
    flow.InstalledAppFlow = Flow
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", types.ModuleType("google_auth_oauthlib"))
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", flow)
    login_youtube(tmp_path)
    assert seen["scopes"] == ["https://www.googleapis.com/auth/youtube.upload",
                              "https://www.googleapis.com/auth/youtube.force-ssl",
                              "https://www.googleapis.com/auth/yt-analytics.readonly"]
