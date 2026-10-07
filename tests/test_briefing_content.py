import json
import sys
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal

import pytest

from quant_company.briefing.contracts import (
    BriefProposal,
    BriefReview,
    CalendarEvent,
    ConditionPatch,
    EditorialPatch,
    MarketObservation,
    MaterialFactPatch,
    SourceDocument,
)
from quant_company.briefing.coverage import inventory, select_documents, topics
from quant_company.briefing.editor import (
    apply_condition_patch,
    apply_editorial_patch,
    apply_material_fact_patch,
    artifact,
    calendar_equivalent_numbers,
    canonical_source_quote,
    claims_wti_price,
    main_post_item_ids,
    non_session_korean_listed_price,
    prune,
    render,
    revision_bundle,
    validate,
    validate_review,
)
from quant_company.briefing.inputs import document, market_report
from quant_company.briefing.market_rules import expired_wti_contract
from quant_company.briefing.numeric import numbers, prose_numbers_supported, reported_change_supported
from quant_company.briefing.qualification import qualify
from quant_company.config import Settings
from quant_company.contracts import ProviderFault, ProviderRequest
from scripts import evaluate_briefing

from .test_briefing import brief, bundle, definition, proposal, response, review, seed  # noqa: F401
from .test_briefing_analyst import analytical_proposal


def test_explicit_shared_scale_range_keeps_both_endpoints():
    quote = "Oil flows are 9-10 million bpd, versus 14.5 million bpd before the war."
    assert prose_numbers_supported("하루 900만~1,000만 배럴", [quote])
    assert not prose_numbers_supported("하루 800만~1,000만 배럴", [quote])
    assert Decimal(9000000) not in numbers("9 barrels. 10 million bpd.")
    assert Decimal(9000000) not in numbers("9 -10 million bpd")
    assert Decimal(-10000000) in numbers("9 -10 million bpd")


def test_compound_approximate_korean_quantity_preserves_magnitude():
    assert prose_numbers_supported("일평균 약 1조6,000억원", ["일평균 1조6천여억원씩 순매수"])
    assert prose_numbers_supported("일평균 약 1조6,000억원", ["일평균 1조 6천여 억 원씩 순매수"])
    assert not prose_numbers_supported("일평균 약 1조7,000억원", ["일평균 1조6천여억원씩 순매수"])
    assert Decimal(1600000000000) not in numbers("1조원과 6천여개")


def test_explicit_decline_magnitude_supports_negative_change_only():
    quote = "운송지수는 0.45%의 낙폭을 기록했다."
    assert prose_numbers_supported("운송지수는 0.45% 하락했다.", [quote])
    assert reported_change_supported(Decimal("-.45"), "%", [quote])
    assert not reported_change_supported(Decimal(".45"), "%", [quote])
    assert not reported_change_supported(Decimal("-.45"), "%", ["운송지수는 0.45%의 상승폭을 기록했다."])
    assert reported_change_supported(Decimal("-1.07"), "%", ["선물도 1.07% 내려 마감했다."])


def doc(identity, title, **updates):
    value = deepcopy(bundle()["documents"][0])
    value.update(id=identity, title=title, sha256=identity, publisher=identity, origin_group=identity,
                 url="https://example.org/"+identity, **updates)
    return SourceDocument.model_validate(value)


def test_exact_dotted_date_supports_korean_month_and_day_without_inference():
    assert prose_numbers_supported("9월 29일 전일장", ["기준일 2026.9.29."])
    assert not prose_numbers_supported("9월 30일 전일장", ["기준일 2026.9.29."])
    assert not prose_numbers_supported("9월 29일", ["지수는 2026.9에 마감했다."])
    assert not prose_numbers_supported("2월 29일", ["2026.2.29"])


def test_cached_broad_etoday_page_cannot_enter_briefing_source_budget():
    receipt = {"ok": True, "publisher_host": "www.etoday.co.kr", "content_type": "text/html",
               "article_extraction": "article", "content": "Unrelated market widgets "*100,
               "original_sha256": "a"*64, "url": "https://www.etoday.co.kr/news/view/123",
               "retrieved_at": "2026-09-21T21:00:00Z", "published_at": "2026-09-21T20:00:00Z"}
    assert document(receipt, "etoday-global", "Etoday", "media") is None
    receipt.update(article_extraction="articleBody", content="Verified article body "*20)
    assert document(receipt, "etoday-global", "Etoday", "media").content == receipt["content"]


def test_analyst_rejects_known_or_unmarked_partial_originals():
    receipt = {"ok": True, "content": "Complete bounded original "*260,
               "original_sha256": "a"*64, "url": "https://example.org/article",
               "retrieved_at": "2026-09-30T21:00:00Z", "published_at": "2026-09-30T20:00:00Z"}
    assert len(receipt["content"]) > 6000
    assert document(receipt, "fixture", "Fixture", "media").content == receipt["content"]
    receipt["excerpt_truncated"] = True
    assert document(receipt, "fixture", "Fixture", "media") is None
    receipt.update(excerpt_truncated=False, article_chars=len(receipt["content"])+1)
    assert document(receipt, "fixture", "Fixture", "media") is None
    receipt.update(excerpt_truncated=False, content="x"*12001)
    assert document(receipt, "fixture", "Fixture", "media") is None


def test_coordinated_fraction_range_requires_explicit_unit_and_exact_endpoints():
    quote = "Revised lower by two or three tenths of a percentage point."
    assert prose_numbers_supported("0.2~0.3%포인트 하향 예상", [quote])
    assert not prose_numbers_supported("0.1~0.3%포인트 하향 예상", [quote])
    assert not prose_numbers_supported("0.2~0.4%포인트 하향 예상", [quote])
    assert not prose_numbers_supported("0.2%포인트", ["two or three tenths of employees"])
    assert numbers("second quarter revenue") == {Decimal(2)}


def test_am_previous_korean_close_requires_frozen_time_and_visible_date():
    b, p = bundle(), proposal()
    day = date.fromisoformat(b["edition"]["previous_kr_session"])
    from quant_company.briefing.schedule import close

    at = close("KR", day)
    quote = "코스피는 전장보다 0.27% 내린 6870.81에 마감했다."
    b["documents"][0]["content"] += "\n"+quote
    obs = MarketObservation.model_validate({
        "id": "prior_kospi", "instrument": "kospi", "value": "6870.81", "unit": "pt",
        "session_date": day, "as_of": at, "basis": "close", "venue": "KRX",
        "reported_change": "-0.27", "change_unit": "%",
        "evidence": [{"source_id": b["documents"][0]["id"], "quote": quote}],
    })
    p.observations.append(obs)
    assert validate(p, b)[obs.id] == "previous_close_time_unavailable"
    b["exchange_closes"] = {"KR_previous": at.isoformat()}
    assert obs.id not in validate(p, b)
    assert f"한국 전일장 {day:%m/%d} · 코스피" in render(p, b)[0][0]
    obs.as_of += timedelta(minutes=1)
    assert validate(p, b)[obs.id] == "wrong_equity_close_time"
    obs.as_of = at
    obs.previous_value = obs.value
    obs.previous_session_date = day
    assert validate(p, b)[obs.id] == "incompatible_comparison"
    obs.previous_value = None
    obs.previous_session_date = None
    obs.session_date -= timedelta(days=1)
    assert validate(p, b)[obs.id] == "wrong_close_session"


def test_am_freezes_previous_korean_close_in_producer_bundle(brief):  # noqa: F811
    from quant_company.briefing.schedule import close

    store, _ = brief
    edition = seed(brief)
    store.prepare()
    with store.db.transaction() as conn:
        frozen = conn.execute("SELECT bundle FROM brief_editions WHERE id=%s", (edition.id,)).fetchone()["bundle"]
    assert frozen["exchange_closes"]["KR_previous"] == close("KR", edition.previous_kr_session).isoformat()


def test_am_previous_fx_reference_requires_exact_frozen_time_and_visible_timestamp():
    from quant_company.briefing.schedule import close

    b, p = bundle(), proposal()
    day = date.fromisoformat(b["edition"]["previous_kr_session"])
    at = close("KR", day)
    quote = "서울 외환시장의 오후 3시 30분 기준가는 달러당 1356.7원이었다."
    b["documents"][0]["content"] += "\n"+quote
    obs = MarketObservation.model_validate({
        "id": "prior_fx", "instrument": "usdkrw", "value": "1356.7", "unit": "KRW/USD",
        "session_date": day, "as_of": at, "basis": "intraday", "venue": "서울 외환시장",
        "evidence": [{"source_id": b["documents"][0]["id"], "quote": quote}],
    })
    p.observations.append(obs)
    assert validate(p, b)[obs.id] == "previous_close_time_unavailable"
    b["exchange_closes"] = {"KR_previous": at.isoformat()}
    assert obs.id not in validate(p, b)
    main = render(p, b)[0][0]
    assert "한국 전일 참고 · 달러/원" in main and f"{day:%m/%d} 15:30 KST" in main
    obs.as_of += timedelta(minutes=1)
    assert validate(p, b)[obs.id] == "wrong_previous_reference_time"


