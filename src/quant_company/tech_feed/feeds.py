"""Use only publisher-supplied feed text; never fetch or summarize an article."""

import re
from html import escape
from urllib.parse import urljoin
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from ..news.feeds import canonical_url, clean, fetch_feed, timestamp
from .contracts import TechFeedItem

MAX_FEED_BYTES = 2 * 1024 * 1024


def parse_feed(raw, source):
    if len(raw) > MAX_FEED_BYTES or b"\x00" in raw:
        raise ValueError("unsafe_or_oversized_feed")
    # Code samples in CDATA/comments may legitimately contain HTML DOCTYPE text.
    declarations = re.sub(br"<!\[CDATA\[.*?\]\]>|<!--.*?-->", b"", raw, flags=re.S)
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)", declarations, re.I):
        raise ValueError("unsafe_feed_declaration")
    root = ET.fromstring(raw)

    def tag(node):
        return node.tag.rsplit("}", 1)[-1]

    if tag(root) not in {"rss", "feed", "RDF"}:
        raise ValueError("not_rss_or_atom")
    nodes = [node for node in root.iter() if tag(node) in {"item", "entry"}]
    if len(nodes) > 2000:
        raise ValueError("too_many_feed_entries")
    entries, skipped = [], 0
    for node in nodes:
        values = {tag(child): "".join(child.itertext()) for child in node}
        link = values.get("link", "").strip()
        if tag(node) == "entry":
            link = next((child.get("href", "") for child in node
                         if tag(child) == "link" and child.get("rel", "alternate") == "alternate"), "")
        try:
            url = canonical_url(urljoin(source.feed_url, link))
            title = clean(values.get("title", ""))[:240]
            categories = [child.get("term", "") or "".join(child.itertext())
                          for child in node if tag(child) == "category"]
            if not link or not title or not source.allows_article(url) or not source.selects(url, title, categories):
                raise ValueError("entry_not_selected")
            published = timestamp(values.get("pubDate") or values.get("published") or values.get("date"))
            updated = timestamp(values.get("updated"))
            description = clean(values.get("description") or values.get("summary")
                                or values.get("content") or values.get("encoded"))
            if len(description) > 160:
                description = description[:159].rstrip() + "…"
            item = TechFeedItem(guid=values.get("guid") or values.get("id") or url, url=url, title=title,
                                description=description, event_at=published or updated,
                                time_kind="published" if published else "updated" if updated else "unknown")
            entries.append(item.model_dump())
        except (ValueError, UnicodeError):
            skipped += 1
    return entries, skipped


def fetch(source, etag=None, modified=None):
    return fetch_feed(source, etag, modified, parser=parse_feed, max_bytes=MAX_FEED_BYTES)


def render(item, source):
    title = escape(item["title"], quote=False).replace("|", "¦")
    url = item["url"].replace("|", "%7C")
    lines = [f"*{escape(source.publisher, quote=False)} · {escape(source.topic, quote=False)}*",
             f"<{url}|{title}>"]
    if item["description"]:
        lines.append(escape(item["description"], quote=False))
    if item["event_at"]:
        stamp = item["event_at"].astimezone(ZoneInfo("Asia/Seoul")).strftime("%m-%d %H:%M KST")
        lines.append(("발행 " if item["time_kind"] == "published" else "수정 ") + stamp)
    return "\n".join(lines)
