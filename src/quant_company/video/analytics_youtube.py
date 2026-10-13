"""Reads for the performance analytics: Data API uploads, Analytics API per-video reports and Reporting API reach
reports (thumbnail impressions and CTR exist only there). The one write is creating a reporting job once."""

import re
from datetime import datetime
from urllib.parse import urlsplit

import httpx

from .analytics_metrics import PT
from .youtube import API, YouTube

ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
REPORTING = "https://youtubereporting.googleapis.com/v1"
REPORTING_HOST = "youtubereporting.googleapis.com"
# Thumbnails are public images; the YouTube token is never sent to this host.
IMAGE_HOST = "i.ytimg.com"
MAX_THUMBNAIL = 1_000_000
UPLOAD_PAGES = 4
TOTALS = {"views": "views", "estimatedMinutesWatched": "watch_minutes", "averageViewDuration": "avg_view_s",
          "averageViewPercentage": "avg_view_pct", "likes": "likes", "comments": "comments", "shares": "shares"}


def iso_seconds(value):
    found = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value or "")
    if not found:
        raise ValueError("Unexpected video duration")
    days, hours, minutes, seconds = (int(x or 0) for x in found.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def table(payload):
    names = [h["name"] for h in payload.get("columnHeaders", [])]
    return [dict(zip(names, row, strict=True)) for row in payload.get("rows") or []]


class AnalyticsClient:
    def __init__(self, settings, youtube=None, token=None, transport=None):
        self.settings, self.transport = settings, transport
        self.youtube = youtube or YouTube(settings, token=token, transport=transport)

    async def get(self, url, **params):
        response = await self.youtube.request("GET", url, params=params)
        response.raise_for_status()
        return response.json()

    # ---- Data API --------------------------------------------------------------------------------------------

    async def uploads(self):
        """Public uploads of the configured channel, newest first (up to UPLOAD_PAGES × 50)."""
        channel = self.settings.video_youtube_channel_id
        await self.youtube.account(channel)
        found = (await self.get(API + "/channels", part="contentDetails", id=channel))["items"]
        playlist = found[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        ids, token = [], None
        for _ in range(UPLOAD_PAGES):
            page = await self.get(API + "/playlistItems", part="contentDetails", playlistId=playlist, maxResults=50,
                                  **({"pageToken": token} if token else {}))
            ids += [item["contentDetails"]["videoId"] for item in page.get("items", [])]
            token = page.get("nextPageToken")
            if not token:
                break
        result = []
        for start in range(0, len(ids), 50):
            chunk = ids[start:start + 50]
            items = {v["id"]: v for v in (await self.get(API + "/videos", part="snippet,contentDetails,status",
                                                         id=",".join(chunk)))["items"]}
            for identity in chunk:
                video = items.get(identity)
                if not video:
                    continue
                if video["snippet"]["channelId"] != channel:
                    raise ValueError("Upload belongs to another channel")
                if video["status"]["privacyStatus"] != "public":
                    continue
                thumbs = video["snippet"].get("thumbnails", {})
                best = next((thumbs[k]["url"] for k in ("maxres", "standard", "high", "medium", "default") if k in thumbs),
                            None)
                result.append({"youtube_id": identity, "title": video["snippet"]["title"],
                               "published_at": instant(video["snippet"]["publishedAt"]),
                               "duration_s": iso_seconds(video["contentDetails"]["duration"]), "thumbnail_url": best})
        return result

    async def thumbnail(self, url):
        parsed = urlsplit(url or "")
        if parsed.scheme != "https" or parsed.hostname != IMAGE_HOST or parsed.username or parsed.port not in (None, 443):
            raise ValueError("Thumbnail outside the YouTube image host")
        async with httpx.AsyncClient(timeout=30, transport=self.transport, trust_env=False,
                                     follow_redirects=False) as client:
            response = await client.get(url)
        response.raise_for_status()
        kind = response.headers.get("content-type", "").split(";")[0]
        if kind not in {"image/jpeg", "image/png"} or len(response.content) > MAX_THUMBNAIL:
            raise ValueError("Unexpected thumbnail")
        return response.content, kind

    # ---- Analytics API ---------------------------------------------------------------------------------------

    async def query(self, start, end, metrics, **extra):
        return table(await self.get(ANALYTICS, ids="channel==" + self.settings.video_youtube_channel_id,
                                    startDate=start.isoformat(), endDate=end.isoformat(), metrics=metrics, **extra))

    async def video_metrics(self, youtube_id, start, end):
        """Totals, retention curve, traffic sources and search terms of one video over [start, end] (Pacific days)."""
        only = "video==" + youtube_id
        totals = await self.query(start, end, ",".join([*TOTALS, "subscribersGained", "subscribersLost"]), filters=only)
        curve = await self.query(start, end, "audienceWatchRatio,relativeRetentionPerformance",
                                 dimensions="elapsedVideoTimeRatio", filters=only)
        traffic = await self.query(start, end, "views,estimatedMinutesWatched", dimensions="insightTrafficSourceType",
                                   filters=only, sort="-views")
        search = await self.query(start, end, "views", dimensions="insightTrafficSourceDetail",
                                  filters=only + ";insightTrafficSourceType==YT_SEARCH", sort="-views", maxResults=25)
        row = totals[0] if totals else {}
        summary = {name: row.get(key) for key, name in TOTALS.items()}
        summary["subscribers"] = (row["subscribersGained"] - row["subscribersLost"]) if row else None
        summary = {k: summary[k] for k in ("views", "watch_minutes", "avg_view_s", "avg_view_pct", "subscribers",
                                           "likes", "comments", "shares")}
        views = summary["views"] or 0
        relative = [r["relativeRetentionPerformance"] for r in curve if r.get("relativeRetentionPerformance") is not None]
        return {"totals": summary,
                "curve": [[r["elapsedVideoTimeRatio"], r["audienceWatchRatio"]] for r in curve],
                "relative_retention": sum(relative) / len(relative) if relative else None,
                "traffic": [{"source": r["insightTrafficSourceType"], "views": r["views"],
                             "share": r["views"] / views if views else None} for r in traffic],
                "search_terms": [{"term": r["insightTrafficSourceDetail"], "views": r["views"]} for r in search]}

    # ---- Reporting API ---------------------------------------------------------------------------------------

    async def report_jobs(self):
        return {j["reportTypeId"]: j["id"] for j in (await self.get(REPORTING + "/jobs")).get("jobs", [])}

    async def create_job(self, report_type):
        response = await self.youtube.request("POST", REPORTING + "/jobs",
                                              json={"reportTypeId": report_type, "name": "quant-company " + report_type})
        response.raise_for_status()
        return response.json()

    async def reports(self, job_id):
        result, token = [], None
        while True:
            page = await self.get(f"{REPORTING}/jobs/{job_id}/reports", **({"pageToken": token} if token else {}))
            result += [{"id": r["id"], "day": instant(r["startTime"]).astimezone(PT).date(),
                        "created": instant(r["createTime"]), "url": r["downloadUrl"]} for r in page.get("reports", [])]
            token = page.get("nextPageToken")
            if not token:
                return result

    async def download(self, url):
        parsed = urlsplit(url or "")
        if parsed.scheme != "https" or parsed.hostname != REPORTING_HOST or parsed.username or parsed.port not in (None, 443):
            raise ValueError("Report download outside the Reporting API host")
        response = await self.youtube.request("GET", url)
        response.raise_for_status()
        return response.text
