"""Exact equivalent expressions observed in real Analyst output; no relaxed numeric gates."""

from decimal import Decimal

import pytest

from quant_company.briefing.numeric import prose_numbers_supported, reported_change_supported


def test_rank_followed_by_parenthesized_amount_is_not_a_different_rank():
    quote = '가상기업은 외국인 순매도 1위(500억원)를 기록했다.'
    assert prose_numbers_supported('가상기업은 순매도 1위로 규모는 500억원이었다.', [quote])
    assert not prose_numbers_supported('가상기업은 순매도 1위였다.', [quote.replace('1위', '11위')])
    assert not prose_numbers_supported('가상기업은 순매도 2위였다.', [quote])


def test_named_month_quarter_supports_the_same_month_not_another_period():
    quote = 'During the June quarter, the synthetic economy expanded 3.8%.'
    assert prose_numbers_supported('6월 분기 성장률은 3.8%였다.', [quote])
    assert not prose_numbers_supported('7월 분기 성장률은 3.8%였다.', [quote])
    assert not prose_numbers_supported('성장률은 6%였다.', [quote])
    assert not prose_numbers_supported('6월 분기 성장률은 3.8%였다.', ['Quarterly growth was 3.8%; the index rose 6 points.'])


@pytest.mark.parametrize('count', [2, 3, 4])
def test_first_n_quarters_supports_only_the_explicit_cumulative_period(count):
    word = {2: 'two', 3: 'three', 4: 'four'}[count]
    quotes = [f'Synthetic revenue for the first {word} quarters was 70 trillion won.']
    assert prose_numbers_supported(f'1~{count}분기 누적 매출은 70조원이었다.', quotes)
    assert not prose_numbers_supported(f'1~{count}분기 누적 매출은 70조원이며 이익은 1조원이었다.', quotes)
    assert not prose_numbers_supported('1% 성장했다.', quotes)
    assert not prose_numbers_supported('1~3분기 매출은 70조원이었다.', ['Revenue in the third quarter was 70 trillion won.'])


@pytest.mark.parametrize('word', ['내림폭', '하락폭', '하향폭', '낙폭'])
def test_decline_label_establishes_negative_direction_without_permitting_positive_move(word):
    quote = f'가상 지수의 {word}은 0.82%였다.'
    assert prose_numbers_supported('가상 지수는 0.82% 하락 마감했다.', [quote])
    assert reported_change_supported(Decimal('-0.82'), '%', [quote])
    assert not reported_change_supported(Decimal('0.82'), '%', [quote])
    assert not prose_numbers_supported('가상 지수는 0.83% 하락했다.', [quote])
    assert not prose_numbers_supported('가상 지수는 0.82% 하락했다.', ['가상 지수 상승폭은 0.82%였다.'])


def test_negative_estimate_revision_supports_only_an_explicit_downward_magnitude():
    quote = 'Synthetic materials earnings estimates saw negative revisions (-7.2%).'
    assert prose_numbers_supported('소재의 하향폭이 7.2%로 가장 컸다.', [quote])
    assert not prose_numbers_supported('소재의 상향폭이 7.2%로 가장 컸다.', [quote])
    assert not prose_numbers_supported('소재의 하향폭이 7.3%였다.', [quote])
    assert not prose_numbers_supported('소재의 하향폭이 7.2%였다.', [quote.replace('-7.2', '+7.2')])


@pytest.mark.parametrize('name,month', [('March', 3), ('May', 5)])
def test_named_action_plan_month_is_not_a_percentage_or_modal_verb(name, month):
    quote = f'Members deployed 200 million barrels under the {name} emergency action plan.'
    assert prose_numbers_supported(f'{month}월 비상계획으로 2억 배럴을 공급했다.', [quote])
    assert not prose_numbers_supported(f'{month+1}월 계획으로 2억 배럴을 공급했다.', [quote])
    assert not prose_numbers_supported(f'공급이 {month}% 늘었다.', [quote])
    assert not prose_numbers_supported('5월 계획을 결정했다.', ['Members may plan another release.'])
