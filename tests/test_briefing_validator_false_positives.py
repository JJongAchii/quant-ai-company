"""Validator false positives reproduced in the 2026-11-17 editorial comparison, with their negative controls.

Source: docs/project/evidence/editorial-model-eval-20261117 (validator-diagnostics.json, editorial-assessment.json)
in the company-editorial-model-eval worktree. Each fix accepts the supplied meaning and still rejects unsupported text.
"""

from datetime import date
from decimal import Decimal

import pytest

from quant_company.briefing.inventory import normalized
from quant_company.briefing.market_rules import expired_wti_contract
from quant_company.briefing.numeric import prose_numbers_supported, reported_change_supported


@pytest.mark.parametrize("draft,quotes,expected", [
    ("3분의 2가 부채로 조달됐다", ["two-thirds of it funded by debt"], True),
    ("3분의 2가 부채로 조달됐다", ["2/3 of it funded by debt"], True),
    ("시장의 3분의 1", ["a third of the market"], True),
    ("3분의 2가 부채로 조달됐다", ["two-fifths of it funded by debt"], False),
    ("3분의 2가 부채로 조달됐다", ["half of it funded by debt"], False),
])
def test_fraction_without_finite_decimal_needs_the_same_stated_fraction(draft, quotes, expected):
    assert prose_numbers_supported(draft, quotes) is expected


@pytest.mark.parametrize("draft,quotes,expected", [
    ("순매도 1·2위였다", ["순매도 1,2위를 기록했다"], True),
    ("순매도 1,2위였다", ["순매도 1·2위를 기록했다"], True),
    ("삼성전자와 SK하이닉스가 순매도 1·2위였다", ["외국인 순매도 1,2위는 삼성전자와 SK하이닉스"], True),
    ("순매도 1·2위였다", ["순매도 1위는 삼성전자"], False),
    ("순매도 12위였다", ["순매도 1,2위를 기록했다"], False),
    ("2위였다", ["2위안 하락"], False),
])
def test_korean_rank_lists_compare_as_ranks_not_as_a_thousands_number(draft, quotes, expected):
    assert prose_numbers_supported(draft, quotes) is expected


def test_slipped_is_a_decline_but_not_a_rise():
    quotes = ["SK하이닉스는 전장보다 7.89% 미끄러진 112만원으로 장을 마쳤다."]
    assert prose_numbers_supported("SK하이닉스는 7.89% 하락했다.", quotes)
    assert reported_change_supported(Decimal("-7.89"), "%", quotes)
    assert not reported_change_supported(Decimal("7.89"), "%", quotes)


def test_magnitude_noun_shared_by_a_korean_and_list():
    quotes = ["나스닥과 S&P500은 각각 0.91%와 3.43%의 낙폭을 기록했다."]
    assert reported_change_supported(Decimal("-0.91"), "%", quotes)
    assert reported_change_supported(Decimal("-3.43"), "%", quotes)
    assert not reported_change_supported(Decimal("0.91"), "%", quotes)
    assert not reported_change_supported(Decimal("-0.91"), "%", ["각각 0.91%와 3.43% 상승했다."])
    assert reported_change_supported(Decimal("-0.91"), "%", ["A는 0.91%와 B는 3.43% 내렸다."])


@pytest.mark.parametrize("text,observed,expired", [
    # The product named right before a delivery label owns it.
    ("브렌트유 1월물은 92.44달러, WTI 12월물은 85.72달러였다.", date(2026, 11, 16), False),
    ("브렌트유 12월물", date(2026, 11, 26), False),
    ("1월물 브렌트유와 12월물 WTI", date(2026, 11, 26), True),
    ("서부텍사스산원유(WTI) 12월물", date(2026, 11, 26), True),
    # A month-only label names the nearest delivery: January named in November is next year's, still trading.
    ("WTI 1월물 85달러", date(2026, 11, 16), False),
    ("WTI 3월물", date(2026, 11, 16), False),
    ("WTI 12월물", date(2026, 11, 16), False),
    ("WTI 12월물", date(2026, 11, 25), True),
    ("WTI 11월물", date(2026, 11, 16), True),
    ("WTI 1월물", date(2026, 12, 26), True),
    ("WTI 2월물", date(2026, 12, 26), False),
    ("2026년 1월물 WTI", date(2026, 11, 16), True),
])
def test_wti_expiry_owner_and_month_only_year(text, observed, expired):
    assert expired_wti_contract(text, observed) is expired


def test_qualifier_survives_a_bracketed_alias():
    assert normalized("WSJ는") in normalized("월스트리트저널(WSJ)는 소식통을 인용해")
    assert normalized("잠정치") not in normalized("확정치(최종)")


@pytest.mark.parametrize("draft,quotes,expected", [
    # Found in the 2026-11-17 limited trial (docs/project/evidence/claude-editorial-20261117/trial.json).
    ("부동산 침체가 5년째 이어졌다", ["household wealth eroding for a fifth year"], True),
    ("비트코인은 주간 기준 6주 연속 하락했다", ["is coming off its sixth weekly decline in a row"], True),
    ("3거래일 연속 상승했다", ["the 3rd straight session of gains"], True),
    ("부동산 침체가 5년째 이어졌다", ["household wealth eroding for a fourth year"], False),
    ("6주 연속 하락했다", ["its sixth monthly decline"], False),
])
def test_korean_running_count_matches_the_same_english_ordinal_period(draft, quotes, expected):
    assert prose_numbers_supported(draft, quotes) is expected
