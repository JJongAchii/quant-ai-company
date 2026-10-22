"""Deterministic performance figures for published episodes: title → edition, fixed metric windows, the retention
curve mapped onto the rendered scenes, channel baselines and the YouTube Reporting API reach CSV."""

import csv
import io
import re
import statistics
from datetime import date, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
# YouTube Analytics and Reporting days are Pacific-time days.
PT = ZoneInfo("America/Los_Angeles")
HORIZONS = {"d3": 3, "d7": 7, "d28": 28}
HOOK_SECONDS = 30
# Fewer comparable episodes than this and a baseline would only describe one or two news days.
MIN_BASELINE = 5
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
# upload.compose: "<hook> | {M}월 {D}일 {아침|마감} · 오늘의 증시story" (series name last since 2026-10-10).
SERIES = "오늘의 증시story"
TITLE = re.compile(r"\|\s*(\d{1,2})월\s+(\d{1,2})일\s+(아침|마감)\s*·\s*오늘의 증시story\s*$")
# Until 12 October 2026: "[오늘의 증시story] … | 10월 8일 마감 브리핑".
LEGACY_TITLE = re.compile(r"^\s*\[오늘의 증시story\].*\|\s*(\d{1,2})월\s+(\d{1,2})일\s+(아침|마감)\s*브리핑\s*$")
KINDS = {"아침": "am", "마감": "close"}
# Reporting API traffic_source_type codes, grouped the way Studio names them.
SOURCES = {0: "direct", 1: "advertising", 3: "browse", 4: "channel", 5: "search", 7: "suggested", 8: "other_youtube",
           9: "external", 11: "cards", 14: "playlists", 17: "notifications", 18: "playlist_page", 20: "end_screen",
           24: "shorts", 26: "hashtags", 32: "related"}
REACH_COLUMNS = {"channel_reach_basic_a1": ("date", "video_id", "video_thumbnail_impressions",
                                            "video_thumbnail_impressions_ctr"),
                 "channel_reach_combined_a1": ("date", "video_id", "traffic_source_type", "video_thumbnail_impressions",
                                               "video_thumbnail_impressions_ctr")}


def in_series(title):
    """The brief series is what is measured; other series of the channel ([역사story] …) are not."""
    return SERIES in (title or "")


def shorts_key(title):
    """A Shorts title as the cut wrote it: an owner-added hashtag (#Shorts) still matches its cut."""
    return re.sub(r"\s*#\S+", "", title or "").strip()


def parse_title(title, published_at):
    """(edition day, 'am'|'close') from the series suffix (or the older series-tag format), or None for any other
    title. The year is the KST publish year, one earlier when the title names a later month (a 31 December close
    published after midnight)."""
    found = TITLE.search(title or "") or LEGACY_TITLE.search(title or "")
    if not found:
        return None
    published = published_at.astimezone(KST).date()
    month, day = int(found[1]), int(found[2])
    try:
        edition = date(published.year, month, day)
        if edition > published + timedelta(days=1):
            edition = date(published.year - 1, month, day)
    except ValueError:
        return None
    return edition, KINDS[found[3]]


def pt_day(published_at):
    return published_at.astimezone(PT).date()


def window(published_at, horizon):
    """Fixed per-horizon day range from the Pacific publish day, so every episode is compared over equal days."""
    start = pt_day(published_at)
    return start, start + timedelta(days=HORIZONS[horizon] - 1)


def watch_ratio(curve, ratio):
    points = sorted(curve)
    if ratio <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        if ratio <= x1:
            return y0 + (y1 - y0) * (ratio - x0) / (x1 - x0)
    return points[-1][1]


def hook_retention(curve, duration):
    """Audience watch ratio at 30 seconds; None when the video is no longer than that."""
    if not curve or not duration or duration <= HOOK_SECONDS:
        return None
    return watch_ratio(curve, HOOK_SECONDS / duration)


def scene_drops(curve, duration, scenes=None, chapters=None):
    """Watch-ratio loss across each rendered scene (manifest scenes, or chapters on older episodes) and per minute;
    the steepest scene per minute is flagged."""
    if scenes is not None:
        parts = [(s["id"], s["type"], s["heading"], s["t0"], s["t1"]) for s in scenes]
    else:
        starts = [c["start"] for c in chapters] + [duration]
        parts = [(f"c{n}", "chapter", c["title"], c["start"], starts[n + 1]) for n, c in enumerate(chapters)]
    rows = []
    for identity, kind, heading, start, end in parts:
        drop = watch_ratio(curve, start / duration) - watch_ratio(curve, min(end, duration) / duration)
        rows.append({"id": identity, "type": kind, "heading": heading, "start": start, "end": end, "drop": drop,
                     "per_minute": drop / ((end - start) / 60) if end > start else 0.0, "steepest": False})
    if rows:
        max(rows, key=lambda r: r["per_minute"])["steepest"] = True
    return rows


def baseline(values):
    """Median of the comparable episodes, or None below MIN_BASELINE of them."""
    known = [v for v in values if v is not None]
    return statistics.median(known) if len(known) >= MIN_BASELINE else None


def _day(text):
    return date.fromisoformat(text if "-" in text else f"{text[:4]}-{text[4:6]}-{text[6:8]}")


def parse_reach_csv(text, report_type):
    """Rows {day, video_id, source, impressions, clicks} from a reach report; combined reports are added up per
    traffic source. The CTR is documented as clicks/impressions but not its scale: a report with any value above 1 is
    read as percent. Clicks are impressions × CTR so sources and days add up."""
    reader = csv.DictReader(io.StringIO(text))
    required = REACH_COLUMNS[report_type]
    if not set(required) <= set(reader.fieldnames or ()):
        raise ValueError("Unexpected reach report columns")
    raw = list(reader)
    ratios = [float(r["video_thumbnail_impressions_ctr"] or 0) for r in raw]
    scale = 100 if any(r > 1 for r in ratios) else 1
    rows = {}
    for row, ratio in zip(raw, ratios, strict=True):
        if not VIDEO_ID.match(row["video_id"]):
            raise ValueError("Invalid video ID in reach report")
        source = (SOURCES.get(int(row["traffic_source_type"]), "other")
                  if report_type == "channel_reach_combined_a1" else "ALL")
        key = (_day(row["date"]), row["video_id"], source)
        impressions = int(row["video_thumbnail_impressions"] or 0)
        entry = rows.setdefault(key, {"day": key[0], "video_id": key[1], "source": source, "impressions": 0, "clicks": 0.0})
        entry["impressions"] += impressions
        entry["clicks"] = round(entry["clicks"] + impressions * ratio / scale, 3)
    return list(rows.values())
