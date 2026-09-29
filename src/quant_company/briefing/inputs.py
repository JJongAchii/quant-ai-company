"""Reuse registered originals; bounded public collection never treats search snippets as evidence."""

import re
import time
from datetime import UTC, timedelta
from urllib.parse import urlsplit

from ..company import fingerprint
from ..news.contracts import load_sources
from ..news.feeds import fetch_feed, timestamp
from ..news.originals import fetch_original
from .contracts import SourceDocument
from .coverage import PATTERNS, inventory, select_documents, topics

MARKET_WORDS = re.compile(r"stock|market|nasdaq|dow|s&p|kospi|kosdaq|yield|inflation|fed\b|"
                          r"tariff|war\b|sanction|energy|earnings|oil|election|"
                          r"증시|코스피|코스닥|마감|금리|환율|반도체|실적|고용|물가|관세|전쟁|제재|유가|정책", re.I)


def calendars(day):
    return [
        ("fed", "Federal Reserve", "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"),
        ("bls", "BLS", f"https://www.bls.gov/schedule/{day.year}/{day.month:02d}_sched.htm"),
        ("bea", "BEA", "https://www.bea.gov/news/schedule"),
        ("bok", "한국은행", "https://www.bok.or.kr/portal/singl/crncyPolicyDrcMtg/listYear.do?menuNo=200755&mtgSe=A"),
    ]


def registrations(settings):
    return [s for s in load_sources(settings.news_sources_file) if s.enabled and s.use_for_summary]


def market_report(doc, kind):
    text = (doc.title+" "+doc.content) if isinstance(doc, SourceDocument) else doc.get("title", "")+" "+doc.get("content", "")
    title = doc.title if isinstance(doc, SourceDocument) else doc.get("title", "")
    names = (r"S&P|스탠더드|나스닥|Nasdaq|뉴욕증시|stock market" if kind == "am" else r"코스피|코스닥|KOSPI|KOSDAQ")
    published = doc.published_at if isinstance(doc, SourceDocument) else timestamp(doc.get("published_at"))
    published = published.astimezone(UTC) if published else None
    if re.search(r"ETF|상장지수펀드", title, re.I):
        return False
    if kind == "pm" and published and (published.hour, published.minute) < (6, 30):
        return False
    return bool(re.search(names, title, re.I)
                and re.search(r"마감|종가|장종료|clos(?:e|ed|ing)", text, re.I))


def document(receipt, registration, publisher, kind, *, title=None, published=None, origin_group=""):
    if not receipt.get("ok") or receipt.get("content_truncated"):
        return None
    retrieved = timestamp(receipt.get("retrieved_at"))
    if retrieved is None or not receipt.get("content") or not receipt.get("original_sha256"):
        return None
    limit = 3000 if kind == "calendar" else 6000
    content = receipt["content"][:limit]
    return SourceDocument(
        id=fingerprint([registration, receipt["url"], receipt["original_sha256"]])[:40],
        url=receipt["url"], title=title or receipt.get("title", publisher), publisher=publisher, kind=kind,
        content=content, origin_group=origin_group, published_at=published or timestamp(receipt.get("published_at")),
        retrieved_at=retrieved, sha256=receipt["original_sha256"], registration=registration,
        receipt={**{k: v for k, v in receipt.items() if k not in {"content", "links"}},
                 "excerpt_truncated": bool(receipt.get("excerpt_truncated") or len(receipt["content"]) > limit)})


