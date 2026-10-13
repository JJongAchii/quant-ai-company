"""Conservative exchange-rule checks for dated market claims.

NYMEX Light Sweet Crude Oil futures rule 200102.F ends trading before the
25th of the month preceding delivery. The exact last day depends on the
exchange holiday calendar, so this check only rejects dates on or after the
25th, when expiry is certain. Rule: https://www.cmegroup.com/rulebook/NYMEX/2/200.pdf
"""

import re
from datetime import date

_DELIVERY_MONTH = re.compile(r"(?:(?P<year>20\d{2})년\s*)?(?P<month>1[0-2]|0?[1-9])월\s*(?:인도분|물)")
_PRODUCT = re.compile(r"WTI|서부\s*텍사스산|브렌트|Brent", re.I)


def expired_wti_contract(text: str, observed_on: date) -> bool:
    """Detect a certainly expired Korean-labelled NYMEX WTI delivery month.

    An absent year refers to the current year, except January delivery named
    in December. We deliberately do not calculate the exact expiry date.
    """
    for match in _DELIVERY_MONTH.finditer(text):
        # The first named product after a delivery label owns that label.
        following = _PRODUCT.search(text[match.end():match.end()+45])
        preceding = re.search(r"(?:WTI|서부\s*텍사스산)(?:원유)?(?:의)?\s*$",
                              text[max(0, match.start()-20):match.start()], re.I)
        if not preceding and (not following or following.group().lower() not in
                              {"wti", "서부텍사스산", "서부 텍사스산"}):
            continue
        month = int(match["month"])
        year = int(match["year"]) if match["year"] else observed_on.year + (observed_on.month == 12 and month == 1)
        previous_month = month-1 or 12
        previous_year = year - (month == 1)
        if observed_on >= date(previous_year, previous_month, 25):
            return True
    return False
