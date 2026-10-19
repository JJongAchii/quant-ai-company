"""Deterministic checks of an email draft against the delivered brief body, and the fallback built from that body.

Numbers: each number the writer wrote must be in the body in the same unit, or that body number rounded to fewer
digits (3.17% -> 3.2%; 18조1,308억원 -> 18조원; truncation only before '넘게/이상/여'). Korean scale words fold into the
value, so 18조1,308억원 never licenses 18억원 or 1,308조원. A change or an amount also keeps its direction: the sign or
the 상승/하락, 순매수/순매도 word next to it must agree with the body's. Wording: no advice, no production/tool wording
and no links or footnotes; a phrase the body itself uses (a topic such as '생성형 AI') is allowed."""

import re
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from ..video.upload import TOOL_WORDING
from .contracts import CalendarRow, IssueBlock, Market, NewsletterDraft, Swing, body_text, plain, sentences

PART = r"\d+(?:\.\d+)?\s*천(?:\s*\d{1,3}(?!\d))?|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
SCALES = {"조": 12, "억": 8, "만": 4}
CURRENCY = r"(?P<cur>\s*(?:원|달러|위안|엔|유로|명|개|주|건|배럴|톤|대|척))?"
ISO = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
DATE = re.compile(r"(?<![\d.,/])(\d{1,2})/(\d{1,2})(?![\d/])")
CLOCK = re.compile(r"(?<![\d.,:])(\d{1,2}):(\d{2})(?![\d:])")
SCALED = re.compile(
    rf"(?P<jo>{PART})\s*조(?:\s*(?P<jo_eok>{PART})\s*억)?(?:\s*(?P<jo_man>{PART})\s*만)?{CURRENCY}"
    rf"|(?P<eok>{PART})\s*억(?:\s*(?P<eok_man>{PART})\s*만)?(?P<cur2>\s*(?:원|달러|위안|엔|유로|명|개|주|건|배럴|톤|대|척))?"
    rf"|(?P<man>{PART})\s*만(?=\s|원|달러|명|개|주|건|[^가-힣]|$)(?P<cur3>\s*(?:원|달러|명|개|주|건))?")
UNITS = ("%포인트", "퍼센트포인트", "%p", "%", "퍼센트", "bps", "bp", "pt", "포인트", "배럴", "배", "원", "달러", "위안", "엔",
         "유로", "명", "개월", "개", "주", "건", "곳", "척", "대", "년", "월", "일", "시", "분기", "분", "초", "거래일", "차례",
         "번째", "위", "세", "톤")