def collect(company, edition, existing=None, candidates=(), *, at, page_fetch=fetch_original,
            article_fetch=fetch_original, feed_fetch=fetch_feed):
    """A small edition-local cache; every accepted source remains attached to the frozen input."""
    specs = registrations(company.settings)
    deadline = time.monotonic() + 210
    spec_map = {s.id: s for s in specs}
    docs = {}
    errors = []
    lower = edition.cutoff-timedelta(hours=72 if edition.weekly else 30)
    for value in [*(existing or {}).get("candidate_documents", []), *(existing or {}).get("documents", [])]:
        doc = SourceDocument.model_validate(value)
        if doc.kind == "calendar" or (doc.registration in spec_map and doc.published_at
                                      and lower <= doc.published_at <= min(at, edition.cutoff)):
            docs[doc.url] = doc
    with company.db.transaction() as conn:
        rows = conn.execute("""WITH originals AS (SELECT DISTINCT ON (a.url) a.*,s.config
            FROM news_articles a JOIN news_sources s ON s.id=a.source_id
            WHERE s.enabled AND s.config->>'use_for_summary'='true' AND a.source_digest=s.config_digest
            AND a.content IS NOT NULL AND a.published_at BETWEEN %s AND %s
            AND a.collected_at<=%s AND (a.title ~* %s OR s.config->>'kind'='official')
            ORDER BY a.url,a.collected_at DESC,a.id DESC), ranked AS (
            SELECT *,row_number() OVER (PARTITION BY COALESCE(NULLIF(config->>'origin_group',''),config->>'publisher')
                ORDER BY published_at DESC,id DESC) AS source_rank FROM originals)
            SELECT * FROM ranked WHERE source_rank<=48 ORDER BY published_at DESC LIMIT 600""",
                            (lower, min(at, edition.cutoff), min(at, edition.cutoff),
                             "|".join(PATTERNS.values()).replace(r"\b", r"\y"))).fetchall()
    for row in rows:
        spec = spec_map.get(row["source_id"])
        receipt = row["retrieval"] or {}
        if not spec or not spec.allows_article(row["url"]) or not receipt.get("ok"):
            continue
        doc = document({**receipt, "url": row["url"], "content": row["content"],
                        "license_url": spec.license_url, "license_name": spec.license_name}, spec.id,
                       spec.publisher, spec.kind, title=row["title"], published=row["published_at"], origin_group=spec.origin_group)
        if doc and doc.retrieved_at <= min(at, edition.cutoff):
            docs[doc.url] = doc
    # A disabled Reporter does not prevent an independently enabled briefing from collecting its inputs.
    urls = []
    if (inventory(list(docs.values()))["missing_topics"] or len(docs) < 8
            or not any(market_report(d, edition.kind) for d in docs.values())):
        preferred = ("cnbc-finance", "yonhap-market", "bbc-world", "cnbc-economy", "yonhap-international",
                     "cnbc-technology", "yonhap-industry", "fed-press", "etoday-global", "sbs-economy")
        primary = sorted(specs, key=lambda s: preferred.index(s.id) if s.id in preferred else 99)[:10]
        for spec in primary:
            if time.monotonic() >= deadline:
                errors.append({"error": "collection_deadline"})
                break
            receipt = feed_fetch(spec)
            if not receipt.get("ok"):
                errors.append({"source": spec.id, "error": receipt.get("error", "feed_failed")})
            for entry in receipt.get("entries", []):
                if (entry.get("published_at") and lower <= entry["published_at"] <= min(at, edition.cutoff)
                        and topics(entry)):
                    urls.append((spec, entry["url"], entry["title"], entry["published_at"]))
    for candidate in candidates:
        spec = next((s for s in specs if s.allows_article(candidate["url"])), None)
        if spec:
            urls.insert(0, (spec, candidate["url"], candidate["title"], None))
    fetched = set()
    # Interleave topics before the fetch budget is spent on many versions of one closing story.
    urls.sort(key=lambda r: (market_report({"title": r[2]}, edition.kind), r[3] is not None), reverse=True)
    diversified = []
    for key in PATTERNS:
        diversified.extend([entry for entry in urls if key in topics({"title": entry[2]})][:2])
    urls = diversified + urls
    for spec, url, title, published in urls:
        if time.monotonic() >= deadline:
            errors.append({"error": "collection_deadline"})
            break
        if url in docs or url in fetched or len(fetched) >= 12:
            continue
        fetched.add(url)
        receipt, _ = article_fetch(url)
        # Revalidate the final redirect, not just the discovered/feed URL.
        if not receipt.get("ok") or not spec.allows_article(receipt.get("url", url)):
            errors.append({"source": spec.id, "url": url, "error": receipt.get("error", "redirect_not_registered")})
            continue
        original_date = timestamp(receipt.get("published_at"))
        if original_date and published and abs(original_date-published) > timedelta(days=1):
            errors.append({"source": spec.id, "error": "publication_date_conflict"})
            continue
        doc = document({**receipt, "license_url": spec.license_url, "license_name": spec.license_name},
                       spec.id, spec.publisher, spec.kind, title=title, published=published, origin_group=spec.origin_group)
        if doc and doc.published_at and lower <= doc.published_at <= min(at, edition.cutoff):
            docs[doc.url] = doc
    for identity, publisher, url in calendars(edition.day):
        if time.monotonic() >= deadline:
            errors.append({"error": "collection_deadline"})
            break
        if url in docs:
            continue
        receipt, _ = page_fetch(url)
        if receipt.get("ok") and urlsplit(receipt["url"]).hostname == urlsplit(url).hostname:
            # Keep the current year's part of long meeting calendars, preserving exact source text.
            content = receipt.get("content", "")
            start = content.find(str(edition.day.year) + " FOMC") if identity == "fed" else 0
            if start > 0:
                receipt = {**receipt, "content": content[start:start+6000]}
            doc = document(receipt, "calendar:"+identity, publisher, "calendar")
            if doc:
                docs[doc.url] = doc
        else:
            errors.append({"source": identity, "url": url, "error": receipt.get("error", "calendar_failed")})
    # Sources completed after the cutoff cannot silently enter a frozen edition.
    eligible = [d for d in docs.values() if d.retrieved_at <= edition.cutoff]
    from .planning import candidates

    originals = [d for d in eligible if d.kind != "calendar"]
    pool = candidates(originals, edition.kind)
    media = select_documents(originals, edition.kind)
    selected = media + [d for d in eligible if d.kind == "calendar"][:4]
    return {"documents": [d.model_dump(mode="json") for d in selected],
            "candidate_documents": [d.model_dump(mode="json") for d in pool],
            "candidate_count": len(originals), "candidate_omitted_count": len(originals)-len(pool),
            "collection_errors": errors,
            "collected_at": at.isoformat(), "source_count": len(selected), "source_coverage": inventory(selected)}


def source_policy(settings):
    return fingerprint([s.model_dump(mode="json") for s in registrations(settings)])
