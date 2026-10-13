"""Public RSS and fixed NAVER API HUB endpoints; credentials never enter receipts."""

import hashlib
import http.client
import json
import math
import re
import unicodedata
from datetime import date, timedelta
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import httpx

from ..news.contracts import load_sources
from ..news.feeds import canonical_url, clean, fetch_feed, timestamp
from ..news.originals import fetch_original
from .contracts import GOOGLE_RSS

MAX_BYTES = 1024 * 1024
NAVER_BASE = "https://naverapihub.apigw.ntruss.com"
NAVER_PATHS = {"trend": "/search-trend/v1/search", "news": "/search/v1/news"}


def keyword_key(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def traffic_floor(value):
    match = re.fullmatch(r"([0-9][0-9,.]*)\s*([KMB만천]?)\+?", value.strip(), re.I)
    if not match:
        return None
    try:
        number = float(match[1].replace(",", ""))
    except ValueError:
        return None
    scale = {"": 1, "K": 1000, "M": 1000000, "B": 1000000000, "만": 10000, "천": 1000}[match[2].upper()]
    return int(number * scale) if math.isfinite(number) and number <= 1e12 else None


def parse_rss(raw, _source=None):
    if len(raw) > MAX_BYTES or b"\x00" in raw or re.search(br"<!\s*(?:DOCTYPE|ENTITY)", raw, re.I):
        raise ValueError("unsafe_trend_feed")
    root = ET.fromstring(raw)
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("not_trends_rss")
    nodes = root.findall("./channel/item")
    if len(nodes) > 200:
        raise ValueError("too_many_trends")
    entries, skipped = [], 0
    ns = {"ht": "https://trends.google.com/trending/rss"}
    for node in nodes:
        title = clean(node.findtext("title", ""))[:240]
        published = timestamp(node.findtext("pubDate"))
        if not title or not published:
            skipped += 1
            continue
        news = []
        for article in node.findall("ht:news_item", ns)[:3]:
            try:
                url = canonical_url(article.findtext("ht:news_item_url", "", ns))
            except (ValueError, UnicodeError):
                continue
            news.append({"url": url, "title": clean(article.findtext("ht:news_item_title", "", ns))[:240],
                         "publisher": clean(article.findtext("ht:news_item_source", "", ns))[:100]})
        traffic = node.findtext("ht:approx_traffic", "", ns).strip()[:40]
        entries.append({"keyword": keyword_key(title), "title": title, "published_at": published,
                        "traffic": traffic, "traffic_floor": traffic_floor(traffic), "news": news})
    return entries, skipped


def fetch_google(etag=None, modified=None):
    captured = {}

    def parse(raw, source):
        entries, skipped = parse_rss(raw, source)
        captured["raw_xml"] = raw.decode("utf-8")
        return entries, skipped

    result = fetch_feed(SimpleNamespace(feed_url=GOOGLE_RSS), etag, modified, parser=parse, max_bytes=MAX_BYTES)
    return {**result, **captured}


class NaverClient:
    def __init__(self, settings, transport=None):
        self.settings, self.transport = settings, transport

    def credentials(self):
        path = self.settings.trend_feed_naver_credentials_file
        if not self.settings.trend_feed_naver_enabled or not path:
            return None
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                return None
            values = [data.get("client_id"), data.get("client_secret")]
            if not all(isinstance(v, str) and v and not v.startswith("REPLACE_") for v in values):
                return None
            return dict(zip(("X-NCP-APIGW-API-KEY-ID", "X-NCP-APIGW-API-KEY"), values, strict=True))
        except (OSError, ValueError):
            return None

    def request(self, kind, payload):
        headers = self.credentials()
        if not headers:
            return {"ok": False, "error": "naver_not_configured"}
        try:
            with httpx.Client(timeout=12, follow_redirects=False, trust_env=False, transport=self.transport) as client:
                args = {"json": payload} if kind == "trend" else {"params": payload}
                with client.stream("POST" if kind == "trend" else "GET", NAVER_BASE + NAVER_PATHS[kind],
                                   headers=headers, **args) as response:
                    if response.status_code != 200:
                        return {"ok": False, "error": "naver_http_error", "http_status": response.status_code}
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_BYTES:
                            raise ValueError("oversized_naver_response")
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        raise ValueError("invalid_naver_response")
                    return {"ok": True, "data": data, "sha256": hashlib.sha256(raw).hexdigest()}
        except (httpx.HTTPError, ValueError, KeyError):
            return {"ok": False, "error": "naver_unavailable_or_invalid"}


def trend_payload(candidates, cutoff):
    end = cutoff.date() - timedelta(days=1)
    return {"startDate": str(end - timedelta(days=27)), "endDate": str(end), "timeUnit": "date",
            "keywordGroups": [{"groupName": c["id"], "keywords": [c["title"]]} for c in candidates]}


def trend_summary(result, payload):
    """Never splice normalized responses; missing/zero baselines remain incomparable."""
    points = {}
    try:
        start, end = date.fromisoformat(payload["startDate"]), date.fromisoformat(payload["endDate"])
        for point in result.get("data", []):
            day, value = date.fromisoformat(point["period"]), point["ratio"]
            if (type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 100
                    or day in points or not start <= day <= end):
                raise ValueError("invalid_trend_point")
            points[day] = value
    except (AttributeError, KeyError, TypeError, ValueError):
        return {"state": "unavailable", "reason": "invalid_series"}
    if not points:
        return {"state": "unavailable", "reason": "no_data"}
    last = max(points)
    summary = {"state": "unavailable", "as_of": str(last), "window": payload,
               "points": [{"period": str(day), "ratio": value} for day, value in sorted(points.items())]}
    days = [last - timedelta(days=n) for n in range(14)]
    if any(day not in points for day in days):
        return {**summary, "reason": "incomplete_comparison"}
    recent = sum(points[day] for day in days[:7]) / 7
    prior = sum(points[day] for day in days[7:]) / 7
    if prior == 0:
        return {**summary, "reason": "zero_baseline"}
    return {**summary, "state": "available", "recent_mean": recent, "prior_mean": prior,
            "change_percent": (recent / prior - 1) * 100}


def article_source(url, settings):
    # Reuse publication permissions, but not the hot-news topic selector.
    return next((s for s in load_sources(settings.news_sources_file)
                 if s.enabled and s.use_for_summary and s.kind == "media" and s.allows_article(url)), None)


def read_article(link, settings):
    source = article_source(link["url"], settings)
    if not source:
        return {"ok": False, "error": "unregistered_original"}
    try:
        result, _ = fetch_original(link["url"])
        if not result.get("ok") or any(not source.allows_article(url) for url in
                                       [result.get("url", link["url"]), *result.get("redirects", [])]):
            return {"ok": False, "error": "original_unavailable_or_redirect_not_allowed"}
        return {**result, "publisher": source.publisher, "origin_group": source.origin_group}
    except (ValueError, OSError, http.client.HTTPException):
        return {"ok": False, "error": "original_unavailable"}
