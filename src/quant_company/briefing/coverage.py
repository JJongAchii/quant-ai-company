"""Bounded topic sampling, not a claim that keyword matches establish materiality."""

import re
from difflib import SequenceMatcher

COVERAGE_VERSION = 4

PATTERNS = {
    "market": r"stocks?|markets?|nasdaq|dow\b|s&p|kospi|kosdaq|증시|코스피|코스닥|장종료",
    "macro_policy": r"inflation|payroll|fed\b|\brates?\b|central bank|tariffs?|금리|물가|(?<![가-힣])고용|연준|(?<![가-힣])한은|한국은행|정책|관세|부채",
    "geopolitics": r"iran|war\b|ceasefire|sanction|strait|trump.*xi|이란|전쟁|휴전|제재|호르무즈|미중|미·중|중동|우크라",
    "corporate": r"earnings|profit|nvidia|chips?|\bai\b|meta|merger|기업|실적|반도체|빅테크|인텔|삼성|하이닉스|엔비디아|전력기기",
    "cross_asset": r"oil|crude|gold|treasur|yield|dollar|bitcoin|유가|원유|국채|채권|환율|달러|비트코인|금값",
}
NOISE = re.compile(r"^\[(?:인사|부고|게시판|표)\]|^\[특징주\].*상장 첫날|ETF 구성종목.*교체|"
                   r"채용|체육관|시세표|상장예비심사|상장예심|"
                   r"신규 상장|시민단체|준공|기탁|나눔|basketball|football|olympic|asian games", re.I)


def priority(title):
    # Prefer economy-wide/market developments over local publicity with incidental finance keywords.
    patterns = (r"증시|코스피|코스닥|nasdaq|s&p|stocks|markets",
                r"연준|(?<![가-힣])한은|한국은행|물가|기준금리|(?<![가-힣])고용|inflation|payroll|federal reserve|central bank",
                r"호르무즈|전쟁|이란|미중|미·중|관세|제재|iran|tariff|sanction|trump.*xi",
                r"유가|국채|환율|oil|crude|treasury|yields",
                r"실적|급등|급락|전력기기|수요|가이던스|earnings|profit|nvidia|meta|merger",
                r"\d[\d,]*(?:조|trillion)\b|자본 부담|capital burden")
    return sum(bool(re.search(p, title, re.I)) for p in patterns)


def topics(doc):
    title = doc.title if hasattr(doc, "title") else doc.get("title", "")
    return {key for key, pattern in PATTERNS.items() if re.search(pattern, title, re.I)}


def inventory(documents):
    relevant = [d for d in documents if d.kind not in {"calendar", "dataset"}]
    covered = {key: [d.id for d in relevant if key in topics(d)] for key in PATTERNS}
    return {"topic_sources": covered, "missing_topics": [k for k, ids in covered.items() if not ids],
            "meaning": "Headline-based collection coverage only; the reviewer must assess materiality and omissions."}


def select_documents(documents, kind, limit=16):
    from .inputs import market_report

    ranked = sorted(documents, key=lambda d: (not bool(NOISE.search(d.title)), priority(d.title),
                    bool(topics(d)), d.published_at or d.retrieved_at, d.id), reverse=True)
    selected, hashes = [], set()

    def duplicate(doc):
        if doc.sha256 in hashes:
            return True
        title = re.sub(r"\[[^]]*\]|\([^)]*\)", "", doc.title)
        for existing in selected:
            other = re.sub(r"\[[^]]*\]|\([^)]*\)", "", existing.title)
            similarity = SequenceMatcher(None, other, title).ratio()
            same_origin = (existing.origin_group or existing.publisher) == (doc.origin_group or doc.publisher)
            if same_origin and similarity > .72:
                return True
            if similarity > .9 and not (market_report(existing, kind) and market_report(doc, kind)):
                return True
        return False

    def add(doc):
        if len(selected) < limit and not duplicate(doc):
            selected.append(doc)
            hashes.add(doc.sha256)

    # Reserve the session report and independent corroboration before sampling other topics.
    closing_origins = set()
    for doc in ranked:
        origin = doc.origin_group or doc.publisher
        if (market_report(doc, kind) and "market" in topics(doc)
                and not NOISE.search(doc.title) and origin not in closing_origins):
            add(doc)
            closing_origins.add(origin)
            if len(closing_origins) == 2:
                break
    # A market-wide flow story is distinct from the session close. Preserve one
    # when the two close slots would otherwise exhaust the market topic sample.
    if kind == "pm":
        for doc in ranked:
            if (not NOISE.search(doc.title)
                    and re.search(r"ETF|상장지수펀드|외국인|기관|수급", doc.title, re.I)
                    and re.search(r"순유출|순유입|순매수|순매도|자금.{0,20}(?:유출|유입|이탈)",
                                  doc.title+" "+doc.content, re.I)):
                before = len(selected)
                add(doc)
                if len(selected) > before:
                    break
    for key in PATTERNS:
        if key == "market" and len(closing_origins) == 2:
            continue
        candidates = [d for d in ranked if key in topics(d) and not NOISE.search(d.title)]
        if key != "market":
            candidates.sort(key=lambda d: (market_report(d, kind),
                            key in {"macro_policy", "geopolitics"} and "cross_asset" in topics(d)))
        count = 0
        for doc in candidates:
            before = len(selected)
            add(doc)
            count += len(selected)-before
            if count == 2:
                break
    for cap in (3, limit):
        for doc in ranked:
            origin = doc.origin_group or doc.publisher
            if (not NOISE.search(doc.title) and topics(doc)
                    and sum((d.origin_group or d.publisher) == origin for d in selected) < cap):
                add(doc)
    return selected