def test_topic_sampling_keeps_major_issues_when_local_news_are_newest():
    docs = [doc("close-a", "Nasdaq market close"), doc("close-b", "S&P market close"),
            doc("policy", "Federal Reserve inflation policy and rates"),
            doc("world", "Iran proposes reopening strait; sanctions remain"),
            doc("earnings", "Nvidia earnings guidance changes"),
            doc("oil", "Oil prices rise as supply falls")]
    docs += [doc(f"local-{n}", "지역 반도체 전력기기 체육관 준공",
                 published_at=definition().cutoff-timedelta(seconds=n)) for n in range(80)]
    selected = select_documents(docs, "am", limit=12)
    assert {d.id for d in docs[:6]} <= {d.id for d in selected}
    assert not inventory(selected)["missing_topics"]
    assert len(selected) <= 12 and not any(d.id.startswith("local") for d in selected)


def test_bank_name_does_not_masquerade_as_central_bank_and_small_ipo_is_demoted():
    succession = doc("succession", "신한은행장 승계절차 개시")
    central_bank = doc("central-bank", "한은, 원화 결제망 시범운영")
    labor = doc("labor", "코레일 자회사 직접고용 요구")
    listing = doc("listing", "[특징주] 소형주 코스닥 상장 첫날 급등")
    etf = doc("etf", "S&P 500 ETF 구성종목 절반 교체")
    assert "macro_policy" not in topics(succession)
    assert "macro_policy" not in topics(labor)
    assert "macro_policy" in topics(central_bank)
    selected = select_documents([succession, central_bank, labor, listing, etf], "pm", limit=2)
    assert selected == [central_bank]


@pytest.mark.parametrize("title,topic", [
    ("미국 무역적자 확대…수입 증가", "macro_policy"),
    ("미국 소매판매 예상 하회", "macro_policy"),
    ("중국 산업생산 둔화", "macro_policy"),
    ("GDP revised lower", "macro_policy"),
    ("Trade deficit widens", "macro_policy"),
    ("Retail sales contract", "macro_policy"),
    ("자동차 판매 감소…해외 수요 약화", "corporate"),
    ("금융주, 대출 손실 부담 확대", "corporate"),
    ("제약 신약 승인…출시 준비", "corporate"),
    ("Utilities lead the session", "corporate"),
    ("Airlines cut capacity", "corporate"),
])
def test_material_macro_and_non_chip_sector_candidates_are_not_dropped_by_topic_filter(title, topic):
    # These are synthetic collection candidates, not claims about an actual session.
    candidate = doc("distinct-development", title)
    docs = [doc("close-a", "Nasdaq market close"), doc("close-b", "S&P market close"),
            doc("chips", "Nvidia earnings guidance changes"), doc("oil", "Oil supply falls"), candidate]
    assert topic in topics(candidate)
    assert candidate.id in {d.id for d in select_documents(docs, "am", limit=6)}


def test_english_kospi_close_can_be_second_report_before_older_korean_recap():
    end = definition().cutoff
    full = doc("full", "코스피 마감·코스닥 종가와 투자자별 수급", content="코스피와 코스닥이 마감했다.",
               published_at=end-timedelta(minutes=5))
    english = doc("english", "KOSPI and KOSDAQ close higher", content="Both indexes closed higher.",
                  published_at=end-timedelta(minutes=6))
    recap = doc("recap", "코스피 상승 마감", content="코스피가 마감했다.",
                published_at=end-timedelta(minutes=10))
    selected = select_documents([recap, english, full], "pm", limit=2)
    assert [item.id for item in selected] == ["full", "english"]


def test_month_named_in_source_can_support_its_exact_korean_month_without_modal_false_positive():
    assert Decimal(7) in numbers("Saudi crude accounted for 34.1% of imports in July.")
    assert Decimal(5) not in numbers("The committee may change its policy.")
    assert Decimal(8) in numbers("August's JOLTS report forecasts 7.24 million job openings.")
    assert Decimal(5) in numbers("May’s inflation data will be released later.")
    assert Decimal(5) not in numbers("May's remarks supported the proposal.")
    assert Decimal(9) not in numbers("August's JOLTS report forecasts 7.24 million job openings.")


@pytest.mark.parametrize("source, translated", [
    ("Oct. 27-28", "10월 27~28일"),
    ("since October 2022", "2022년 10월 이후"),
    ("27 September", "9월 27일"),
    ("Sept. 2026", "2026년 9월"),
    ("a quarter percentage point increase", "0.25%포인트 인상"),
    ("a fifth of global oil shipments", "세계 원유 운송의 5분의 1"),
    ("three quarters of revenue", "매출의 4분의 3"),
    ("half-point increase", "0.5포인트 인상"),
])
def test_exact_month_and_fraction_translation_preserves_numeric_evidence(source, translated):
    assert numbers(translated) <= numbers(source)


def test_fraction_normalization_does_not_treat_fiscal_quarters_or_ordinals_as_proportions():
    assert numbers("The fifth company reported strong quarter earnings.") == set()
    assert Decimal("0.25") not in numbers("The fourth quarter of 2026")


def test_compact_percent_ranges_preserve_upper_bounds_without_erasing_negative_values():
    source = "The target range is 3.75%-4%, and underlying inflation is 2.5%-3%."
    assert numbers("목표범위 3.75~4%, 기조 물가 2.5~3%") <= numbers(source)
    assert Decimal("-4") not in numbers(source)
    assert Decimal("-3") in numbers("The change was 5% -3%")
    assert not prose_numbers_supported("목표범위 3.75~4%", ["The target changed 3.75% -4%"])


def test_signed_decline_in_prose_requires_same_magnitude_and_downward_quote():
    quote = "코스피는 전 거래일보다 191.18포인트(2.70%) 내린 6,889.74로 마감했다."
    assert prose_numbers_supported("전일 코스피는 6,889.74(-2.70%)였습니다.", [quote])
    assert not prose_numbers_supported("전일 코스피는 6,889.74(-3.70%)였습니다.", [quote])
    assert not prose_numbers_supported("코스피는 -2.70%였습니다.", ["코스피는 2.70% 올랐다."])


def test_wti_delivery_month_cannot_settle_after_certain_expiry():
    assert expired_wti_contract("10월물 WTI 마감 92.60달러", date(2026, 9, 28))
    assert expired_wti_contract("10월 인도분 미국 서부텍사스산원유(WTI)", date(2026, 9, 28))
    assert not expired_wti_contract("10월물 WTI 마감 92.60달러", date(2026, 9, 21))
    assert not expired_wti_contract("11월물 WTI 마감 92.60달러", date(2026, 9, 28))
    assert not expired_wti_contract("10월물 브렌트유 마감 92.60달러", date(2026, 9, 28))
    assert not expired_wti_contract("2027년 10월물 WTI 마감 92.60달러", date(2026, 9, 28))
    assert expired_wti_contract("1월물 WTI 마감 92.60달러", date(2026, 12, 28))
    assert expired_wti_contract("WTI 10월물 마감 92.60달러", date(2026, 9, 28))


def test_expired_wti_price_is_rejected_even_when_original_quotes_it():
    p, data = analytical_proposal(), bundle()
    quote = "뉴욕상업거래소에서 9월물 WTI는 배럴당 92.60달러에 마감했다."
    data["documents"][0]["content"] += "\n"+quote
    p.overview[0].text = "9월물 WTI는 배럴당 92.60달러에 마감했습니다."
    p.overview[0].evidence[0].quote = quote
    assert validate(p, data)[p.overview[0].id] == "expired_wti_delivery_contract"
    p.overview[0].text = "WTI는 배럴당 92.60달러에 마감했습니다."
    assert validate(p, data)[p.overview[0].id] == "expired_wti_delivery_contract"

    valid = quote.replace("9월물", "10월물")
    data["documents"][0]["content"] += "\n"+valid
    p.overview[0].text = "10월물 WTI는 배럴당 92.60달러에 마감했습니다."
    p.overview[0].evidence[0].quote = valid
    assert p.overview[0].id not in validate(p, data)

    p.overview[0].text = "WTI는 보도된 인도월과 거래일이 맞지 않아 종가를 제시하지 않습니다."
    p.overview[0].evidence[0].quote = quote
    assert not claims_wti_price(p.overview[0].text)
    assert p.overview[0].id not in validate(p, data)


def test_long_quote_repairs_only_unambiguous_final_period_or_comma():
    source = ("If an airline lands, you cannot provide it fuel, landing services or ticket sales, "
              "or you will be knocked out of the dollar system.")
    wrong_end = source[:-1]+","
    assert canonical_source_quote(wrong_end, [source]) == source
    assert canonical_source_quote(source.replace("cannot", "can"), [source]) is None
    assert canonical_source_quote("This is a short quote,", ["This is a short quote."]) is None
    assert canonical_source_quote(wrong_end, [source, source[:-1]+","]) == wrong_end
    p, data = proposal(), bundle()
    data["documents"][0]["content"] += "\n"+source
    p.summary[0].text = "항공사 지원 제한은 달러 결제 접근을 위협합니다."
    p.summary[0].evidence[0].quote = wrong_end
    assert p.summary[0].id not in validate(p, data)
    assert p.summary[0].evidence[0].quote == source


