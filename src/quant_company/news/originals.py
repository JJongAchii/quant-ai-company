"""Extract article regions before taking a bounded excerpt for editorial review."""

import re
from html.parser import HTMLParser

from ..web_fetch import MAX_TEXT, fetch

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
IGNORE = {"script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form", "button"}
BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "article", "section"}


class ArticlePage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.regions = []
        self.published_at = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and attrs.get("name") == "govuk:first-published-at":
            self.published_at = attrs.get("content", "")[:100] or None
        ignored = (bool(self.stack and self.stack[-1][1]) or tag in IGNORE
                   or attrs.get("aria-hidden") == "true")
        rank = (3 if "articleBody" in attrs.get("itemprop", "").split() else
                2 if tag == "article" or attrs.get("id") == "article" else
                1 if tag == "main" or attrs.get("role") == "main" else 0)
        region = None
        if rank and not ignored and len(self.regions) < 32:
            region = {"rank": rank, "parts": [], "chars": 0}
            self.regions.append(region)
        if tag not in VOID:
            self.stack.append((tag, ignored, region))
        if tag in BLOCK and not ignored:
            self.handle_data("\n")

    def handle_endtag(self, tag):
        if tag in BLOCK:
            self.handle_data("\n")
        for i in range(len(self.stack)-1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.stack and self.stack[-1][1]:
            return
        for _, _, region in self.stack:
            if region is not None and region["chars"] <= MAX_TEXT:
                region["parts"].append(data)
                region["chars"] += len(data)

    def article(self):
        usable = [r for r in self.regions if r["chars"] >= 120]
        if not usable:
            return "", "missing"
        chosen = max(usable, key=lambda r: (r["rank"], r["chars"]))
        content = "\n".join(" ".join(line.split()) for line in "".join(chosen["parts"]).splitlines() if line.strip())
        return content, {1: "main", 2: "article", 3: "articleBody"}[chosen["rank"]]


def fetch_original(url):
    receipt, raw = fetch(url)
    if not receipt.get("ok") or raw is None:
        return receipt, raw
    content = receipt["content"]
    method = "plain_text"
    if "html" in receipt.get("content_type", ""):
        encoding = re.search(r"charset=[\"']?([\w-]+)", receipt["content_type"])
        page = ArticlePage()
        page.feed(raw.decode(encoding[1] if encoding else "utf-8"))
        content, method = page.article()
        if page.published_at and receipt.get("publisher_host") == "www.gov.uk":
            receipt = {**receipt, "published_at": page.published_at, "publication_time_basis": "page_metadata"}
    if len(content) < 120:
        return {**receipt, "ok": False, "content": "", "error": "article_body_not_found"}, raw
    return {**receipt, "content": content[:6000], "content_truncated": len(content) > MAX_TEXT,
            "article_extraction": method, "article_chars": len(content), "excerpt_truncated": len(content) > 6000}, raw
