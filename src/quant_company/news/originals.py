"""Extract article regions before taking a bounded excerpt for editorial review."""

import json
import re
from html.parser import HTMLParser

from ..web_fetch import MAX_TEXT, fetch

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
IGNORE = {"script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form", "button"}
BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "article", "section"}


class ArticlePage(HTMLParser):
    def __init__(self, host=""):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.regions = []
        self.published_at = None
        self.host = host
        self.jsonld = None
        self.bylines = []
        self.paywalled = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self.jsonld = ""
        if tag == "meta" and attrs.get("name") == "govuk:first-published-at":
            self.published_at = attrs.get("content", "")[:100] or None
        ignored = (bool(self.stack and self.stack[-1][1]) or tag in IGNORE
                   or attrs.get("aria-hidden") == "true" or "hidden" in attrs
                   or bool(re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", attrs.get("style", ""), re.I)))
        rank = (4 if self.host == "world.kbs.co.kr" and "body_txt" in attrs.get("class", "").split() else
                3 if "articleBody" in attrs.get("itemprop", "").split() else
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
        if tag == "script" and self.jsonld is not None:
            try:
                pending = [json.loads(self.jsonld)]
                for _ in range(200):
                    if not pending:
                        break
                    data = pending.pop()
                    if isinstance(data, list):
                        pending.extend(data[:40])
                    elif isinstance(data, dict):
                        kind = data.get("@type", [])
                        kind = [kind] if isinstance(kind, str) else kind
                        if isinstance(kind, list) and set(kind) & {"Article", "NewsArticle", "ReportageNewsArticle"}:
                            if isinstance(data.get("datePublished"), str):
                                self.published_at = self.published_at or data["datePublished"][:100]
                            self.paywalled |= data.get("isAccessibleForFree") in (False, "false", "False")
                            authors = data.get("author", [])
                            authors = authors if isinstance(authors, list) else [authors]
                            for author in authors[:10]:
                                name = author.get("name") if isinstance(author, dict) else author
                                if isinstance(name, str) and name not in self.bylines:
                                    self.bylines.append(name[:150])
                        pending.extend(v for v in data.values() if isinstance(v, (dict, list)))
            except (ValueError, TypeError):
                pass
            self.jsonld = None
        if tag in BLOCK:
            self.handle_data("\n")
        for i in range(len(self.stack)-1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.jsonld is not None:
            self.jsonld = (self.jsonld + data)[:MAX_TEXT]
        if self.stack and self.stack[-1][1]:
            return
        for _, _, region in self.stack:
            if region is not None and region["chars"] <= MAX_TEXT:
                region["parts"].append(data)
                region["chars"] += len(data)

    def article(self):
        usable = [r for r in self.regions if r["chars"] >= 120
                  and (self.host != "world.kbs.co.kr" or r["rank"] == 4)]
        if not usable:
            return "", "missing"
        chosen = max(usable, key=lambda r: (r["rank"], r["chars"]))
        content = "\n".join(" ".join(line.split()) for line in "".join(chosen["parts"]).splitlines() if line.strip())
        return content, {1: "main", 2: "article", 3: "articleBody", 4: "kbs_body_txt"}[chosen["rank"]]


def fetch_original(url):
    receipt, raw = fetch(url)
    if not receipt.get("ok") or raw is None:
        return receipt, raw
    content = receipt["content"]
    method = "plain_text"
    if "html" in receipt.get("content_type", ""):
        encoding = re.search(r"charset=[\"']?([\w-]+)", receipt["content_type"])
        page = ArticlePage(receipt.get("publisher_host", ""))
        page.feed(raw.decode(encoding[1] if encoding else "utf-8"))
        content, method = page.article()
        if page.paywalled:
            return {**receipt, "ok": False, "content": "", "error": "article_requires_subscription"}, raw
        receipt = {**receipt, "bylines": page.bylines}
        if page.published_at and (not receipt.get("published_at") or receipt.get("publisher_host") == "www.gov.uk"):
            receipt = {**receipt, "published_at": page.published_at, "publication_time_basis": "page_metadata"}
    if len(content) < 120:
        return {**receipt, "ok": False, "content": "", "error": "article_body_not_found"}, raw
    return {**receipt, "content": content[:6000], "content_truncated": len(content) > MAX_TEXT,
            "article_extraction": method, "article_chars": len(content), "excerpt_truncated": len(content) > 6000}, raw