def test_issue_specific_next_check_is_in_main_when_separate_watchpoint_exists():
    p, data = proposal(), bundle()
    parts, quality = render(p, data)
    assert p.issues[0].next_check.text in parts[0]
    assert p.issues[0].next_check.id in main_post_item_ids(p, data)
    assert p.issues[0].next_check.text not in "\n".join(parts[1:])
    assert quality["format_version"] == 18


def test_supported_rate_baseline_in_issue_assessment_survives_to_reviewed_main():
    p, data = analytical_proposal(), bundle()
    quote = "다음 달 인상 확률은 70.3%로 일주일 전 57.6%보다 높아졌다."
    data["documents"][0]["content"] += "\n"+quote
    assessment = p.issues[0].interpretation
    assessment.text = quote+" 금리선물 평가이며 확정된 정책 결정은 아닙니다."
    assessment.evidence[0].quote = quote
    assert validate(p, data) == {}
    parts, _ = render(p, data)
    assert assessment.text in parts[0]
    assert assessment.id in main_post_item_ids(p, data)
    assert assessment.text not in "\n".join(parts[1:])
    verdict = review().model_dump(mode="json")
    verdict["source_assessments"][0].update(item_ids=[assessment.id], material_facts=[{
        "fact": "인상 확률의 이전 기준과 현재 수준", "quote": quote,
        "main_item_ids": [assessment.id],
    }])
    validate_review(BriefReview.model_validate(verdict), p, data)


def test_korean_relative_calendar_date_and_afternoon_time_are_exactly_equivalent():
    source = "내달 2일 미 동부시간 오전 8시 30분(한국시간 오후 9시 30분) 발표한다."
    published = SourceDocument.model_validate(bundle()["documents"][0]).published_at
    equivalent = calendar_equivalent_numbers(source, published)
    assert "10월 2일" in equivalent
    assert "한국 21:30" in equivalent
    assert prose_numbers_supported("10월 2일 한국 21:30 발표", [equivalent])
    assert not prose_numbers_supported("10월 3일 한국 21:30 발표", [equivalent])
    assert not prose_numbers_supported("10월 2일 한국 20:30 발표", [equivalent])


def test_undated_calendar_never_invents_a_relative_date_but_preserves_explicit_time():
    source = '내달 2일 발표. 한국시간 오후 9시 30분.'
    equivalent = calendar_equivalent_numbers(source, None)
    assert '10월 2일' not in equivalent
    assert '한국 21:30' in equivalent
    assert not prose_numbers_supported('10월 2일 발표', [equivalent])


def test_explicit_english_month_supports_only_its_korean_calendar_literal():
    quote = 'The earnings report is expected in late October.'
    assert prose_numbers_supported('10월 하순 예상 실적 발표를 확인한다.', [quote])
    assert not prose_numbers_supported('11월 실적 발표를 확인한다.', [quote])
    assert not prose_numbers_supported('10% 실적 증가를 확인한다.', [quote])
    assert not prose_numbers_supported('10억원 매출을 확인한다.', [quote])
    assert not prose_numbers_supported('10월 발표를 확인한다.', ['October Corporation announced earnings.'])


def test_official_calendar_without_publication_date_is_validated_without_crashing():
    p, data = proposal(), bundle()
    doc = deepcopy(data['documents'][0])
    quote = '2026년 9월 22일 회의이며 발표 시각은 미정이다.'
    doc.update(id='official-calendar', kind='calendar', content=quote, published_at=None)
    data['documents'].append(doc)
    p.calendar = [CalendarEvent(id='official-event', title='공식 회의', at=None,
        source_timezone='시각 미확인', status='time_unconfirmed', note=quote,
        evidence=[{'source_id': doc['id'], 'quote': quote}])]
    assert 'official-event' not in validate(p, data)


def test_major_release_three_days_ahead_remains_eligible_for_daily_brief():
    p, data = proposal(), bundle()
    quote = "고용보고서는 사흘 뒤 발표될 예정이다."
    data["documents"][0]["content"] += "\n"+quote
    p.calendar = [CalendarEvent.model_validate({
        "id": "jobs_release", "title": "고용보고서", "at": (definition().cutoff+timedelta(days=3)).isoformat(),
        "source_timezone": "Asia/Seoul", "status": "scheduled",
        "evidence": [{"source_id": "source-1", "quote": quote}],
    })]
    assert "jobs_release" not in validate(p, data)
    p.calendar[0].at = definition().cutoff+timedelta(days=8)
    assert validate(p, data)["jobs_release"] == "event_outside_window"


def test_valid_translation_keeps_market_issue_but_changed_amount_removes_it():
    p, data = analytical_proposal(), bundle()
    text = "The central bank approved a quarter percentage point increase."
    data["documents"][0]["content"] += "\n"+text
    p.issues[0].fact.text = "중앙은행은 금리를 0.25%포인트 인상했습니다."
    p.issues[0].fact.evidence[0].quote = text
    assert not validate(p, data)
    p.issues[0].fact.text = "중앙은행은 금리를 0.5%포인트 인상했습니다."
    rejected = validate(p, data)
    assert rejected[p.issues[0].fact.id] == "unsupported_prose_number"
    assert prune(p, rejected).issues == []


def test_quoted_etf_price_on_non_trading_day_cannot_support_a_return():
    p, data = proposal(), bundle()
    data["documents"][0]["published_at"] = "2026-09-21T00:00:00+00:00"
    sunday = "KODEX200은 1월 2일 6만 460원에서 지난 20일에는 10만 9,285원으로 올라 80.76% 수익률을 기록했다."
    data["documents"][0]["content"] += "\n" + sunday
    p.overview[0].text = "KODEX200은 지난 20일 기준 80.76% 올랐다고 보도됐습니다."
    p.overview[0].evidence[0].quote = sunday
    assert validate(p, data)["overview"] == "source_price_date_non_session"

    friday = sunday.replace("지난 20일", "지난 18일")
    data["documents"][0]["content"] += "\n" + friday
    p.overview[0].text = p.overview[0].text.replace("20일", "18일")
    p.overview[0].evidence[0].quote = friday
    assert "overview" not in validate(p, data)
    assert not non_session_korean_listed_price(
        "KODEX200은 지난 20일에는 100원으로 가입비를 정했다.",
        SourceDocument.model_validate(data["documents"][0]).published_at,
    )
    assert not non_session_korean_listed_price(
        "KODEX200 운용자산은 지난 20일에는 100억원으로 올라섰다.",
        SourceDocument.model_validate(data["documents"][0]).published_at,
    )
    published = SourceDocument.model_validate(data["documents"][0]).published_at
    assert non_session_korean_listed_price("KODEX200은 2025년 9월 20일 10만원으로 올랐다.", published)
    assert not non_session_korean_listed_price("KODEX200은 2024년 9월 20일 10만원으로 올랐다.", published)


@pytest.mark.parametrize("source, expected", [
    ("1조 2천827억 원", "1282700000000"),
    ("1조\n2천22억 원", "1202200000000"),
    ("3 천 169억 원", "316900000000"),
    ("-1조 2천억 원", "-1200000000000"),
])
def test_spacing_inside_korean_quantities_preserves_the_whole_amount(source, expected):
    assert numbers(source) == {Decimal(expected)}


def test_korean_quantity_spacing_does_not_merge_separate_amounts_or_subtraction():
    assert numbers("1조 원과 2천억 원") == {Decimal("1e12"), Decimal("2e11")}
    assert numbers("1조 -2천억 원") == {Decimal("1e12"), Decimal("-2e11")}


@pytest.mark.parametrize("claim, expected", [
    ("건설은 5.66% 하락했습니다.", True),
    ("건설은 5.66%, 기계·장비는 3.22% 하락했습니다.", True),
    ("건설은 5.66%, 기계·장비는 3.22% 내렸다.", True),
    ("건설은 5.65% 하락했습니다.", False),
    ("건설은 5.66% 상승했습니다.", False),
    ("건설은 5.66% 상승했고 기계·장비는 3.22% 하락했습니다.", False),
    ("건설은 5.66% 하락하지 않았습니다.", False),
    ("건설은 5.66%포인트 하락했습니다.", False),
    ("건설은 5.66원 하락했습니다.", False),
    ("5.66% 수준입니다. 기계·장비는 3.22% 하락했습니다.", False),
])
def test_signed_percentage_prose_preserves_direction_unit_and_other_occurrences(claim, expected):
    quotes = ["건설(-5.66%), 기계·장비(-3.22%), IT서비스(-2.40%) 업종은 하락했다."]
    assert prose_numbers_supported(claim, quotes) is expected


def test_positive_percentage_cannot_support_decline_and_source_words_can_supply_sign():
    assert not prose_numbers_supported("주가는 5.66% 하락했습니다.", ["주가 변동률 +5.66%"])
    assert prose_numbers_supported("주가는 5.66% 하락했습니다.", ["주가는 5.66% 하락했다."])


