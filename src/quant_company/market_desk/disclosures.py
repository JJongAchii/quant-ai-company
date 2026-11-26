"""Read material listed-company filings from DART's recent disclosures RSS and public viewer."""

import re
import xml.etree.ElementTree as ET
from datetime import timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

import httpx

ORIGIN = "https://dart.fss.or.kr"
MATERIAL = re.compile(r"공급계약|영업정지|거래정지|잠정.*실적|영업.*잠정실적|회사합병|유상증자|생산중단|회생절차|상장폐지")
VIEW = re.compile(r"""viewDoc\(\s*["'](\d+)["'],\s*["'](\d+)["'],\s*["'](\d+)["'],\s*["'](\d+)["'],\s*["'](\d+)["'],\s*["']([\w.]+)["']""")


class Body(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.in_body = False
        self.hidden = False

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self.in_body = True
        elif tag in {"style", "script"}:
            self.hidden = True

    def handle_endtag(self, tag):
        if tag == "body":
            self.in_body = False
        elif tag in {"style", "script"}:
            self.hidden = False

    def handle_data(self, data):
        if self.in_body and not self.hidden and data.strip():
            self.parts.append(data.strip())


def get(client, path, params=None):
    with client.stream("GET", path, params=params) as reply:
        reply.raise_for_status()
        buf = bytearray()
        for chunk in reply.iter_bytes():
            buf.extend(chunk)
            if len(buf) > 1_500_000:
                raise ValueError("oversize DART response")
    return bytes(buf)


def filing_body(client, receipt):
    shell = get(client, "/dsaf001/main.do", {"rcpNo": receipt}).decode("utf-8", "replace")
    args = next((row for row in VIEW.findall(shell) if row[0] == receipt), None)
    if not args:
        return ""
    raw = get(client, "/report/viewer.do", dict(zip(("rcpNo", "dcmNo", "eleId", "offset", "length", "dtd"), args, strict=True)))
    parser = Body()
    codec = "cp949" if re.search(rb"charset\s*=\s*(?:euc-kr|ks_c_5601|cp949)", raw[:4096], re.I) else "utf-8"
    parser.feed(raw.decode(codec))
    return " ".join(" ".join(parser.parts).split())[:6500]


def recent(at):
    with httpx.Client(base_url=ORIGIN, timeout=7, follow_redirects=False) as client:
        items = ET.fromstring(get(client, "/api/todayRSS.xml")).findall("./channel/item")
        docs = []
        for item in items:
            title = item.findtext("title", "")
            if item.findtext("category") not in {"유가", "코스닥"} or not MATERIAL.search(title):
                continue
            match = re.fullmatch(r"https://dart\.fss\.or\.kr/api/link\.jsp\?rcpNo=(\d{14})", item.findtext("link", ""))
            stamp = parsedate_to_datetime(item.findtext("pubDate", ""))
            if not match or not at - timedelta(hours=2) <= stamp <= at:
                continue
            receipt = match[1]
            try:
                body = filing_body(client, receipt)
            except (httpx.HTTPError, ValueError):
                body = ""
            docs.append({"id": "dart-" + receipt, "kind": "filing", "publisher": "금융감독원 DART",
                         "title": title, "url": f"{ORIGIN}/dsaf001/main.do?rcpNo={receipt}",
                         "company": item.findtext("{http://purl.org/dc/elements/1.1/}creator", ""),
                         "published_at": stamp.isoformat(), "content": body})
            if len(docs) == 4:
                break
    return docs
