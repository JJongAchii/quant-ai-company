import hashlib
import http.client
import json
import re
import ssl
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from ..web_fetch import Page, PublicConnection, public_url

MAX_BYTES = 20 * 1024 * 1024


def download(url, *, allows=None, connection_factory=PublicConnection):
    requested = public_url(url)
    current, redirects, deadline = requested, [], time.monotonic() + 60
    for _ in range(4):
        if allows is not None and not allows(current):
            raise ValueError("quant_unregistered_redirect")
        target = urlsplit(current)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("quant_download_timeout")
        connection = connection_factory(target.hostname, timeout=min(12, remaining), context=ssl.create_default_context())
        try:
            connection.request("GET", urlunsplit(("", "", target.path, target.query, "")), headers={
                "User-Agent": "QuantCompanyResearch/1.0 (public research curation)", "Accept-Encoding": "identity",
                "Accept": "application/pdf,text/html,application/atom+xml,application/xml,application/json,text/plain,*/*;q=0.1"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                if not response.getheader("Location"):
                    raise ValueError("quant_redirect_without_location")
                current = public_url(urljoin(current, response.getheader("Location")))
                redirects.append(current)
                continue
            if response.status != 200:
                retry = response.getheader("Retry-After", "")
                return {"ok": False, "error": "http_status", "http_status": response.status,
                        "retry_after": min(604800, max(3600, int(retry))) if retry.isdigit() else 3600}, None
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("quant_unsupported_encoding")
            content_type = response.getheader("Content-Type", "").lower()
            limit = MAX_BYTES if "pdf" in content_type or target.path.lower().endswith(".pdf") else 2 * 1024 * 1024
            raw = bytearray()
            while len(raw) <= limit:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("quant_download_timeout")
                if getattr(connection, "sock", None):
                    connection.sock.settimeout(min(12, remaining))
                chunk = response.read1(min(16384, limit + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            if len(raw) > limit:
                raise ValueError("quant_original_too_large")
            return {"ok": True, "url": current, "requested_url": requested, "redirects": redirects,
                    "retrieved_at": datetime.now(UTC).isoformat(), "content_type": content_type,
                    "original_sha256": hashlib.sha256(raw).hexdigest(), "original_bytes": len(raw)}, bytes(raw)
        finally:
            connection.close()
    raise ValueError("quant_too_many_redirects")


class ResearchPage(Page):
    def __init__(self):
        super().__init__()
        self.metadata, self.labeled_links, self.anchor = {}, [], None

    def handle_starttag(self, tag, attrs):
        if tag in {"nav", "footer"}:
            self.ignored += 1
        super().handle_starttag(tag, attrs)
        attrs = dict(attrs)
        if tag == "meta":
            name = (attrs.get("name") or attrs.get("property") or "").lower()
            if name.startswith(("citation_", "dc.", "article:")):
                self.metadata.setdefault(name, []).append(attrs.get("content", "")[:1000])
        if not self.ignored and tag == "a" and attrs.get("href"):
            href = attrs["href"]
            # KCMI exposes public report URLs through a fixed numeric onclick, never execute JS.
            report = re.fullmatch(r"viewpage\((\d+),\s*[\"']\./report_view[\"'],\s*[\"']_self[\"']\);?",
                                  attrs.get("onclick", ""))
            if report:
                href = "/report/report_view?report_no=" + report[1]
            self.anchor = {"url": href, "title": ""}

    def handle_data(self, data):
        super().handle_data(data)
        if self.anchor is not None and len(self.anchor["title"]) < 500:
            self.anchor["title"] += data

    def handle_endtag(self, tag):
        super().handle_endtag(tag)
        if tag in {"nav", "footer"}:
            self.ignored = max(0, self.ignored - 1)
        if tag == "a" and self.anchor is not None:
            if len(self.labeled_links) < 1000:
                self.labeled_links.append(self.anchor)
            self.anchor = None


def parse_html(raw, receipt):
    charset = re.search(r"charset=[\"']?([\w-]+)", receipt["content_type"])
    text = raw.decode(charset[1] if charset else "utf-8")
    page = ResearchPage()
    page.feed(text)
    content = "\n".join(" ".join(line.split()) for line in "".join(page.parts).splitlines() if line.strip())
    links = []
    for item in page.labeled_links:
        try:
            links.append({"url": public_url(urljoin(receipt["url"], item["url"])),
                          "title": " ".join(item["title"].split())[:500]})
        except (ValueError, UnicodeError):
            continue
    return page, content, links


def extract_pdf(raw):
    # -I ignores Python/user environment. Child gets no Slack/database/auth environment.
    result = subprocess.run([sys.executable, "-I", str(Path(__file__).with_name("pdf_extract.py"))],
                            input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=60, cwd="/", env={"LANG": "C.UTF-8"})
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError("quant_pdf_unreadable_or_resource_limit")
    return json.loads(result.stdout)


def fetch_original(url, source, *, downloader=download, pdf_parser=extract_pdf):
    try:
        receipt, raw = downloader(url, allows=source.allows)
        if not receipt["ok"]:
            return receipt
        metadata, links = {}, []
        if raw.startswith(b"%PDF-"):
            extracted = pdf_parser(raw)
        elif "html" in receipt["content_type"] or receipt["content_type"].startswith("text/plain"):
            page, content, links = parse_html(raw, receipt)
            metadata = page.metadata
            metadata["page_title"] = [" ".join(page.title)[:500]]
            if re.search(r"not found|does not exist|access denied|page unavailable|페이지를 찾을 수", metadata["page_title"][0], re.I):
                raise ValueError("quant_soft_error_page")
            pdfs = metadata.get("citation_pdf_url", []) + [x["url"] for x in links
                    if (".pdf" in x["url"].lower() or "/common/download" in x["url"] or "/fileDown.do" in x["url"])
                    and not re.search(r"privacy|terms|disclaimer|cookie|legal|개인정보|이용약관", x["title"] + x["url"], re.I)]
            if urlsplit(receipt["url"]).hostname == "arxiv.org" and "/abs/" in receipt["url"]:
                pdfs.insert(0, receipt["url"].replace("/abs/", "/pdf/"))
            nber = re.fullmatch(r"/papers/(w\d+)/?", urlsplit(receipt["url"]).path)
            if urlsplit(receipt["url"]).hostname == "www.nber.org" and nber:
                pdfs.insert(0, f"https://www.nber.org/system/files/working_papers/{nber[1]}/{nber[1]}.pdf")
            extracted = None
            # At most two public author/publisher copies. No recursive crawl or login bypass.
            errors = []
            for pdf in list(dict.fromkeys(pdfs))[:2]:
                try:
                    pdf = public_url(urljoin(receipt["url"], pdf))
                    if not source.allows(pdf):
                        continue
                    pdf_receipt, pdf_raw = downloader(pdf, allows=source.allows)
                    if pdf_receipt["ok"] and pdf_raw.startswith(b"%PDF-"):
                        extracted = pdf_parser(pdf_raw)
                        pdf_receipt["landing_receipt"] = receipt
                        receipt = pdf_receipt
                        break
                    errors.append(pdf_receipt.get("error", "not_pdf"))
                except (ValueError, OSError, subprocess.TimeoutExpired):
                    errors.append("pdf_extraction_failed")
            if extracted is None:
                extracted = {"pages": [{"location": f"HTML §{i // 3000 + 1}", "text": content[i:i+3000]}
                                       for i in range(0, min(len(content), 150000), 3000)],
                             "truncated": len(content) > 150000}
                receipt["pdf_errors"] = errors
                receipt["fulltext_status"] = "html_requires_evidence_check"
        else:
            raise ValueError("quant_unsupported_original_type")
        if not extracted["pages"] or sum(len(p["text"]) for p in extracted["pages"]) < 500:
            raise ValueError("quant_original_insufficient")
        return {**receipt, **extracted, "metadata": metadata, "links": links}
    except (ValueError, UnicodeError, OSError, http.client.HTTPException, subprocess.TimeoutExpired) as exc:
        code = str(exc)
        return {"ok": False, "error": code if re.fullmatch(r"[a-z_]{3,80}", code) else "quant_original_unavailable"}