def test_supported_decline_keeps_overview_but_wrong_direction_is_rejected():
    p, data = analytical_proposal(), bundle()
    quote = "건설(-5.66%), 기계·장비(-3.22%) 업종은 하락했다."
    data["documents"][0]["content"] += "\n"+quote
    p.overview[0].text = "건설은 5.66%, 기계·장비는 3.22% 하락했습니다."
    p.overview[0].evidence[0].quote = quote
    assert not validate(p, data)
    p.overview[0].text = "건설은 5.66%, 기계·장비는 3.22% 상승했습니다."
    assert validate(p, data)[p.overview[0].id] == "unsupported_prose_number"


def test_pm_same_day_fx_reference_at_equity_close_is_dated_but_not_stale():
    edition = definition("pm")
    data = bundle(edition)
    quote = "오후 3시 30분 기준 원/달러 환율은 1,381.0원입니다."
    data["documents"][0]["content"] += "\n"+quote
    at = edition.cutoff.replace(hour=15, minute=30, second=0, microsecond=0)
    data["exchange_closes"] = {"KR": at.isoformat()}
    raw = {"id": "fx", "instrument": "usdkrw", "value": "1381.0", "unit": "KRW/USD",
           "session_date": str(edition.kr_session), "as_of": at.isoformat(), "basis": "intraday",
           "venue": "서울 외환시장", "evidence": [{"source_id": "source-1", "quote": quote}]}
    p = BriefProposal(summary=[], overview=[], observations=[raw], issues=[], internals=[],
                      watchpoints=[], watch_results=[], calendar=[])
    assert "fx" not in validate(p, data)
    p.observations[0] = p.observations[0].model_copy(update={"as_of": at-timedelta(hours=6)})
    assert validate(p, data)["fx"] == "stale_intraday_observation"


def test_us_story_and_korean_open_cannot_replace_korean_closing_report():
    us = doc("us", "뉴욕증시 마감", content="코스피는 전일 종가를 확인했다. 뉴욕증시는 마감했다.")
    opening = doc("open", "코스피 개장", content="코스피는 전일 종가 대비 상승했다.",
                  published_at="2026-09-22T00:10:00Z")
    closing = doc("close", "KOSPI Up Tuesday", content="KOSPI closed higher, KOSDAQ declined.",
                  published_at="2026-09-22T07:00:00Z")
    assert not market_report(us, "pm") and not market_report(opening, "pm")
    assert market_report(closing, "pm")
    assert select_documents([us, opening, closing], "pm")[0].id == "close"


def test_etf_flow_story_with_syndicated_close_caption_does_not_take_closing_report_slot():
    etf = doc(
        "etf-flow", "'하락 예감?' 코스피·반도체 추종 ETF서 한 주간 뭉칫돈 이탈",
        content="사진 설명: 코스피 6,889.74로 마감, 코스닥 846.58로 마감. "
                "본문: 18∼23일 KODEX 200에서 2천199억원 순유출됐다.",
        published_at="2026-09-28T08:04:00Z",
    )
    closing = doc(
        "kr-close", "연휴서 돌아온 코스피, 2.7%↓…다시 7천피 밑으로",
        content="28일 코스피는 6,889.74로 마감했다. 코스닥은 846.58로 마감했다. "
                "외국인과 기관은 순매도하고 개인은 순매수했다.",
        published_at="2026-09-28T07:19:35Z",
    )
    assert not market_report(etf, "pm")
    assert market_report(closing, "pm")
    second_close = doc(
        "kr-close-b", "코스피 2.7% 하락 마감",
        content="28일 코스피는 6,889.74로 마감했고 코스닥은 846.58로 상승 마감했다.",
        published_at="2026-09-28T07:09:00Z",
    )
    assert [d.id for d in select_documents([etf, closing, second_close], "pm", limit=3)] == [
        "kr-close", "kr-close-b", "etf-flow",
    ]


def test_cross_publisher_reprint_uses_one_slot_but_close_corrob_keeps_two():
    story = doc("story", "카타르, 호르무즈 대치 속 LNG 공급 불가항력 선언 추가 연장")
    reprint = doc("reprint", '"카타르, 호르무즈 대치 속 LNG 공급 불가항력 선언 추가 연장"')
    selected = select_documents([story, reprint], "pm")
    assert len(selected) == 1
    close_a = doc("close-a", "코스피 2.7% 하락 마감", content="28일 코스피와 코스닥은 마감했다.",
                  published_at="2026-09-28T07:09:00Z")
    close_b = doc("close-b", "코스피 2.7% 하락 마감", content="28일 코스피와 코스닥은 마감했다.",
                  published_at="2026-09-28T07:09:00Z")
    assert len(select_documents([close_a, close_b], "pm")) == 2


def test_market_decline_language_and_quarter_wording_keep_supported_numbers():
    assert reported_change_supported(Decimal("-5.08"), "%", ["삼성전자 주가는 5.08% 급락했다."])
    assert reported_change_supported(Decimal("-3.27"), "%", ["spot gold prices were off by 3.27%"])
    assert reported_change_supported(Decimal("-4.92"), "%", ["spot silver had shed 4.92%"])
    assert prose_numbers_supported(
        "세계 중앙은행의 2분기 금 매입은 289톤이었다.",
        ["global central banks purchased a record 289 metric tons in the second quarter"],
    )


def test_negative_etf_flow_quote_supports_positive_outflow_magnitude_only():
    quote = "TIGER 200이 -1천404억원으로 자금 순유출 2위를 차지했다."
    assert prose_numbers_supported("TIGER 200에서 1천404억원이 순유출됐다.", [quote])
    assert not prose_numbers_supported("TIGER 200에서 1천404억원이 순유입됐다.", [quote])


def test_overview_is_validated_and_thin_brief_cannot_claim_complete_content():
    p = proposal()
    p.overview = []
    _, quality = render(p, bundle())
    assert quality["reduced"] and not quality["substantive"]
    p.overview = [p.summary[0].model_copy(update={"id": "overview", "text": "매출이 9999% 증가했습니다."})]
    rejected = validate(p, bundle())
    assert rejected["overview"] == "unsupported_prose_number"
    assert not prune(p, rejected).overview
    _, quality = render(proposal(), bundle(), rejected={"removed": "unsupported_prose_number"})
    assert quality["substantive"] and quality["reduced"]


def test_literal_source_quote_linebreak_survives_nested_artifact_decoding():
    p = proposal()
    p.summary[0].evidence[0].quote = "First source paragraph.\nSecond source paragraph."
    r = response({"request_id": "case-write"}, p)
    r.decision.artifacts[0].content = r.decision.artifacts[0].content.replace("\\n", "\n")
    assert artifact(r, BriefProposal) == p
    r.decision.artifacts[0].content = r.decision.artifacts[0].content.replace("First source", "\x00First source")
    with pytest.raises(json.JSONDecodeError):
        artifact(r, BriefProposal)
    r.decision.artifacts[0].content = '{"summary": [broken]}'
    with pytest.raises(json.JSONDecodeError):
        artifact(r, BriefProposal)


def test_contiguous_source_quote_may_normalize_linebreak_but_not_change_words():
    data = bundle()
    data["documents"][0]["content"] += "\n기관은 순매수했다.\n외국인은 순매도했다."
    p = proposal()
    p.summary[0].evidence[0].quote = "기관은 순매수했다. 외국인은 순매도했다."
    assert "summary" not in validate(p, data)
    p.summary[0].evidence[0].quote = "기관은 순매수했다. 외국인은 순매수했다."
    assert validate(p, data)["summary"] == "evidence_not_in_frozen_original"


def test_exact_source_title_can_support_a_claim_but_changed_title_cannot():
    data = bundle()
    data["documents"][0]["title"] = "금리 0.25%p 인상 때 가계 이자 3.3조원 증가"
    p = proposal()
    p.summary[0].evidence[0].quote = data["documents"][0]["title"]
    assert "summary" not in validate(p, data)
    p.summary[0].evidence[0].quote = "금리 0.25%p 인상 때 가계 이자 4.3조원 증가"
    assert validate(p, data)["summary"] == "evidence_not_in_frozen_original"


@pytest.mark.asyncio
async def test_evaluation_call_preserves_fault_and_never_calls_again_over_receipt(tmp_path, monkeypatch):
    request_path, receipt_path = tmp_path/"request.json", tmp_path/"receipt.json"
    request_path.write_text(ProviderRequest(request_id="evaluation-frozen", model="gpt-6-astra", prompt="Synthetic test").model_dump_json())
    calls = []

    class Client:
        def __init__(self, *args, **kwargs):
            assert kwargs["timeout_seconds"] == 960

        async def run(self, request):
            calls.append(request.request_id)
            raise ProviderFault("uncertain", "Synthetic lost response")

    monkeypatch.setattr(evaluate_briefing, "RuntimeClient", Client)
    monkeypatch.setattr(evaluate_briefing, "Settings", lambda: Settings(model_runtime_token="synthetic-only", company_model_timeout_seconds=960))
    monkeypatch.setattr(sys, "argv", ["evaluate", "--call", str(request_path), "--output", str(receipt_path)])
    with pytest.raises(SystemExit):
        await evaluate_briefing.main()
    receipt = json.loads(receipt_path.read_text())
    assert receipt["fault"]["code"] == "uncertain" and not receipt["automatic_retry"]
    with pytest.raises(ValueError, match="Existing receipt"):
        await evaluate_briefing.main()
    assert calls == ["evaluation-frozen"]


