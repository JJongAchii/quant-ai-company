"""Deterministic performance figures for published episodes: title → edition, fixed metric windows, the retention
curve mapped onto the rendered scenes, channel baselines, the strategy report's figures and the YouTube Reporting API
reach CSV."""

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
# A three-day card goes only to an upload this far from the d3 median of its own kind (am, close, shorts) over the
# previous 28 days: (at or above, at or below) that median times the factor, for each metric.
OUTLIER = {"views": (2.0, 0.5), "ctr": (1.5, 2 / 3), "hook_30s": (1.3, 0.77)}
OUTLIER_MIN = 3
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


def baseline(values, minimum=MIN_BASELINE):
    """Median of the comparable episodes, or None below `minimum` of them."""
    known = [v for v in values if v is not None]
    return statistics.median(known) if len(known) >= minimum else None


def outlier(metrics, base):
    """[{'metric', 'ratio'}] far from the same-kind median; [] below OUTLIER_MIN comparable episodes. A metric without
    a value or a median is skipped; the bounds hold with a float tolerance (0.02 / 0.03 is a ratio of 2/3)."""
    if base["n"] < OUTLIER_MIN:
        return []
    reasons = []
    for key, (high, low) in OUTLIER.items():
        value, median = metrics.get(key), base.get(key)
        if value is None or not median:
            continue
        ratio = value / median
        if ratio >= high - 1e-9 or ratio <= low + 1e-9:
            reasons.append({"metric": key, "ratio": round(ratio, 2)})
    return reasons


def medians(rows, keys=("ctr", "hook_30s", "avg_view_pct", "views")):
    result = {"n": len(rows)}
    for key in keys:
        values = [r["metrics"].get(key) for r in rows if r["metrics"] and r["metrics"].get(key) is not None]
        result[key] = statistics.median(values) if values else None
    return result


# ---- The channel strategy report: the figures the service computes and freezes with the request -----------------
# Rows are AnalyticsStore.episodes() rows; every figure reads the three-day snapshot (d3), the one window all
# uploads share, except the search terms (the latest snapshot holds the most). The output is JSON-native.

def title_hook(title):
    """The hook of a title without the series suffix or the older series tag."""
    return re.split(r"\s*\|\s*", (title or "").replace("[오늘의 증시story]", ""))[0].strip()


def group_stats(rows):
    """Totals of views, subscribers and impressions, impression-weighted CTR and the median hold and view share."""
    metrics = [r["d3"] for r in rows]

    def known(key):
        return [m[key] for m in metrics if m.get(key) is not None]

    result = {"n": len(rows)}
    for key in ("views", "subscribers", "impressions"):
        result[key] = sum(known(key)) if known(key) else None
    shown = [(m["ctr"], m["impressions"]) for m in metrics if m.get("ctr") is not None and m.get("impressions")]
    result["ctr"] = sum(c * i for c, i in shown) / sum(i for _, i in shown) if shown else None
    for key in ("hook_30s", "avg_view_pct"):
        result[key] = statistics.median(known(key)) if known(key) else None
    return result


def traffic_shares(rows):
    """{Analytics traffic source: share of the rows' views}, largest first."""
    views = {}
    for row in rows:
        for item in (row["d3"] or {}).get("traffic") or []:
            views[item["source"]] = views.get(item["source"], 0) + (item.get("views") or 0)
    total = sum(views.values())
    if not total:
        return {}
    return {k: round(v / total, 3) for k, v in sorted(views.items(), key=lambda kv: (-kv[1], kv[0]))}


def search_terms(rows, limit=15):
    """The viewers' search terms added up over the videos: [{term, views, videos}]."""
    terms = {}
    for row in rows:
        for item in (row["metrics"] or {}).get("search_terms") or []:
            entry = terms.setdefault(item["term"], {"term": item["term"], "views": 0, "videos": 0})
            entry["views"] += item.get("views") or 0
            entry["videos"] += 1
    return sorted(terms.values(), key=lambda t: (-t["views"], t["term"]))[:limit]


def _median_views(rows):
    views = [r["d3"].get("views") for r in rows if r["d3"].get("views") is not None]
    return statistics.median(views) if views else None


def shorts_pairs(rows):
    """Each Shorts beside the episode of its job (both with a d3), and the episodes with and without a Shorts since
    the first pair; a job's first measured episode stands for it (a re-upload shares the job)."""
    measured = [r for r in rows if r["edition_kind"] in {"am", "close"} and r["d3"]]
    episodes = {}
    for row in measured:
        if row["job_id"]:
            episodes.setdefault(row["job_id"], row)
    pairs, paired = [], {}
    for cut in rows:
        episode = episodes.get(cut["job_id"]) if cut["edition_kind"] == "shorts" and cut["d3"] else None
        if episode:
            paired[episode["youtube_id"]] = episode
            pairs.append({"day": episode["published_at"].astimezone(KST).date().isoformat(), "shorts": cut["youtube_id"],
                          "episode": episode["youtube_id"], "shorts_views": cut["d3"].get("views"),
                          "shorts_subscribers": cut["d3"].get("subscribers"),
                          "episode_views": episode["d3"].get("views"), "episode_traffic": traffic_shares([episode])})
    first = min((e["published_at"] for e in paired.values()), default=None)
    without = [e for e in measured if first and e["published_at"] >= first and e["youtube_id"] not in paired]
    return {"pairs": pairs, "with_shorts": {"n": len(paired), "views": _median_views(list(paired.values()))},
            "without": {"n": len(without), "views": _median_views(without)}}


