"""Bounded original-page retrieval. Connect to a checked public address with host TLS validation."""

import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_BYTES = 1024 * 1024
MAX_TEXT = 100000


def public_url(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or any(ord(c) < 33 for c in value):
        raise ValueError("invalid_web_url")
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None
            or url.port not in (None, 443) or "\\" in value):
        raise ValueError("public_https_url_required")
    host = url.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host:
        raise ValueError("non_public_web_host")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("web_ip_literals_not_allowed")
    return urlunsplit(("https", host, url.path or "/", url.query, ""))


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.title, self.links = [], [], []
        self.ignored = 0
        self.in_title = False
        self.published_at = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style", "noscript", "svg"}:
            self.ignored += 1
        if tag == "title":
            self.in_title = True
        if self.ignored:
            return
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr", "article", "section"}:
            self.parts.append("\n")
        if tag == "a" and attrs.get("href") and len(self.links) < 120:
            self.links.append(attrs["href"])
        if tag == "meta" and (attrs.get("property") or attrs.get("name", "")).lower() in {
            "article:published_time", "datepublished", "date", "dc.date.issued", "citation_publication_date",
        }:
            self.published_at = self.published_at or attrs.get("content", "")[:100] or None

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"}:
            self.ignored = max(0, self.ignored - 1)
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.ignored:
            return
        self.parts.append(data)
        if self.in_title:
            self.title.append(data)


class PublicConnection(http.client.HTTPSConnection):
    def connect(self):
        addresses = socket.getaddrinfo(self.host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError("non_public_web_address")
        # DNS is resolved once. The socket uses that checked address; TLS authenticates the original host.
        family, kind, protocol, _, address = addresses[0]
        sock = socket.socket(family, kind, protocol)
        try:
            sock.settimeout(self.timeout)
            sock.connect(address)
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def retrieve(url, *, connection_factory=PublicConnection):
    requested = public_url(url)
    current, redirects = requested, []
    deadline = time.monotonic() + 30
    for _ in range(4):
        target = urlsplit(current)
        connection = connection_factory(target.hostname, timeout=12, context=ssl.create_default_context())
        try:
            connection.request("GET", urlunsplit(("", "", target.path, target.query, "")), headers={
                "User-Agent": "QuantCompanyResearch/1.0", "Accept": "text/html,text/plain,application/xhtml+xml",
                "Accept-Encoding": "identity",
            })
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise ValueError("web_redirect_without_location")
                current = public_url(urljoin(current, location))
                redirects.append(current)
                continue
            if response.status != 200:
                return {"ok": False, "url": requested, "error": "http_status", "http_status": response.status}, None
            content_type = response.getheader("Content-Type", "").lower()
            if content_type.split(";", 1)[0].strip() not in {"text/html", "application/xhtml+xml", "text/plain"}:
                return {"ok": False, "url": requested, "error": "unsupported_content_type",
                        "content_type": content_type[:100]}, None
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("unsupported_web_encoding")
            chunks, size = [], 0
            while size <= MAX_BYTES:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("web_original_timeout")
                if getattr(connection, "sock", None):
                    connection.sock.settimeout(min(12, remaining))
                chunk = response.read1(min(16384, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            raw = b"".join(chunks)
            if len(raw) > MAX_BYTES:
                raise ValueError("web_original_too_large")
            encoding = re.search(r"charset=[\"']?([\w-]+)", content_type)
            try:
                original = raw.decode(encoding[1] if encoding else "utf-8")
            except (UnicodeError, LookupError):
                raise ValueError("web_original_encoding_unknown") from None
            page = Page()
            if "html" in content_type:
                page.feed(original)
                content = "\n".join(" ".join(line.split()) for line in "".join(page.parts).splitlines() if line.strip())
            else:
                content = original
            if not content.strip():
                raise ValueError("empty_web_original")
            links = []
            for href in page.links:
                try:
                    candidate = public_url(urljoin(current, href))
                except (ValueError, UnicodeError):
                    continue
                if candidate not in links:
                    links.append(candidate)
            return {"ok": True, "url": current, "requested_url": requested, "redirects": redirects,
                    "title": " ".join(" ".join(page.title).split())[:500] or target.hostname,
                    "publisher_host": target.hostname, "retrieved_at": datetime.now(UTC).isoformat(),
                    "published_at": page.published_at, "publication_time_basis": "page_metadata" if page.published_at else "unknown",
                    "last_modified": response.getheader("Last-Modified"), "content_type": content_type,
                    "original_sha256": hashlib.sha256(raw).hexdigest(), "original_bytes": len(raw),
                    "content": content[:MAX_TEXT], "content_truncated": len(content) > MAX_TEXT,
                    "links": links[:40]}, raw
        finally:
            connection.close()
    raise ValueError("too_many_web_redirects")


def fetch(url):
    try:
        return retrieve(url)
    except (ValueError, UnicodeError) as exc:
        code = str(exc)
        if not re.fullmatch(r"[a-z_]{3,80}", code):
            code = "invalid_web_url"
    except (OSError, http.client.HTTPException):
        code = "web_network_unavailable"
    return {"ok": False, "url": url, "error": code}, None