def test_offline_repair_uses_mechanically_accepted_draft_and_keeps_failures():
    p = proposal()
    p.internals = [p.summary[0].model_copy(update={"id": "bad-internal", "text": "등락률은 9999%입니다."})]
    written, reviewed = response({"request_id": "write"}, p), response({"request_id": "review"}, review())
    revised = evaluate_briefing.prepare_revision(bundle(), written, reviewed)
    feedback = revised["revision_feedback"]
    assert feedback["previous_draft"]["internals"] == []
    assert feedback["rejected"] == {"bad-internal": "unsupported_prose_number"}
    assert feedback["repair_mode"] == "full_proposal"
    # Review receives the new accepted proposal, not the prior critique or draft.
    assessed = evaluate_briefing.assess(revised, response({"request_id": "revise"}, proposal()))
    assert "revision_feedback" not in assessed["bundle"]
    with pytest.raises(ValueError, match="Only one"):
        evaluate_briefing.prepare_revision(revised, written, reviewed)


def test_six_substantive_issues_survive_in_main_post_without_silent_thread_overflow():
    p = analytical_proposal().model_dump(mode="json")
    p["overview"] = [{**p["summary"][0], "id": "overview", "text": "미국 지수 상승은 반도체에 집중됐고 비기술 업종은 엇갈렸습니다."}]
    issue = p["issues"][0]
    p["issues"] = []
    for n in range(6):
        copy = deepcopy(issue)
        copy["headline"] = "서로 다른 중요 사건 " + chr(65+n)
        for part in (copy["fact"], copy["interpretation"], copy["next_check"], *copy["analysis"].values()):
            if isinstance(part, dict):
                part["id"] += f"-{n}"
        copy["fact"]["text"] *= 7
        copy["analysis"]["mechanism"]["text"] *= 5
        p["issues"].append(copy)
    parts, quality = render(BriefProposal.model_validate(p), bundle())
    assert len(parts[0]) > 2900
    assert all(i["headline"] in parts[0] for i in p["issues"])
    assert all(i["analysis"]["mechanism"]["text"] in parts[0] for i in p["issues"])
    assert all(i["analysis"]["alternative"]["text"] in parts[0] for i in p["issues"])
    assert "다음 확인할 것" in parts[0] and quality["substantive"]
    assert len(parts[0]) <= 9500 and len(parts) <= 5


def test_main_claim_keeps_links_to_all_its_supporting_sources():
    p, b = proposal(), bundle()
    for n in range(3):
        extra = doc(f"extra-{n}", f"Supporting original {n}")
        b["documents"].append(extra.model_dump(mode="json"))
        p.summary[0].evidence.append(p.summary[0].evidence[0].model_copy(update={"source_id": extra.id}))
    parts, _ = render(p, b)
    assert all(d["url"] in parts[0] for d in b["documents"])


def test_selected_calendar_events_remain_visible_and_unknown_time_is_labelled_once():
    p = proposal()
    p.calendar = [CalendarEvent.model_validate({
        "id": f"event-{n}", "title": f"행사 {n}", "at": None,
        "source_timezone": "America/New_York", "status": "time_unconfirmed",
        "note": "현지 23일 예정. 정확한 시각은 미확인.",
        "evidence": p.summary[0].evidence,
    }) for n in range(1, 5)]
    p.watchpoints = [p.watchpoints[0].model_copy(update={"id": f"watch-{n}", "text": f"시장 조건 {n}"})
                     for n in range(1, 4)]
    parts, _ = render(p, bundle())
    main, detail = parts[0], "\n".join(parts[1:])
    assert "행사 1" in main and "행사 2" in main
    assert "시장 조건 1" in main and "시장 조건 2" in main
    assert all(f"행사 {n}" in main for n in range(1, 5))
    assert "시장 조건 3" not in main and "시장 조건 3" in detail
    assert "시각 미확인 · 행사" not in main and "[미확인]" not in main
    assert "추가 확인 사항" in detail and "추가 확인 일정" not in detail


def test_source_coverage_cannot_be_empty_duplicated_or_claim_uncited_items():
    p, b = proposal(), bundle()
    validate_review(review(), p, b)
    for changes in ({"source_assessments": []},
                    {"source_assessments": review().source_assessments*2}):
        with pytest.raises(ValueError, match="review_source_coverage_incomplete"):
            validate_review(review().model_copy(update=changes), p, b)
    b["documents"].append(doc("uncited", "Iran war and oil supplies").model_dump(mode="json"))
    v = review().model_dump()
    v["source_assessments"].append({"source_id": "uncited", "treatment": "covered",
                                   "reason": "이 기사를 인용했다고 잘못 주장한 검토", "item_ids": ["summary"],
                                   "material_facts": []})
    with pytest.raises(ValueError, match="review_coverage_not_cited"):
        validate_review(BriefReview.model_validate(v), p, b)


def test_missing_major_issue_preserves_useful_partial_but_cannot_pass(brief):  # noqa: F811
    store, clock = brief
    edition = seed(brief)
    store.commit(response(store.prepare()["request"], analytical_proposal()))
    critic = store.prepare()["request"]
    clock["at"] = edition.due_at
    verdict = review().model_dump()
    verdict.update(verdict="reduce", concerns=["원문에 있는 중요한 정책 변화의 설명이 빠졌습니다."])
    verdict["checks"]["coverage"] = False
    store.commit(response(critic, BriefReview.model_validate(verdict)))
    with store.db.transaction() as conn:
        row = conn.execute("SELECT * FROM brief_editions WHERE state='ready'").fetchone()
    assert row["proposal"] and row["quality"]["reduced"]
    assert not row["quality"]["fallback"]


@pytest.mark.parametrize("repair_passes", [True, False])
def test_confirmed_editorial_failure_gets_only_one_frozen_repair_and_recheck(brief, repair_passes):  # noqa: F811
    store, clock = brief
    edition = seed(brief)
    store.commit(response(store.prepare()["request"]))
    value = review().model_dump()
    value.update(verdict="reduce", concerns=["제공 원문의 중요한 정책 변화가 충분히 설명되지 않았습니다."])
    value["checks"]["depth"] = False
    failed = BriefReview.model_validate(value)
    store.commit(response(store.prepare()["request"], failed))
    revision = store.prepare()["request"]
    assert revision["request_id"].endswith("-revise")
    assert "revision_feedback" in revision["prompt"] and "previous_draft" in revision["prompt"]
    payload = json.loads(revision["prompt"].split("BRIEF DATA JSON:\n")[1])
    assert "quote" not in payload["revision_feedback"]["previous_draft"]["summary"][0]["evidence"][0]
    original = "".join(payload["original_quotes"][reference][1]
                       for reference in payload["documents"][0]["original_quote_refs"])
    assert original == bundle()["documents"][0]["content"]
    assert store.prepare()["request"] == revision
    store.commit(response(revision))
    final = store.prepare()["request"]
    assert final["request_id"].endswith("-final_review")
    assert "main_post_preview" in final["prompt"]
    store.commit(response(final, review() if repair_passes else failed))
    with store.db.transaction() as conn:
        row = conn.execute("SELECT * FROM brief_editions WHERE id=%s", (edition.id,)).fetchone()
        calls = conn.execute("SELECT phase FROM brief_calls WHERE edition_id=%s", (edition.id,)).fetchall()
    assert row["state"] == "ready" and row["quality"]["revision_used"]
    assert row["quality"]["reduced"] is not repair_passes
    assert {c["phase"] for c in calls} == {"write", "review", "revise", "final_review"}
    clock["at"] = edition.due_at
    store.flush()
    if repair_passes:
        # A real first draft cannot qualify a fixture or unfinished correction.
        with store.db.transaction() as conn:
            conn.execute("UPDATE brief_calls SET response=jsonb_set(response,'{provider}','\"codex\"') WHERE edition_id=%s AND phase IN ('write','review')", (edition.id,))
        result = qualify(store.company, at=definition("pm").due_at+timedelta(minutes=15))
        check = next(c for c in result["editions"] if c["id"] == edition.id)
        assert "real_codex_not_verified" in check["reasons"]
        with store.db.transaction() as conn:
            conn.execute("UPDATE brief_calls SET response=jsonb_set(response,'{provider}','\"codex\"') WHERE edition_id=%s", (edition.id,))
        result = qualify(store.company, at=definition("pm").due_at+timedelta(minutes=15))
        check = next(c for c in result["editions"] if c["id"] == edition.id)
        assert "real_codex_not_verified" not in check["reasons"]
    assert store.prepare()["state"] == "idle"


def test_exact_kilobarrel_flow_and_explicit_prewar_baseline_normalize_without_estimation():
    source = ("Clearance stood at 10,591 kilobarrels a day on Saturday, "
              "against a pre-war baseline of 17,133.")
    assert numbers("하루 1,059만1천배럴, 전쟁 전 1,713만3천배럴") <= numbers(source)
    assert Decimal(10591000) in numbers("10,591 kilobarrels")
    assert Decimal(17133000) not in numbers("10,591 kilobarrels a day. A separate baseline of 17,133.")
    assert Decimal(17133000) not in numbers("10,591 kilobarrels a day, against a pre-war baseline of 17,133 percent.")
    assert Decimal(17133000) not in numbers("10,591 kilobarrels a day, against a pre-war baseline of 17,133 barrels.")
    assert Decimal(10590000) not in numbers(source)


