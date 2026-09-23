import http.client
import json
import re
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from ..news.feeds import canonical_url
from .originals import download, parse_html


def collect(source, downloader=download):
    try:
        if source.kind == "seed":
            return {"ok": True, "entries": [{"url": source.url, "title": source.publisher, "metadata": {}}],
                    "basis": "registered_seed_not_verified_original"}
        receipt, raw = downloader(source.url)
        if not receipt["ok"]:
            return receipt
        entries = []
        if source.kind == "atom":
            if b"\x00" in raw or re.search(br"<!\s*(?:DOCTYPE|ENTITY)", raw, re.I):
                raise ValueError("unsafe_quant_feed")
            root = ET.fromstring(raw)
            for node in [n for n in root.iter() if n.tag.rsplit("}", 1)[-1] in {"entry", "item"}]:
                value = {child.tag.rsplit("}", 1)[-1]: "".join(child.itertext()) for child in node}
                url = next((c.get("href") for c in node if c.tag.endswith("}link")
                            and c.get("rel", "alternate") == "alternate"), value.get("link", ""))
                if url.startswith("http://") and urlsplit(url).hostname in source.article_hosts:
                    url = "https://" + url[7:]
                metadata = {"published": value.get("published") or value.get("pubDate"), "updated": value.get("updated"),
                            "doi": value.get("doi"), "authors": ["".join(c.itertext()).strip() for c in node
                                                                    if c.tag.endswith("}author")]}
                entries.append({"url": url, "title": value.get("title", "")[:500], "metadata": metadata})
        elif source.kind == "crossref":
            data = json.loads(raw)
            for item in data["message"]["items"]:
                stamp = item.get("published", {}).get("date-parts", [[]])[0]
                entries.append({"url": item["URL"], "title": (item.get("title") or [""])[0][:500],
                                "metadata": {"doi": item.get("DOI"), "published": "-".join(f"{v:02d}" for v in stamp),
                                             "authors": [" ".join(filter(None, (a.get("given"), a.get("family"))))
                                                         for a in item.get("author", [])][:20],
                                             "metadata_only": True}})
        else:
            _, _, links = parse_html(raw, receipt)
            entries = [{**item, "metadata": {}} for item in links if source.selects(item["url"])
                       and canonical_url(item["url"]) != canonical_url(source.url) and len(item["title"]) > 6]
        unique = {}
        for entry in entries:
            try:
                url = canonical_url(entry["url"])
                if not source.selects(url) or not entry["title"].strip():
                    continue
                unique[url] = {**entry, "url": url}
            except (ValueError, UnicodeError):
                continue
        if source.kind == "html" and not unique:
            return {**receipt, "ok": False, "error": "source_structure_no_candidates"}
        return {**receipt, "entries": list(unique.values()), "coverage": "registered endpoint only"}
    except (ValueError, UnicodeError, KeyError, TypeError, OSError, ET.ParseError, http.client.HTTPException):
        return {"ok": False, "error": "quant_source_unreadable"}


def aliases(url, metadata):
    result = ["url:" + canonical_url(url)]
    doi = metadata.get("doi") or (metadata.get("citation_doi") or [None])[0]
    if doi:
        doi = re.sub(r"^https?://(?:dx\.)?doi.org/", "", str(doi).strip(), flags=re.I)
        if re.fullmatch(r"10\.\d{4,9}/\S{1,300}", doi):
            result.insert(0, "doi:" + doi.lower())
    target = urlsplit(url)
    if target.hostname in {"arxiv.org", "export.arxiv.org"}:
        identifier = re.sub(r"^/(?:abs|pdf)/", "", target.path)
        identifier = re.sub(r"(?:v\d+)?(?:\.pdf)?$", "", identifier)
        if re.fullmatch(r"\d{4}\.\d{4,5}|[a-z-]+/\d{7}", identifier):
            result.insert(0, "arxiv:" + identifier)
    title = metadata.get("citation_title") or []
    authors = metadata.get("citation_author") or metadata.get("authors") or []
    # Exact title+author is only a fallback alias; avoid title-only false merges.
    if title and authors:
        normalized = re.sub(r"\W+", "", str(title[0]).lower())
        if len(normalized) > 20:
            result.append("bibliography:" + normalized + ":" + re.sub(r"\W+", "", str(authors[0]).lower()))
    return list(dict.fromkeys(result))
