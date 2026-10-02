"""Exact written-unit/date normalization; no estimation or market-price inference."""

import re
from datetime import date
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
    # Expand the shared denominator only for an explicitly labelled point/percent
    # range: "2 or 3 tenths of a percentage point" has two exact endpoints.
    text = re.sub(r"\b(\d+)\s+or\s+(\d+)\s+("+"|".join(values)+r")"
                  r"(?P<unit>\s+(?:of\s+(?:a|one)\s+)?(?:percentage[-\s]+)?(?:points?|percent)\b)",
                  lambda m: f"{m[1]} {m[3]}{m['unit']} or {m[2]} {m[3]}{m['unit']}",
                  text, flags=re.I)
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


def decimal_points(text):
    return re.sub(r"(?<![A-Za-z0-9])(\d+)-point-(\d+)\b", r"\1.\2", text, flags=re.I)


def months(text):
    result = {Decimal(m[1]) for m in re.finditer(r"(?<![\d.])(0?[1-9]|1[0-2])월", text)}
    for match in re.finditer(r"(?<!\d)((?:19|20)\d{2})[./-](\d{1,2})[./-](\d{1,2})(?!\d)", text):
        try:
            dated = date(*(int(part) for part in match.groups()))
        except ValueError:
            continue
        result.add(Decimal(dated.month))
    for month, name in enumerate(MONTHS, 1):
        names = name if name == "May" else "(?:"+name+"|"+name[:3]+r"\.?"+(r"|Sept\.?" if month == 9 else "")+")"
        dated = r"\b(?:\d{1,2}(?:st|nd|rd|th)?\s+"+names+"|"+names+r"\s+(?:\d{4}|\d{1,2}(?:st|nd|rd|th)?))\b"
        release_month = (r"\b"+names+r"['’]s\s+(?:(?:CPI|PPI|PCE|JOLTS|jobs|payrolls?|inflation)\s+)?"
                         r"(?:report|data|figures|reading|release)\b")
        labelled_release = (r"\b"+names+r"\s+(?:(?:U\.S\.|US|U\.K\.|UK)\s+)?"
                            r"(?:CPI|PPI|PCE|JOLTS|jobs|payrolls?|inflation|employment)\s+"
                            r"(?:report|data|figures|reading|release)\b")
        if (re.search(dated, text, re.I) or re.search(release_month, text, re.I)
                or re.search(labelled_release, text, re.I)
                or re.search(r"\b(?:in|of|from|during|as of|by|for|last|this|next|since|until|on)\s+"+names+r"\b", text, re.I)):
            result.add(Decimal(month))
    return result


def numbers(text):
    def dotted_date(match):
        try:
            year, month, day = (int(part) for part in match.groups())
            date(year, month, day)
        except ValueError:
            return match[0]
        return f"{year}년 {month}월 {day}일"

    text = re.sub(r"(?<![\d.])((?:19|20)\d{2})\.(\d{1,2})\.(\d{1,2})(?!\d|\.\d)",
                  dotted_date, text)
    # Canonicalize entire quantities so 2.5 million == 250만, not the unrelated bare digits 2.5/250.
    digit_words = "|".join(CARDINALS[:10])
    thousands = r"\b("+"|".join(CARDINALS[1:10])+r")-thousand-(\d{1,3})-point-("+digit_words+r"|\d+)\b"
    text = re.sub(thousands,
        lambda m: str(CARDINALS.index(m[1].lower())*1000+int(m[2]))+"."
                  +(str(CARDINALS.index(m[3].lower())) if m[3].lower() in CARDINALS else m[3]),
        text, flags=re.I)
    quarters = {"first": "1", "second": "2", "third": "3", "fourth": "4"}
    text = re.sub(r"\b(first|second|third|fourth)[-\s]+quarter\b",
                  lambda m: quarters[m[1].lower()]+" quarter", text, flags=re.I)
    text = written_fractions(written_counts(text))
    # A same-sentence pre-war comparison inherits this explicit flow unit.
    # Do not propagate it to another sentence, a percent or a labelled unit.
    text = re.sub(
        "("+NUMBER+r")\s+kilobarrels(?P<context>\s+(?:a|per)\s+day[^.!?\n]{0,80}?,"
        r"\s*against\s+a\s+pre-war\s+baseline\s+of\s+)("+NUMBER+r")(?=\.(?!\d)|[,;]|$)",
        lambda m: m[1]+" kilobarrels"+m["context"]+m[3]+" kilobarrels", text, flags=re.I,
    )
    # Some broadcasters spell decimal points as hyphenated words/numbers.
    text = decimal_points(text)
    # Basis points and percentage points express the same exact rate distance.
    # Canonicalize both source/prose bp values without treating 4bp as 4%.
    text = re.sub(r"(?<![A-Za-z0-9])("+NUMBER+r")\s*(?:bps?(?![A-Za-z])|basis points?\b)",
                  lambda m: str(Decimal(m[1].replace(",", "").replace("−", "-"))/100), text, flags=re.I)
    # In "301조 관세", 조 names a legal section, not a trillion-unit amount.
    # Keep the section number checked; "301조원" stays a monetary quantity.
    text = re.sub(r"(?<![\d.])(\d+)\s*조(?=\s*관세)", r"\1 article", text)
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
    scales = {"million": 10**6, "billion": 10**9, "trillion": 10**12, "bn": 10**9, "tn": 10**12,
              "kilobarrels": 1000}
    normalized = re.sub("("+NUMBER+r")\s*(million|billion|trillion|bn|tn|kilobarrels)\b",
        lambda m: str(Decimal(m[1].replace(",", "").replace("−", "-"))*scales[m[2].lower()]),
        normalized, flags=re.I)
    # G7, H200 and similar names are semantic entities, not quantities. The
    # independent review still checks entity identity against the originals.
    result = {Decimal(n.replace(",", "").replace("−", "-"))
              for n in re.findall(r"(?<![A-Za-z0-9.,])"+NUMBER, normalized)}
    return result | months(text)