def test_material_fact_patch_preserves_existing_text_prices_and_conditions():
    original = proposal()
    patch = MaterialFactPatch.model_validate({"additions": [{
        "id": "fact", "text": "시장 참여는 혼조였습니다.",
        "evidence": [original.issues[0].fact.evidence[0].model_dump()],
    }]})
    corrected = apply_material_fact_patch(original, patch, {"fact": ["source-1"]})
    expected = original.model_dump()
    expected["issues"][0]["fact"]["text"] += " 시장 참여는 혼조였습니다."
    assert corrected.model_dump() == expected
    assert original.issues[0].fact.text != corrected.issues[0].fact.text
    bad = MaterialFactPatch.model_validate({"additions": [{"id": "fact", "text": "추가 기준은 9999%입니다."}]})
    assert validate(apply_material_fact_patch(original, bad, {"fact": ["source-1"]}), bundle())["fact"] == "unsupported_prose_number"


@pytest.mark.parametrize("values,scope", [
    ([{"id": "condition", "text": "바꾼 조건"}], {"condition": ["source-1"]}),
    ([{"id": "sp500", "text": "바꾼 가격"}], {"sp500": ["source-1"]}),
    ([{"id": "fact", "text": "추가 사실"}], {"view": ["source-1"]}),
    ([{"id": "fact", "text": "추가 사실"}, {"id": "fact", "text": "중복 사실"}], {"fact": ["source-1"]}),
    ([{"id": "fact", "text": "추가 사실", "evidence": [{"source_id": "outside", "quote": "An unauthorized source passage."}]}], {"fact": ["source-1"]}),
])
def test_material_fact_patch_cannot_escape_verified_append_scope(values, scope):
    with pytest.raises(ValueError, match="material_patch_scope_rejected"):
        apply_material_fact_patch(proposal(), MaterialFactPatch.model_validate({"additions": values}), scope)


def test_material_patch_cannot_exceed_original_claim_limits():
    p = proposal()
    p.issues[0].fact.text = "가"*490
    patch = MaterialFactPatch.model_validate({"additions": [{"id": "fact", "text": "나"*20}]})
    with pytest.raises(ValueError):
        apply_material_fact_patch(p, patch, {"fact": ["source-1"]})


def test_verified_material_append_round_trip_requires_full_review(brief):  # noqa: F811
    store, _ = brief
    edition = seed(brief)
    original = proposal()
    store.commit(response(store.prepare()["request"], original))
    with store.db.transaction() as conn:
        accepted = BriefProposal.model_validate(conn.execute(
            "SELECT proposal FROM brief_editions WHERE id=%s", (edition.id,),
        ).fetchone()["proposal"])
    value = review().model_dump()
    value.update(verdict="reduce", concerns=["제공된 원문의 중요한 반대 근거를 본문에 보완해야 합니다."])
    value["checks"]["coverage"] = False
    value["source_assessments"][0]["material_facts"].append({
        "fact": "시장 참여에 대한 반대 근거", "quote": bundle()["documents"][0]["content"][:100], "main_item_ids": [],
    })
    failed = BriefReview.model_validate(value)
    store.commit(response(store.prepare()["request"], failed))
    request = store.prepare()["request"]
    data = json.loads(request["prompt"].split("BRIEF DATA JSON:\n")[1])
    assert data["revision_feedback"]["repair_mode"] == "material_append"
    assert "MaterialFactPatch" in request["prompt"]
    assert "condition" not in data["revision_feedback"]["allowed_ids"]
    patch = MaterialFactPatch.model_validate({"additions": [{"id": "fact", "text": "시장 참여는 혼조였습니다."}]})
    store.commit(response(request, patch))
    final = store.prepare()["request"]
    assert final["request_id"].endswith("-final_review")
    store.commit(response(final))
    with store.db.transaction() as conn:
        saved = conn.execute("SELECT * FROM brief_editions WHERE id=%s", (edition.id,)).fetchone()
    corrected = BriefProposal.model_validate(saved["proposal"])
    assert corrected.observations == accepted.observations
    assert corrected.issues[0].next_check == original.issues[0].next_check
    assert corrected.issues[0].fact.text.startswith(original.issues[0].fact.text+" ")
    assert not saved["quality"]["reduced"] and saved["quality"]["revision_used"]
    offline = evaluate_briefing.assess(bundle(), response(request, patch), response(final),
                                     previous=response({"request_id": "fixture-write"}, original),
                                     correction_review=response({"request_id": "fixture-review"}, failed))
    assert offline["passed"] and offline["correction"]["mode"] == "material_append"


def test_material_patch_requires_valid_claims_and_preserves_full_repair_for_other_failures():
    value = review().model_dump()
    value.update(verdict="reduce", concerns=["원문의 중요한 사실이 본문에서 누락됐습니다."])
    value["checks"]["coverage"] = False
    value["source_assessments"][0]["material_facts"][0]["main_item_ids"] = []
    p, b = proposal().model_dump(mode="json"), bundle()
    r = BriefReview.model_validate(value)
    assert revision_bundle(b, p, r, {})["revision_feedback"]["repair_mode"] == "material_append"
    assert revision_bundle(b, p, r, {"fact": "unsupported_prose_number"})["revision_feedback"]["repair_mode"] == "full_proposal"
    value["checks"]["readability"] = False
    assert revision_bundle(b, p, BriefReview.model_validate(value), {})["revision_feedback"]["repair_mode"] == "editorial_patch"


def test_editorial_patch_preserves_prices_quotes_kind_and_all_unedited_fields():
    p = proposal()
    p.issues[0].fact.text += " 나스닥은 17,100입니다."
    patch = EditorialPatch.model_validate({"edits": [{"id": "fact", "text": p.issues[0].fact.text+" 시장 참여는 혼조였습니다."}]})
    amended = apply_editorial_patch(p, patch, {"fact": ["source-1"]})
    expected = p.model_dump()
    expected["issues"][0]["fact"]["text"] = patch.edits[0].text
    assert amended.model_dump() == expected
    with pytest.raises(ValueError, match="editorial_patch_loses_verified_numbers"):
        apply_editorial_patch(p, EditorialPatch.model_validate({"edits": [{"id": "fact", "text": "단순한 시장 요약입니다."}]}), {"fact": ["source-1"]})
    with pytest.raises(ValueError, match="editorial_patch_scope_rejected"):
        apply_editorial_patch(p, EditorialPatch.model_validate({"edits": [{"id": "condition", "text": "새 조건"}]}), {"condition": ["source-1"]})


def test_readability_editorial_patch_round_trip_is_reviewed_in_postgres(brief):  # noqa: F811
    store, _ = brief
    edition = seed(brief)
    p = proposal()
    store.commit(response(store.prepare()["request"], p))
    value = review().model_dump()
    value.update(verdict="reduce", concerns=["시장 수급 문장에서 진단을 줄여 가독성을 보완해야 합니다."])
    value["checks"]["readability"] = False
    value["source_assessments"][0]["material_facts"][0]["main_item_ids"] = ["fact"]
    critique = BriefReview.model_validate(value)
    store.commit(response(store.prepare()["request"], critique))
    req = store.prepare()["request"]
    data = json.loads(req["prompt"].split("BRIEF DATA JSON:\n")[1])
    feedback = data["revision_feedback"]
    assert feedback["repair_mode"] == "editorial_patch" and "previous_draft" not in feedback
    assert "unchanged_context" in feedback and feedback["protected_material_facts"]
    assert feedback["allowed_source_ids"] == ["source-1"]
    assert not {"summary", "condition", "sp500"} & set(feedback["allowed_ids"])
    patch = EditorialPatch.model_validate({"edits": [{"id": "fact", "text": p.issues[0].fact.text+" 시장 참여는 혼조였습니다."}],
        "context_additions": [{"issue_fact_id": "fact", "claim": {"id": "market_context",
            "text": "미 국채 금리는 하락했고 기술주 밖의 상승 참여는 혼조였습니다.", "kind": "fact",
            "evidence": [{"source_id": "source-1", "quote": bundle()["documents"][0]["content"]}]}}]})
    store.commit(response(req, patch))
    final = store.prepare()["request"]
    assert final["request_id"].endswith("-final_review")
    final_data = json.loads(final["prompt"].split("BRIEF DATA JSON:\n")[1])
    assert "market_context" in final_data["main_post_item_ids"]
    store.commit(response(final))
    with store.db.transaction() as conn:
        row = conn.execute("SELECT * FROM brief_editions WHERE id=%s", (edition.id,)).fetchone()
    assert not row["quality"]["reduced"] and row["quality"]["revision_used"]
    assert row["proposal"]["issues"][0]["context"][0]["id"] == "market_context"
    assert patch.context_additions[0].claim.text in row["rendered"][0]
    offline = evaluate_briefing.assess(bundle(), response(req, patch), response(final),
        previous=response({"request_id": "write"}, p), correction_review=response({"request_id": "review"}, critique))
    assert offline["passed"] and offline["correction"]["mode"] == "editorial_patch"


