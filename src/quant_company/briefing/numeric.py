"""Exact written-unit/date normalization; no estimation or market-price inference."""

import re
from decimal import Decimal

NUMBER = r"[+\-−]?\d[\d,]*(?:\.\d+)?"
MONTHS = "January February March April May June July August September October November December".split()
SMALL = {"십": 10, "백": 100, "천": 1000}
LARGE = {"만": 10000, "억": 100000000, "조": 1000000000000}
CARDINALS = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()


def written_fractions(text):
    values = {"half": Decimal("0.5"), "halves": Decimal("0.5"),
              "quarter": Decimal("0.25"), "quarters": Decimal("0.25"),
              "fifth": Decimal("0.2"), "fifths": Decimal("0.2"),
              "tenth": Decimal("0.1"), "tenths": Decimal("0.1")}
    pattern = (r"\b(?:(a|an|\d+)\s+)?("+"|".join(values)+r")\b"
               r"(?=[-\s]+(of\b|(?:percentage[-\s]+)?points?\b|percent\b))")

    def fraction(match):
        # A fiscal quarter or ordinal fifth is not a quantity. Bare fractions
        # are accepted only with an explicit percent/point unit.
        if match[1] is None and match[3].lower() == "of":
            return match[0]
        numerator = Decimal(match[1]) if match[1] and match[1].isdigit() else Decimal(1)
        return str(numerator*values[match[2].lower()])

    text = re.sub(pattern, fraction, text, flags=re.I)
    # These denominators have exact finite decimal equivalents; do not round
    # arbitrary fractions or infer a percentage from an unlabelled proportion.
    return re.sub(r"(?<![\d.])(2|4|5|10)\s*분의\s*(\d+)(?![\d.])",
                  lambda m: str(Decimal(m[2])/Decimal(m[1])), text)


def written_counts(text):
    units = "|".join(CARDINALS[1:10])
    pattern = r"\b(?:(?:"+"|".join(TENS)+r")(?:(?:-|\s)(?:"+units+r"))?|"+"|".join(CARDINALS)+r")\b"

    def convert(match):
        # Larger spelled-out compounds are unsupported, not partial numbers.
        if (re.match(r"[-\s]+(?:hundred|thousand)\b", text[match.end():], re.I)
                or re.search(r"\b(?:hundred|thousand)(?:\s+and)?[-\s]+$", text[:match.start()], re.I)):
            return match[0]
        words = re.split(r"[-\s]", match[0].lower())
        if words[0] in TENS:
            return str((TENS.index(words[0])+2)*10 + (CARDINALS.index(words[1]) if len(words) == 2 else 0))
        return str(CARDINALS.index(words[0]))

    return re.sub(pattern, convert, text, flags=re.I)


def quantity(text):
    sign = -1 if text.startswith(("-", "−")) else 1
    total, section, current = Decimal(0), Decimal(0), None
    for token in re.findall(r"\d[\d,]*(?:\.\d+)?|[십백천만억조]", text):
        if token in SMALL:
            section += (current if current is not None else 1)*SMALL[token]
            current = None
        elif token in LARGE:
            total += (section+(current if current is not None else 0))*LARGE[token]
            section, current = Decimal(0), None
        else:
            current = Decimal(token.replace(",", ""))
    return sign*(total+section+(current if current is not None else 0))


def numbers(text):
    # Canonicalize entire quantities so 2.5 million == 250만, not the unrelated bare digits 2.5/250.
    digit_words = "|".join(CARDINALS[:10])
    thousands = r"\b("+"|".join(CARDINALS[1:10])+r")-thousand-(\d{1,3})-point-("+digit_words+r"|\d+)\b"
    text = re.sub(thousands,
        lambda m: str(CARDINALS.index(m[1].lower())*1000+int(m[2]))+"."
                  +(str(CARDINALS.index(m[3].lower())) if m[3].lower() in CARDINALS else m[3]),
        text, flags=re.I)
    quarters = {"first": "1", "second": "2", "third": "3", "fourth": "4"}
    text = re.sub(r"\b(first|second|third|fourth)\s+quarter\b",
                  lambda m: quarters[m[1].lower()]+" quarter", text, flags=re.I)
    text = written_fractions(written_counts(text))
    # Some broadcasters spell decimal points as hyphenated words/numbers.
    text = re.sub(r"(?<![A-Za-z0-9])(\d+)-point-(\d+)\b", r"\1.\2", text, flags=re.I)
    # A compact percent range uses a hyphen as a separator, not the sign of
    # its upper bound. Keep a spaced "5% -3%" as a genuinely negative value.
    text = re.sub(r"(?<=%)-(?=\d[\d,]*(?:\.\d+)?%)", " to ", text)
    text = re.sub(r"\bS&P\)?\s*500(?!\d)", "S_AND_P_INDEX", text, flags=re.I)
    normalized = re.sub(NUMBER+r"(?:\s*[십백천만억조](?:\s*\d[\d,]*(?:\.\d+)?)?)+",
                        lambda m: " "+str(quantity(m[0]))+" ", text)
    # Finance articles use an attached m for millions of dollars. Require the
    # currency mark so unrelated m-units are not silently converted.
    normalized = re.sub(r"(?<![A-Za-z0-9])(?:US)?\$\s*("+NUMBER+r")m\b",
        lambda m: str(Decimal(m[1].replace(",", "").replace("−", "-"))*1000000),
        normalized, flags=re.I)
    scales = {"million": 10**6, "billion": 10**9, "trillion": 10**12, "bn": 10**9, "tn": 10**12}
    normalized = re.sub("("+NUMBER+r")\s*(million|billion|trillion|bn|tn)\b",
        lambda m: str(Decimal(m[1].replace(",", "").replace("−", "-"))*scales[m[2].lower()]),
        normalized, flags=re.I)
    # G7, H200 and similar names are semantic entities, not quantities. The
    # independent review still checks entity identity against the originals.
    result = {Decimal(n.replace(",", "").replace("−", "-"))
              for n in re.findall(r"(?<![A-Za-z0-9.,])"+NUMBER, normalized)}
    for month, name in enumerate(MONTHS, 1):
        names = name if name == "May" else "(?:"+name+"|"+name[:3]+r"\.?"+(r"|Sept\.?" if month == 9 else "")+")"
        dated = r"\b(?:\d{1,2}(?:st|nd|rd|th)?\s+"+names+"|"+names+r"\s+(?:\d{4}|\d{1,2}(?:st|nd|rd|th)?))\b"
        # A possessive month labels an economic release, without inventing its
        # day or time. Require release context, not just a possessive name.
        release_month = (r"\b"+names+r"['’]s\s+(?:(?:CPI|PPI|PCE|JOLTS|jobs|payrolls?|inflation)\s+)?"
                         r"(?:report|data|figures|reading|release)\b")
        if (re.search(dated, text, re.I)
                or re.search(release_month, text, re.I)
                or re.search(r"\b(?:in|of|during|as of|by|for|last|this|next|since|until|on)\s+"+names+r"\b", text, re.I)):
            result.add(Decimal(month))
    return result