def _features(rows):
    """(name, row → True | False | None) in report order; None leaves a row out of that comparison."""
    durations = [r["duration_s"] for r in rows if r["duration_s"]]
    middle = statistics.median(durations) if durations else None
    return (("question_title", lambda r: title_hook(r["title"]).endswith("?")),
            ("number_in_title", lambda r: bool(re.search(r"\d", title_hook(r["title"])))),
            ("short_thumbnail", lambda r: len(r["plan"]["thumbnail"]) <= 14 if (r["plan"] or {}).get("thumbnail")
             else None),
            ("close_edition", lambda r: r["edition_kind"] == "close"),
            ("long", lambda r: r["duration_s"] >= middle if r["duration_s"] and middle else None),
            ("craft", lambda r: (r["craft"] or 0) >= 1),
            ("lessons", lambda r: bool(r["lessons"])))


def comparisons(rows):
    """Median d3 figures of the episodes with and without each feature; Shorts are left to their own section."""
    rows = [r for r in rows if r["edition_kind"] != "shorts" and r["d3"]]
    keys = ("views", "ctr", "hook_30s", "avg_view_pct")
    result = []
    for name, has in _features(rows):
        marks = [(has(r), r) for r in rows]
        result.append({"feature": name,
                       "with": medians([{"metrics": r["d3"]} for mark, r in marks if mark is True], keys),
                       "without": medians([{"metrics": r["d3"]} for mark, r in marks if mark is False], keys)})
    return result


def report_stats(rows, start, previous_start, before_start):
    """Everything the report states as a number. A period holds the uploads whose d3 snapshot was taken in it as a
    reportable one (not a backfill): this period from the previous report to this one, the previous before it."""
    def period(lo, hi):
        return [r for r in rows if r["d3"] and r["d3"].get("report") and lo <= r["d3_taken"] < hi]

    def kinds(found, figure):
        return {"episodes": figure([r for r in found if r["edition_kind"] != "shorts"]),
                "shorts": figure([r for r in found if r["edition_kind"] == "shorts"])}

    this, previous = period(previous_start, start), period(before_start, previous_start)
    data_day = max((r["window_end"] for r in rows if r["window_end"]), default=None)
    return {"data_day": data_day.isoformat() if data_day else None, "new": [r["youtube_id"] for r in this],
            "this": kinds(this, group_stats), "previous": kinds(previous, group_stats),
            "traffic": {"this": kinds(this, traffic_shares), "previous": kinds(previous, traffic_shares)},
            "search_terms": search_terms(rows), "shorts_pairs": shorts_pairs(rows), "comparisons": comparisons(rows)}


# ---- Monday's benchmark: other channels' public videos that grew fast for their size -----------------------------
# Korea's most popular videos of all categories and of People & Blogs (22), Education (27) and Science & Technology
# (28): any field, as the owner asked (2026-10-22).
BENCHMARK_CATEGORIES = (None, "22", "27", "28")
BENCHMARK_PICK = 5
YOUNG_CHANNEL_DAYS = 365


def benchmark_pick(videos, channels, own_channel, at, limit=BENCHMARK_PICK):
    """The best video of each other channel by views per subscriber, doubled for a channel at most a year old; a
    hidden subscriber count or view count leaves the video out. Ties go to views, then the ID."""
    seen, best = set(), {}
    for video in videos:
        if video["youtube_id"] in seen:
            continue
        seen.add(video["youtube_id"])
        channel = channels.get(video["channel_id"])
        if video["channel_id"] == own_channel or not channel or channel["subscribers"] is None or video["views"] is None:
            continue
        age = (at - channel["created"]).days
        ratio = video["views"] / max(channel["subscribers"], 1)
        item = {**video, "subscribers": channel["subscribers"], "channel_age_days": age, "views_per_subscriber": ratio,
                "score": ratio * (2.0 if age <= YOUNG_CHANNEL_DAYS else 1.0)}
        held = best.get(video["channel_id"])
        if held is None or (-item["score"], -item["views"], item["youtube_id"]) < (-held["score"], -held["views"],
                                                                                    held["youtube_id"]):
            best[video["channel_id"]] = item
    return sorted(best.values(), key=lambda v: (-v["score"], -v["views"], v["youtube_id"]))[:limit]


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