def test_editorial_story_context_is_anchored_bounded_and_has_its_own_numeric_proof():
    p, b = proposal(), bundle()
    original = p.model_dump()
    content = "HBM 투자 계획은 250억달러이며 새 공장은 2027년 가동할 예정입니다."
    b["documents"].append({**b["documents"][0], "id": "source-2", "content": content})
    change = {"issue_fact_id": "fact", "claim": {"id": "capex_context", "text": content, "kind": "fact",
                                                "evidence": [{"source_id": "source-2", "quote": content}]}}
    patch = EditorialPatch.model_validate({"context_additions": [change]})
    result = apply_editorial_patch(p, patch, {"fact": ["source-1", "source-2"]})
    assert p.model_dump() == original
    assert result.issues[0].fact == p.issues[0].fact and result.observations == p.observations
    assert validate(result, b) == {}
    assert "capex_context" in main_post_item_ids(result, b)
    assert render(result, b)[0][0].index(content) < render(result, b)[0][0].index("다음 확인")
    with pytest.raises(ValueError, match="editorial_context_scope_rejected"):
        apply_editorial_patch(p, patch, {"fact": ["source-1"]})
    for anchor, identity in [("condition", "new_context"), ("fact", "summary")]:
        bad = deepcopy(change)
        bad["issue_fact_id"], bad["claim"]["id"] = anchor, identity
        with pytest.raises(ValueError, match="editorial_context_scope_rejected"):
            apply_editorial_patch(p, EditorialPatch.model_validate({"context_additions": [bad]}),
                                  {anchor: ["source-2"]})
    bad = deepcopy(change)
    bad["claim"]["text"] = content.replace("250", "260")
    rejected = apply_editorial_patch(p, EditorialPatch.model_validate({"context_additions": [bad]}),
                                    {"fact": ["source-2"]})
    errors = validate(rejected, b)
    assert errors["capex_context"] == "unsupported_prose_number"
    assert prune(rejected, errors).model_dump() == original
    duplicate = deepcopy(change)
    duplicate["claim"]["id"] = "second_context"
    third = deepcopy(change)
    third["claim"]["id"] = "third_context"
    with pytest.raises(ValueError):
        apply_editorial_patch(p, EditorialPatch.model_validate({"context_additions": [change, duplicate, third]}),
                              {"fact": ["source-2"]})


def test_uncertain_revision_is_not_replaced_with_another_model_call(brief):  # noqa: F811
    store, clock = brief
    edition = seed(brief)
    store.commit(response(store.prepare()["request"]))
    v = review().model_dump()
    v.update(verdict="reduce", concerns=["주요 사건의 배경 설명이 부족합니다."])
    v["checks"]["depth"] = False
    store.commit(response(store.prepare()["request"], BriefReview.model_validate(v)))
    request = store.prepare()["request"]
    store.fault(request["request_id"], "uncertain")
    assert store.prepare()["state"] == "idle"
    clock["at"] = edition.due_at+timedelta(minutes=11)
    store.flush()
    with store.db.transaction() as conn:
        calls = conn.execute("SELECT phase,state FROM brief_calls WHERE edition_id=%s", (edition.id,)).fetchall()
    assert len(calls) == 3
    assert next(c for c in calls if c["phase"] == "revise")["state"] == "blocked"


def test_isolated_condition_repair_preserves_facts_and_still_requires_full_review(brief):  # noqa: F811
    store, _ = brief
    edition = seed(brief)
    original = proposal()
    store.commit(response(store.prepare()["request"], original))
    value = review().model_dump()
    value.update(verdict="reduce", rejected_ids=["condition"], concerns=["어느 관측이 현재 해석을 약화하는지 확인 조건을 구체화해야 합니다."])
    value["checks"]["falsifiability"] = False
    failed = BriefReview.model_validate(value)
    store.commit(response(store.prepare()["request"], failed))
    request = store.prepare()["request"]
    payload = json.loads(request["prompt"].split("BRIEF DATA JSON:\n")[1])
    assert payload["revision_feedback"]["repair_mode"] == "conditions_only"
    assert payload["revision_feedback"]["allowed_ids"] == ["condition"]
    replacement = original.issues[0].next_check.model_copy(update={"text": "비기술 업종의 참여가 개선되지 않으면 시장 전반의 회복이라는 해석을 유보합니다."})
    patch = ConditionPatch(replacements=[replacement])
    corrected = apply_condition_patch(original, patch, ["condition"])
    assert corrected.issues[0].fact == original.issues[0].fact
    assert corrected.observations == original.observations
    assert corrected.issues[0].next_check == replacement
    for invalid in [ConditionPatch(replacements=[replacement, replacement]),
                    ConditionPatch(replacements=[replacement.model_copy(update={"id": "fact"})])]:
        with pytest.raises(ValueError, match="condition_patch_scope_rejected"):
            apply_condition_patch(original, invalid, ["condition"])
    store.commit(response(request, patch))
    final = store.prepare()["request"]
    assert final["request_id"].endswith("-final_review") and "main_post_preview" in final["prompt"]
    store.commit(response(final))
    with store.db.transaction() as conn:
        row = conn.execute("SELECT * FROM brief_editions WHERE id=%s", (edition.id,)).fetchone()
    saved = BriefProposal.model_validate(row["proposal"])
    assert saved.model_dump(exclude={"observations"}) == corrected.model_dump(exclude={"observations"})
    assert {item.id: item for item in saved.observations} == {item.id: item for item in corrected.observations}
    assert row["quality"]["revision_used"] and not row["quality"]["reduced"]
    result = evaluate_briefing.assess(bundle(), response(request, patch), response(final),
        previous=response({"request_id": "original-write"}, original),
        correction_review=response({"request_id": "original-review"}, failed))
    assert result["passed"] and result["correction"]["allowed_ids"] == ["condition"]


def test_migration_updates_existing_phase_constraint(company):
    with company.db.transaction() as conn:
        conn.execute("ALTER TABLE brief_calls DROP CONSTRAINT brief_calls_phase_check")
        conn.execute("ALTER TABLE brief_calls ADD CONSTRAINT brief_calls_phase_check CHECK(phase IN ('search','write','review'))")
    company.db.migrate()
    with company.db.transaction() as conn:
        definition = conn.execute("SELECT pg_get_constraintdef(oid) AS value FROM pg_constraint WHERE conrelid='brief_calls'::regclass AND conname='brief_calls_phase_check'").fetchone()["value"]
    assert "final_review" in definition and "revise" in definition


@pytest.mark.parametrize("left,right", [("2.5 million", "250만"), ("5만2048.83", "52048.83"),
    ("1조3천억", "1300000000000"), ("-2.3 billion", "-23억"), ("10 November", "11월 10일"),
    ("$110bn", "1,100억 달러"), ("$17.5m", "1,750만 달러")])
def test_exact_written_quantity_and_month_conversion(left, right):
    assert numbers(left) == numbers(right)
    assert numbers("2.5 million") != numbers("2500만")


def test_attached_dollar_million_does_not_convert_unrelated_m_units():
    assert numbers("$17.5m") == {Decimal(17500000)}
    assert numbers("17.5m") == {Decimal("17.5")}
    assert numbers("$17.5m") != numbers("1,750억 달러")


def test_spoken_market_decimal_and_shared_change_predicate():
    assert numbers("one-thousand-358-point-two won") == {Decimal("1358.2")}
    assert numbers("seven-thousand-17-point-91") == {Decimal("7017.91")}
    assert numbers("22-point-eight") == {Decimal("22.8")}
    claim = "샌디스크는 6.8%, 마이크론은 5% 올랐고 우버는 1.3%, 리프트는 2.4% 내렸습니다."
    quote = "샌디스크는 6.8%, 마이크론은 5% 올랐다. 우버는 1.3%, 리프트는 2.4% 내렸다."
    assert prose_numbers_supported(claim, [quote])
    assert not prose_numbers_supported("우버는 1.3%, 리프트는 2.4% 내렸다.",
                                       ["우버는 1.3%, 리프트는 2.4% 올랐다."])


@pytest.mark.parametrize("verb,sign", [("gained", 1), ("fell", -1)])
def test_spoken_decimal_change_keeps_adjacent_direction_and_explicit_unit(verb, sign):
    quote = f"The KOSDAQ {verb} six-point-11 points, or zero-point-72 percent, to close at 855-point-91."
    assert reported_change_supported(sign*Decimal(".72"), "%", [quote])
    assert reported_change_supported(sign*Decimal("6.11"), "pt", [quote])
    assert not reported_change_supported(-sign*Decimal(".72"), "%", [quote])
    assert not reported_change_supported(sign*Decimal(".72"), "bp", [quote])
    assert not reported_change_supported(sign*Decimal(".73"), "%", [quote])
    assert not reported_change_supported(sign*Decimal(".72"), "%", [
        f"KOSDAQ {verb} six-point-11 points, while KOSPI was flat at zero-point-72 percent."])