def reported_change_supported(value, unit, quotes):
    units = {"%": r"%(?!p|포인트)|percent(?!age|\s+points?)", "bp": r"bps?(?![A-Za-z])|basis points?", "pt": r"pt\b|points?|포인트"}
    down = (r"하락|급락|내린|내렸|떨어|낮아|줄었|밀린|밀렸|빠진|빠졌|"
            r"fell|fall|down|lower\b|declin\w*|lost|slipped|shed|slump\w*|(?:was|were|is|are)\s+off")
    up = r"상승|오른|올랐|높아|늘었|뛴|뛰었|뛰며|rose|ris\w*|up|higher\b|gain\w*|advanced|jumped|surged"
    for quote in quotes:
        quote = decimal_points(written_counts(quote))
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
            # Broadcasters give one move in points and then its percentage:
            # "gained six-point-11 points, or zero-point-72 percent".
            # Require the same adjacent predicate and an explicit first unit.
            equivalent = ("(?:"+direction+r")(?:\s+(?:by|about|roughly|nearly))?\s+"
                          +NUMBER+r"\s*(?:points?|percent|bp|basis points?)\s*,?\s+or\s*$")
            if re.search(equivalent, before, re.I):
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
    # A ranking is not a price or quantity: English "second-largest" supports
    # Korean "2위", but never a bare 2, a 2% change or a second-tier label.
    ranks = {1: r"(?<![\w-])(?:the\s+)?(?:largest|biggest)\b",
             2: r"\bsecond[-\s]+(?:largest|biggest)\b",
             3: r"\bthird[-\s]+(?:largest|biggest)\b"}

    def english_rank_supported(value, quote):
        if value not in ranks:
            return False
        if value == 1:
            # "largest" inside a higher ordinal, even with spaces rather than
            # a hyphen, cannot establish the leading rank.
            quote = re.sub(r"\b(?:second|third|[a-z]+th|(?!(?:1st)\b)\d+(?:st|nd|rd|th))"
                           r"[-\s]+(?:largest|biggest)\b", " ", quote, flags=re.I)
        return bool(re.search(ranks[value], quote, re.I))

    def check_rank(match):
        nonlocal valid
        value = int(match[1])
        korean = re.escape(match[1])+r"\s*위(?=$|[\s,.]|[의인로를은가였다])"
        if any(re.search(korean, quote) or english_rank_supported(value, quote)
               for quote in quotes):
            return "위"
        valid = False
        return match[0]

    remaining = re.sub(r"(?<![\d.,])([1-9]\d*)\s*위(?=$|[\s,.]|[의인로를은가였다])", check_rank, remaining)
    if not valid:
        return False
    # A time such as 8:30 cannot support an August release label merely because
    # the same bare digit appears; require an actual month in the own quotes.
    if not months(remaining) <= months(" ".join(quotes)):
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