NORMAL_UNIT = {"%포인트": "%p", "퍼센트포인트": "%p", "퍼센트": "%", "bps": "bp", "포인트": "pt"}
PLAIN = re.compile(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(\s*(?:" + "|".join(map(re.escape, UNITS)) + "))?")
MORE = re.compile(r"\s*(?:넘게|넘는|넘어|이상|여|남짓|초과|[을를가이]\s*넘)")
RAW = "~"  # a component of a compound body amount: matches only a unitless email number
# Direction of a change or an amount: its own sign, else the first direction word after it in the same clause (no other
# number, sentence end or list mark in between), else a board label right before it ('기관 순매수 5,731 억원').
UP = re.compile(r"상승|오른|올라|오르|급등|반등|증가|늘어|늘었|늘린|순매수|매수\s*우위")
DOWN = re.compile(r"하락|내린|내려|내리|떨어|급락|감소|줄어|줄었|줄인|순매도|매도\s*우위")
CLAUSE_END = re.compile(r"[\d.!?,·;()\n]")
LABEL = re.compile(r"(순매수|순매도|상승|하락)\s*$")
CHANGE_UNITS = frozenset({"%", "%p", "bp"})

# Advice, register and citation wording. Production/tool wording is the video's TOOL_WORDING plus the email's own.
ADVICE = re.compile(
    r"(?:매수|매도|매집|손절|익절|비중\s*(?:확대|축소))\s*(?:를|을)?\s*(?:추천|권유|권장|권합니다|하세요|하십시오|하시길|"
    r"해야\s*합니다|하는\s*것이\s*좋|할\s*때입니다|할\s*기회|기회입니다)"
    r"|사세요|파세요|사야\s*합니다|팔아야\s*합니다|담으세요|담아\s*두|올라타|추천\s*종목|유망\s*종목|투자\s*(?:추천|권유)|"
    r"수익\s*보장|목표\s*수익률|목표\s*주가를\s*제시")
TOOLS = re.compile(r"AI가\s*(?:쓴|작성|정리|분석|요약|만든)|인공지능|챗봇|ChatGPT|챗GPT|GPT|OpenAI|오픈AI|Anthropic|"
                   r"앤트로픽|언어\s*모델|LLM|자동\s*작성|자동으로\s*(?:작성|정리)|모델이\s*(?:쓴|작성|정리)")
COLLOQUIAL = re.compile(r"(?:어요|아요|예요|에요|해요|네요|군요|래요|죠)(?=[.!?…\s]|$)")
CITATION = re.compile(r"https?://|www\.|\[\d+\]|\(출처|출처\s*[:·]|근거\s*[:·]")


@dataclass(frozen=True)
class Quantity:
    value: Decimal
    unit: str
    exp: int
    text: str
    more: bool = False
    direction: str | None = None


def exponent(literal):
    """Position of the last significant digit; trailing zeros count only when two or more digits remain."""
    digits = literal.replace(",", "")
    if "." in digits:
        return -len(digits.split(".")[1])
    kept = digits.rstrip("0")
    return len(digits) - len(kept) if len(kept.lstrip("0")) >= 2 else 0


def part(literal):
    """'5천' -> (5000, 3), '1천308' -> (1308, 0), '5,731' -> (5731, 0)."""
    literal = re.sub(r"\s+", "", literal)
    if "천" in literal:
        head, _, tail = literal.partition("천")
        if tail:
            return Decimal(head) * 1000 + Decimal(tail), 0
        return Decimal(head) * 1000, 3 + exponent(head)
    return Decimal(literal.replace(",", "")), exponent(literal)


def flat(text):
    """plain() per line: lines stay apart, so a direction word never reaches across two body lines."""
    return "\n".join(plain(line) for line in (text or "").split("\n"))


def direction(text, start, end):
    sign = text[max(0, start - 1):start]
    if sign in {"+", "▲"}:
        return "up"
    if sign in {"-", "−", "▼"}:
        return "down"
    clause = CLAUSE_END.split(text[end:end + 24], maxsplit=1)[0]
    found = sorted((m.start(), name) for name, pattern in (("up", UP), ("down", DOWN)) if (m := pattern.search(clause)))
    if found:
        return found[0][1]
    label = LABEL.search(text[max(0, start - 8):start])
    return None if not label else "up" if label[1] in {"순매수", "상승"} else "down"


def quantities(text, *, raw=False):
    """Every number of TEXT with its unit, and the direction of changes and amounts. With RAW, compound amounts also
    yield their components (body side)."""
    text = flat(text)
    found, blank = [], list(text)

    def take(match):
        for i in range(match.start(), match.end()):
            blank[i] = " "

    def more(end):
        return bool(MORE.match(text, end))

    for match in ISO.finditer(text):
        for value, unit in zip(match.groups(), ("년", "월", "일"), strict=True):
            found.append(Quantity(Decimal(value), unit, 0, match[0]))
            if raw:
                found.append(Quantity(Decimal(value), RAW, 0, match[0]))
        take(match)
    for pattern, units in ((DATE, ("월", "일")), (CLOCK, ("시", "분"))):
        for match in pattern.finditer("".join(blank)):
            for value, unit in zip(match.groups(), units, strict=True):
                found.append(Quantity(Decimal(value), unit, 0, match[0]))
                if raw:
                    found.append(Quantity(Decimal(value), RAW, 0, match[0]))
            take(match)
    for match in SCALED.finditer("".join(blank)):
        groups = match.groupdict()
        parts = [(name, groups[name]) for name in ("jo", "jo_eok", "jo_man", "eok", "eok_man", "man") if groups[name]]
        value, exp = Decimal(0), 0
        for name, literal in parts:
            amount, digits = part(literal)
            scale = SCALES[{"jo": "조", "eok": "억", "man": "만"}[name.split("_")[-1]]]
            value += amount * Decimal(10) ** scale
            exp = digits + scale
            if raw:
                found.append(Quantity(amount, RAW, digits, match[0]))
        unit = (groups["cur"] or groups["cur2"] or groups["cur3"] or "").strip()
        found.append(Quantity(value, unit, exp, match[0].strip(), more(match.end()),
                              direction(text, match.start(), match.end())))
        take(match)
    for match in PLAIN.finditer("".join(blank)):
        unit = NORMAL_UNIT.get((match[2] or "").strip(), (match[2] or "").strip())
        found.append(Quantity(Decimal(match[1].replace(",", "")), unit, exponent(match[1]), match[0].strip(),
                              more(match.end()),
                              direction(text, match.start(), match.end()) if unit in CHANGE_UNITS else None))
    return found


def compatible(mine, theirs):
    if theirs.unit == RAW:
        return mine.unit == ""
    return mine.unit == theirs.unit or not mine.unit or not theirs.unit


def matches(mine, theirs):
    a, b = abs(mine.value), abs(theirs.value)
    if a == b:
        return True
    if mine.exp <= theirs.exp:
        return False
    step = Decimal(10) ** mine.exp
    if mine.more:  # '18조원 넘게' states a floor: 18조1,308억원 qualifies, 17조9,000억원 does not
        return (b / step).to_integral_value(ROUND_DOWN) * step == a
    return (b / step).quantize(Decimal(1), ROUND_HALF_UP) * step == a


def unsupported_numbers(draft, source_text):
    allowed = quantities(source_text, raw=True)
    findings = []
    for field, text in draft.texts():
        for quantity in quantities(text):
            found = [other for other in allowed if compatible(quantity, other) and matches(quantity, other)]
            if not found:
                findings.append(f"{field}: '{quantity.text}' 는 브리핑 본문에 없는 숫자입니다(같은 단위의 반올림만 허용)")
                continue
            # The body's own directions for this value; a value it never states a direction for binds none.
            known = {other.direction for other in found if other.direction}
            if quantity.direction and known and quantity.direction not in known:
                findings.append(f"{field}: '{quantity.text}' 의 방향(부호, 상승·하락, 순매수·순매도)이 브리핑 본문과 반대입니다")
    return findings


def wording(draft, source_text):
    shown = plain(source_text)
    findings = []
    for field, text in draft.texts():
        for label, pattern in (("매수·매도 권유 표현", ADVICE), ("AI·모델·도구 언급", TOOL_WORDING), ("AI·모델·도구 언급", TOOLS),
                               ("구어체 어미", COLLOQUIAL)):
            for match in pattern.finditer(text):
                if match[0] not in shown:
                    findings.append(f"{field}: {label} '{match[0]}'")
        for match in CITATION.finditer(text):
            findings.append(f"{field}: 출처·링크·각주 표시 '{match[0]}'는 넣지 않습니다")
    return findings


def structure(draft, source):
    findings = []
    # Issues the reader actually saw in the body, not the proposal's count (a pruned issue never reached Slack).
    required = min(2, len(source["issues"]))
    if len(draft.issues) < required:
        findings.append(f"issues: 주요 이슈는 {required}개 이상이어야 합니다")
    if source["calendar"] and not draft.calendar:
        findings.append("calendar: 브리핑에 일정이 있으므로 주요 일정 블록이 필요합니다")
    if not re.search(r"\d", draft.headline + draft.accent):
        findings.append("headline: 제목에 그날 두드러진 사실의 숫자가 필요합니다")
    body = " ".join(text for field, text in draft.texts() if not field.startswith("market.glossary"))
    for term in draft.market.glossary:
        if term.term not in body:
            findings.append(f"market.glossary: '{term.term}'는 메일 본문에 쓰인 용어만 풀이합니다")
    return findings


def findings(draft, source):
    text = body_text(source)
    return list(dict.fromkeys(unsupported_numbers(draft, text) + wording(draft, text) + structure(draft, source)))


# ---- Fallback -------------------------------------------------------------------------------------------------

KOSPI = re.compile(r"^코스피 (\d{1,3}(?:,\d{3})*(?:\.\d+)?) pt(?: · (?:보도상 )?([+-])(\d+(?:\.\d+)?)%)?")
BOARD_TRAILER = re.compile(r" · (?:정규장 종가|관측값|시간외|잠정|최근 24시간) \d{2}/\d{2} \d{2}:\d{2} KST(?: · [^·]+)?$")


def clip(text, limit):
    """Whole sentences up to LIMIT characters; a single longer sentence is cut at a space and marked."""
    if len(text) <= limit:
        return text.strip()
    kept = ""
    for sentence in sentences(text):
        if len(kept) + len(sentence) + 1 > limit:
            break
        kept = f"{kept} {sentence}".strip()
    if kept:
        return kept
    cut = text[:limit - 1].rsplit(" ", 1)[0]
    return cut + "…"


def day_label(when, weekdays):
    """'10/20 06:00' -> '20일 (화)' as the approved layout shows dates; anything else stays as the body has it."""
    match = re.match(r"(\d{2})/(\d{2})", when)
    if not match or match[0] not in weekdays:
        return when
    return f"{int(match[2])}일 ({weekdays[match[0]]})"


def first(text, limit):
    return clip((sentences(text) or [text])[0], limit)


def fallback(source):
    """The same layout from the body's own text: 30-second summary, numbers board, each issue's headline with the
    first fact sentence and its next check, and the calendar (design §3.2)."""
    sections = {}
    for line in source["lines"]:
        sections.setdefault(line["section"], []).append(line["text"])
    # The board's lines without their timestamp and venue trailers (' · 정규장 종가 10/19 15:30 KST · KRX').
    board = [BOARD_TRAILER.sub("", t) for t in sections.get("주요 숫자", []) if not t.startswith("주요 숫자")]
    kospi = next((KOSPI.match(t) for t in board if KOSPI.match(t)), None)
    summary = source["summary"] or sections.get("오늘의 핵심", [])
    opening = first(summary[0], 120) if summary else "오늘 장 마감 브리핑의 주요 내용입니다."
    if kospi and kospi[3]:
        move = "상승" if kospi[2] == "+" and float(kospi[3]) else "하락" if float(kospi[3]) else "보합"
        headline, accent = f"코스피 {kospi[3]}% {move},", f"{kospi[1]} 마감"
        swing = Swing(close_level=kospi[1], close_change=f"{kospi[2]}{kospi[3]}%")
    elif kospi:
        headline, accent, swing = f"코스피 {kospi[1]}", "마감", None
    else:
        headline, accent, swing = first(summary[0], 40) if summary else "오늘의 증시 마감", "", None
    lead = clip(" ".join(first(s, 160) for s in summary[:2]), 300) if summary else opening
    overview = source["overview"] or sections.get("시장 전체 흐름", [])
    flow = clip(" ".join(overview), 500) if overview else "오늘 시장의 주요 숫자는 아래와 같습니다."
    sectors = clip(" ".join(source["internals"] or sections.get("업종·수급에서 볼 점", [])), 400)
    issues = [IssueBlock(headline=clip(issue["headline"], 60), body=first(issue["fact"], 600),
                         check=first(issue.get("next_check") or "다음 발표와 시장 반응을 확인합니다.", 160))
              for issue in source["issues"][:3]]
    market = Market(heading="주요 숫자와 시장 흐름", swing=swing, flow=flow, sectors=sectors,
                    numbers=[clip(t, 160) for t in board[:8]])
    calendar = [CalendarRow(when=day_label(row["when"], source["weekdays"]), what=clip(row["title"], 80))
                for row in source["calendar"][:7]]
    return NewsletterDraft(headline=headline, accent=accent, preview=opening, lead=lead, market=market, issues=issues,
                           calendar=calendar)