def test_spelled_out_counts_and_entity_names_are_not_false_price_errors():
    assert numbers("in the last five days") == numbers("최근 5일")
    assert numbers("twenty-one days and one million downloads") == {Decimal(21), Decimal(1000000)}
    assert numbers("G7, H200 and B200; 256 GPUs") == {Decimal(256)}
    assert numbers("S&P500지수 5300") == numbers("S&P 500은 5300") == {Decimal(5300)}
    assert numbers("스탠더드앤드푸어스(S&P) 500지수 5300") == {Decimal(5300)}
    assert numbers("six days") != numbers("5일")
    assert numbers("two hundred and five") == set()


def test_explicit_month_of_prior_agreement_does_not_need_an_invented_day():
    quote = "Iran offered reopening in seven days if the U.S. returns to the memorandum of understanding from June."
    assert prose_numbers_supported("이란은 6월 양해각서로 복귀하면 7일 안에 열겠다고 제안했다.", [quote])
    assert not prose_numbers_supported("이란은 6월 1일 합의로 복귀하면 7일 안에 열겠다고 제안했다.", [quote])
    assert not prose_numbers_supported("이란은 5월 양해각서로 복귀하면 7일 안에 열겠다고 제안했다.", [quote])


def test_basis_points_are_exact_rate_distance_not_bare_percentage():
    quote = "미 국채 2년물 금리는 4bp 하락한 4.84% 수준이었다."
    assert prose_numbers_supported("미 국채 2년물은 0.04%포인트 내린 4.84%였다.", [quote])
    assert prose_numbers_supported("미 국채 2년물은 4bp 내린 4.84%였다.", [quote])
    assert not prose_numbers_supported("미 국채 2년물은 0.4%포인트 내린 4.84%였다.", [quote])
    assert not prose_numbers_supported("미 국채 2년물은 4% 내린 4.84%였다.", [quote])
    assert numbers("10 basis points") == numbers("0.1%포인트")
    assert numbers("-10bp") == {Decimal("-0.1")}
    assert numbers("1bp는 0.01%포인트") == {Decimal("0.01")}
    assert numbers("10bpm") == {Decimal(10)}
    assert reported_change_supported(Decimal("-4"), "bp", [quote])


def test_hyphenated_fiscal_quarter_preserves_actual_period():
    quote = "Fourth-quarter DRAM revenue increased 343% from a year ago."
    assert prose_numbers_supported("회계연도 4분기 DRAM 매출은 전년 대비 343% 증가했다.", [quote])
    assert not prose_numbers_supported("회계연도 3분기 DRAM 매출은 전년 대비 343% 증가했다.", [quote])


def test_release_label_supports_written_month_without_inventing_a_day():
    quote = "the September U.S. jobs report, due Friday at 8:30 a.m. ET"
    assert prose_numbers_supported("미국 9월 고용보고서", [quote])
    assert not prose_numbers_supported("미국 8월 고용보고서", [quote])
    assert not prose_numbers_supported("미국 9월 1일 고용보고서", [quote])
    assert numbers("officials may report job losses") == set()


def test_legal_section_and_trillion_won_remain_distinct_quantities():
    quote = 'a hearing on "Section 301" tariffs'
    assert prose_numbers_supported("301조 관세의 적법성 심리가 진행됐다.", [quote])
    assert not prose_numbers_supported("122조 관세의 적법성 심리가 진행됐다.", [quote])
    assert not prose_numbers_supported("301조원 규모였다.", [quote])
    assert numbers("301조원") == {Decimal(301000000000000)}


def test_reported_change_uses_local_direction_and_actual_unit():
    quote = "이날 코스닥 지수는 1.89포인트(0.23%) 내린 834.38로 마감했다."
    assert reported_change_supported(Decimal("-.23"), "%", [quote])
    assert reported_change_supported(Decimal("-1.89"), "pt", [quote])
    assert not reported_change_supported(Decimal(".23"), "%", [quote])
    assert not reported_change_supported(Decimal("-.23"), "bp", [quote])
    assert not reported_change_supported(Decimal(".25"), "%", ["금리가 0.25%p 상승했다."])
    assert reported_change_supported(Decimal("1.5"), "%", ["Stocks rose 1.5%."])
    assert not reported_change_supported(Decimal("-1.5"), "%", ["Stocks rose 1.5%, while oil fell 3%."])
    assert reported_change_supported(Decimal("-5"), "bp", ["Yield change -5bp."])
    assert reported_change_supported(Decimal("2.3"), "%", ["599.55포인트(2.3%) 뛴 27,122.09로 마감했다."])


@pytest.mark.parametrize("direction,sign", [("lower", -1), ("higher", 1)])
def test_adjacent_english_comparative_preserves_change_sign_and_unit(direction, sign):
    quote = f"Shares were 0.5% {direction} in mid-morning deals."
    assert reported_change_supported(sign*Decimal(".5"), "%", [quote])
    assert not reported_change_supported(-sign*Decimal(".5"), "%", [quote])
    assert not reported_change_supported(sign*Decimal(".6"), "%", [quote])
    assert not reported_change_supported(sign*Decimal(".5"), "bp", [quote])
    assert not reported_change_supported(sign*Decimal(".5"), "%", [
        f"Shares were at 0.5%, while the margin forecast was {direction}."])
    assert not reported_change_supported(sign*Decimal(".5"), "%", [
        f"The 0.5% {direction}case label describes a chart."])


def test_vw_margin_range_and_adjacent_english_decline_survive_prose_validation():
    quote = ("Shares were 0.5% lower in mid-morning deals, extending Friday's 8.3% decline "
             "after Volkswagen downgraded its expected operating return on sales to 1%, "
             "from a previous forecast of 4% to 5.5%.")
    claim = "폭스바겐은 연간 영업이익률 전망을 4∼5.5%에서 1%로 낮췄고, 주가는 월요일 오전 장중 0.5% 내렸습니다."
    assert prose_numbers_supported(claim, [quote])
    assert not prose_numbers_supported(claim.replace("0.5%", "0.6%"), [quote])
    assert not prose_numbers_supported(claim, [quote.replace("0.5% lower", "0.5% higher")])


def test_english_ranking_only_supports_an_explicit_matching_korean_rank():
    quote = "Moscow's ban removed the second-largest source of diesel from the global market."
    assert prose_numbers_supported("러시아는 세계 2위 경유 공급원이었다.", [quote])
    assert not prose_numbers_supported("러시아는 세계 3위 경유 공급원이었다.", [quote])
    assert not prose_numbers_supported("러시아는 세계 1위 경유 공급원이었다.", [quote])
    assert not prose_numbers_supported("러시아는 세계 1위 경유 공급원이었다.",
                                       [quote.replace("second-largest", "second largest")])
    assert not prose_numbers_supported("러시아는 세계 1위 경유 공급원이었다.",
                                       [quote.replace("second-largest", "fourth largest")])
    assert not prose_numbers_supported("경유는 2달러였다.", [quote])
    assert not prose_numbers_supported("경유는 2% 올랐다.", [quote])
    assert not prose_numbers_supported("세계 2위였다.", ["It was a second-tier supplier with 2 factories."])
    assert prose_numbers_supported("세계 2위 공급원이었다.", ["세계 2위 경유 공급원이다."])


def test_named_meeting_month_is_translatable_without_inventing_a_date_or_quantity():
    quote = "The minutes from its September meeting will be released on Wednesday."
    assert prose_numbers_supported("9월 회의 의사록은 수요일 공개 예정이다.", [quote])
    assert not prose_numbers_supported("8월 회의 의사록은 수요일 공개 예정이다.", [quote])
    assert not prose_numbers_supported("9월 7일 회의 의사록", [quote])
    assert not prose_numbers_supported("회의에서 9% 인상을 결정했다.", [quote])
    assert not prose_numbers_supported("5월 회의", ["The committee may meet on Wednesday."])


def test_terse_decline_matches_a_signed_row_and_never_an_advance():
    assert prose_numbers_supported("코스피 0.89% 하락, 코스닥 2.98% 상승",
                                   ["코스피 -0.89%", "코스닥 +2.98%"])
    assert not prose_numbers_supported("코스피 0.89% 하락",
                                       ["코스피 +0.89%"])
    assert prose_numbers_supported("코스피 0.89% 하락",
                                   ["The index slid 62.35 points, or 0.89 percent."])
    assert not prose_numbers_supported("코스피 0.89% 하락",
                                       ["The index gained 62.35 points, or 0.89 percent."])


def test_crypto_rolling_window_uses_korean_reporting_date_not_us_equity_session():
    p, b = proposal(), bundle()
    b["documents"][0]["content"] += " 비트코인 22일 오전 6시 28분 현재 100달러, 24시간 대비 2% 상승."
    from quant_company.briefing.contracts import MarketObservation

    p.observations = [MarketObservation(id="btc", instrument="btc", value=100, unit="USD",
        session_date="2026-09-22", as_of="2026-09-21T21:28:00Z", basis="rolling_24h",
        venue="Synthetic consolidated feed", reported_change=2, change_unit="%",
        evidence=[{"source_id": "source-1", "quote": "비트코인 22일 오전 6시 28분 현재 100달러, 24시간 대비 2% 상승."}])]
    assert validate(p, b) == {}