def reported_change_supported(value, unit, quotes):
    units = {"%": r"%(?!p|포인트)|percent(?!age|\s+points?)", "bp": r"bp\b|basis points?", "pt": r"pt\b|points?|포인트"}
    down = (r"하락|급락|내린|내렸|떨어|낮아|줄었|밀린|밀렸|빠진|빠졌|"
            r"fell|fall|down|declin\w*|lost|slipped|shed|slump\w*|(?:was|were|is|are)\s+off")
    up = r"상승|오른|올랐|높아|늘었|뛴|뛰었|뛰며|rose|ris\w*|up|gain\w*|advanced|jumped|surged"
    for quote in quotes:
        for match in re.finditer("("+NUMBER+r")\s*(?:"+units[unit]+")", quote, re.I):
            raw = match[1]
            amount = Decimal(raw.replace(",", "").replace("−", "-"))
            if abs(amount) != abs(value):
                continue
            if raw.startswith(("+", "-", "−")):
                if amount == value:
                    return True
                continue
            if value == 0:
                return True
            direction = down if value < 0 else up
            before, after = quote[:match.start()], quote[match.end():]
            after = re.sub(r"^\s*\([^()]{0,40}\)", "", after)
            if (re.search("(?:"+direction+r")(?:\s+(?:by|about|roughly|nearly))?\s*$", before, re.I)
                    or re.match(r"^[\s)\]]*(?:(?:가|나|만큼)\s*)?(?:"+direction+")", after, re.I)):
                return True
            # A coordinated list can share its final direction: "A 1.3%, B 2.4% 내렸다".
            shared = (r"(?:\s*,\s*[가-힣A-Za-z·\s]{0,60}"+NUMBER+r"\s*(?:"+units[unit]+r"))+"
                      r"\s*(?:"+direction+r")")
            if re.match(shared, after, re.I):
                return True
    return False


def prose_numbers_supported(text, quotes):
    """Match exact numbers, including an explicit Korean percentage decline.

    A shared predicate can cover a coordinated list (A는 5%, B는 3% 하락했다).
    Only those occurrences get signed-change checking; other amounts keep their
    original sign. Instrument attribution and claim meaning still require review.
    """
    rate = r"(?<![\d.,+\-−])\d[\d,]*(?:\.\d+)?\s*%(?!\s*(?:[pP]\b|포인트))"
    label = r"[가-힣A-Za-z·\s]{0,60}"
    decline = r"\s*(?:하락(?:했습니다|했다|했고|한)|내렸(?:습니다|다)|내린)"
    series = rate+r"(?:\s*,\s*"+label+rate+r")*"+decline
    valid = True

    def check(match):
        nonlocal valid
        for value in re.finditer(rate, match[0]):
            amount = Decimal(value[0].rstrip("% ").replace(",", ""))
            if not reported_change_supported(-amount, "%", quotes):
                valid = False
        return re.sub(rate, " ", match[0])

    remaining = re.sub(series, check, text)
    if not valid:
        return False
    supported = numbers(" ".join(quotes))
    # A source may write "2.70% 내린" while the brief writes "-2.70%".
    # Require the same magnitude and an explicit downward predicate in a quote.
    negative_percent = r"(?<![\d.,])[-−]\s*(\d[\d,]*(?:\.\d+)?)\s*%(?!\s*(?:[pP]\b|포인트))"
    for match in re.finditer(negative_percent, remaining):
        amount = -Decimal(match[1].replace(",", ""))
        if reported_change_supported(amount, "%", quotes):
            supported.add(amount)
    # A quoted negative fund flow is naturally reported as a positive magnitude
    # followed by an explicit Korean outflow word. Never extend this to inflows.
    korean_amount = NUMBER+r"(?:\s*[십백천만억조](?:\s*\d[\d,]*(?:\.\d+)?)?)+"
    outflow = (r"(?<![A-Za-z0-9])("+korean_amount+r")(?:원|달러)?(?:[이가은는])?\s*"
               r"(?:순유출|유출|빠졌|빠진|감소|하락)")
    for match in re.finditer(outflow, remaining):
        magnitude = quantity(match[1])
        if magnitude > 0 and -magnitude in supported:
            supported.add(magnitude)
    return numbers(remaining) <= supported
