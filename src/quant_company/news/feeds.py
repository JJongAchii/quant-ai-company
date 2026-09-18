"""Bounded RSS/Atom reads using the existing public-address-only HTTPS transport."""

import hashlib
import http.client
import re
import ssl
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

from ..web_fetch import MAX_BYTES, Page, PublicConnection, public_url


def canonical_url(value):
    url = urlsplit(public_url(value))
    query = [(k, v) for k, v in parse_qsl(url.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((url.scheme, url.netloc, quote(url.path, safe="/:@-._~!$&'()*+,;=%"), urlencode(sorted(query)), ""))


def clean(value):
    page = Page()
    page.feed(value or "")
    return " ".join(" ".join(page.parts).split())


def timestamp(value):
    if not value:
        return None
    for parser in (lambda v: datetime.fromisoformat(v.replace("Z", "+00:00")), parsedate_to_datetime):
        try:
            result = parser(value.strip())
            if result.tzinfo is not None:
                return result.astimezone(UTC)
        except (ValueError, TypeError, OverflowError):
            pass
    return None


def parse_feed(raw, source):
    if len(raw) > MAX_BYTES or b"\x00" in raw or re.search(br"<!\s*(?:DOCTYPE|ENTITY)", raw, re.I):
        raise ValueError("unsafe_or_oversized_feed")
    root = ET.fromstring(raw)
    def tag(node):
        return node.tag.rsplit("}", 1)[-1]
    if tag(root) not in {"rss", "feed", "RDF"}:
        raise ValueError("not_rss_or_atom")
    entries = [node for node in root.iter() if tag(node) in {"item", "entry"}]
    result, skipped = [], 0
    for node in entries[:200]:
        values = {tag(child): "".join(child.itertext()) for child in node}
        link = values.get("link", "").strip()
        if tag(node) == "entry":
            link = next((child.get("href", "") for child in node
                         if tag(child) == "link" and child.get("rel", "alternate") == "alternate"), "")
        try:
            url = canonical_url(urljoin(source.feed_url, link))
            if not link or urlsplit(url).hostname not in source.article_hosts:
                raise ValueError("article_host_not_allowed")
            title = clean(values.get("title", ""))[:500]
            if not title:
                raise ValueError("missing_title")
        except (ValueError, UnicodeError):
            skipped += 1
            continue
        published = timestamp(values.get("pubDate") or values.get("published") or values.get("date"))
        updated = timestamp(values.get("updated"))
        description = clean(values.get("description") or values.get("summary") or values.get("encoded"))[:6000]
        digest = hashlib.sha256((title + "\n" + description + "\n" + str(updated)).encode()).hexdigest()
        result.append({"url": url, "title": title, "summary": description,
                       "published_at": published, "updated_at": updated, "digest": digest})
    return result, skipped + max(0, len(entries)-200)


def fetch_feed(source, etag=None, modified=None, *, connection_factory=PublicConnection):
    current = source.feed_url
    deadline = time.monotonic() + 30
    try:
        for _ in range(4):
            url = urlsplit(public_url(current))
            # Feed redirects must stay on the registered host. Source changes require configuration.
            if url.hostname != urlsplit(source.feed_url).hostname:
                raise ValueError("feed_redirect_host_changed")
            headers = {"User-Agent": "QuantCompanyNews/1.0", "Accept-Encoding": "identity",
                       "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml"}
            if etag:
                headers["If-None-Match"] = etag
            if modified:
                headers["If-Modified-Since"] = modified
            connection = connection_factory(url.hostname, timeout=12, context=ssl.create_default_context())
            try:
                connection.request("GET", urlunsplit(("", "", url.path, url.query, "")), headers=headers)
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location:
                        raise ValueError("feed_redirect_without_location")
                    current = public_url(urljoin(current, location))
                    continue
                if response.status == 304:
                    return {"ok": True, "not_modified": True, "entries": []}
                if response.status != 200:
                    return {"ok": False, "error": "http_status", "http_status": response.status}
                if response.getheader("Content-Encoding", "identity").lower() != "identity":
                    raise ValueError("unsupported_feed_encoding")
                raw = bytearray()
                while len(raw) <= MAX_BYTES:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ValueError("feed_timeout")
                    if getattr(connection, "sock", None):
                        connection.sock.settimeout(min(12, remaining))
                    chunk = response.read1(min(16384, MAX_BYTES + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                entries, skipped = parse_feed(bytes(raw), source)
                return {"ok": True, "entries": entries, "skipped": skipped,
                        "etag": (response.getheader("ETag") or "")[:500],
                        "modified": (response.getheader("Last-Modified") or "")[:200],
                        "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
            finally:
                connection.close()
        raise ValueError("too_many_feed_redirects")
    except ssl.SSLCertVerificationError:
        return {"ok": False, "error": "feed_tls_verification_failed"}
    except (ValueError, UnicodeError, ET.ParseError):
        return {"ok": False, "error": "feed_parse_or_policy_error"}
    except (OSError, http.client.HTTPException):
        return {"ok": False, "error": "feed_network_unavailable"}
