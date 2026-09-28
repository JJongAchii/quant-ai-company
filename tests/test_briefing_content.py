import json
import sys
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

import pytest

from quant_company.briefing.contracts import (
    BriefProposal,
    BriefReview,
    CalendarEvent,
    ConditionPatch,
    SourceDocument,
)
from quant_company.briefing.coverage import inventory, select_documents, topics
from quant_company.briefing.editor import (
    apply_condition_patch,
    artifact,
    prune,
    render,
    validate,
    validate_review,
)
from quant_company.briefing.inputs import market_report
from quant_company.briefing.numeric import numbers, prose_numbers_supported, reported_change_supported
from quant_company.briefing.qualification import qualify
from quant_company.config import Settings
from quant_company.contracts import ProviderFault, ProviderRequest
from scripts import evaluate_briefing

from .test_briefing import brief, bundle, definition, proposal, response, review, seed  # noqa: F401
from .test_briefing_analyst import analytical_proposal


def doc(identity, title, **updates):
    value = deepcopy(bundle()["documents"][0])
    value.update(id=identity, title=title, sha256=identity, publisher=identity, origin_group=identity,
                 url="https://example.org/"+identity, **updates)
    return SourceDocument.model_validate(value)


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


def test_month_named_in_source_can_support_its_exact_korean_month_without_modal_false_positive():
    assert Decimal(7) in numbers("Saudi crude accounted for 34.1% of imports in July.")
    assert Decimal(5) not in numbers("The committee may change its policy.")


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


def test_main_checkpoints_are_short_and_unknown_time_is_labelled_once():
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
    assert "행사 3" not in main and "행사 4" not in main and "시장 조건 3" not in main
    assert all(value in detail for value in ("행사 3", "행사 4", "시장 조건 3"))
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
                                   "reason": "이 기사를 인용했다고 잘못 주장한 검토", "item_ids": ["summary"]})
    with pytest.raises(ValueError, match="review_coverage_not_cited"):
        validate_review(BriefReview.model_validate(v), p, b)


def test_missing_major_issue_preserves_useful_partial_but_cannot_pass(brief):  # noqa: F811
    store, clock = brief
    edition = seed(brief)
    store.commit(response(store.prepare()["request"], analytical_proposal()))
    clock["at"] = edition.due_at
    verdict = review().model_dump()
    verdict.update(verdict="reduce", concerns=["원문에 있는 중요한 정책 변화의 설명이 빠졌습니다."])
    verdict["checks"]["coverage"] = False
    store.commit(response(store.prepare()["request"], BriefReview.model_validate(verdict)))
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
    assert payload["documents"][0]["content"] == bundle()["documents"][0]["content"]
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
    ("$110bn", "1,100억 달러")])
def test_exact_written_quantity_and_month_conversion(left, right):
    assert numbers(left) == numbers(right)
    assert numbers("2.5 million") != numbers("2500만")


def test_spelled_out_counts_and_entity_names_are_not_false_price_errors():
    assert numbers("in the last five days") == numbers("최근 5일")
    assert numbers("twenty-one days and one million downloads") == {Decimal(21), Decimal(1000000)}
    assert numbers("G7, H200 and B200; 256 GPUs") == {Decimal(256)}
    assert numbers("S&P500지수 5300") == numbers("S&P 500은 5300") == {Decimal(5300)}
    assert numbers("스탠더드앤드푸어스(S&P) 500지수 5300") == {Decimal(5300)}
    assert numbers("six days") != numbers("5일")
    assert numbers("two hundred and five") == set()


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


def test_crypto_rolling_window_uses_korean_reporting_date_not_us_equity_session():
    p, b = proposal(), bundle()
    b["documents"][0]["content"] += " 비트코인 22일 오전 6시 28분 현재 100달러, 24시간 대비 2% 상승."
    from quant_company.briefing.contracts import MarketObservation

    p.observations = [MarketObservation(id="btc", instrument="btc", value=100, unit="USD",
        session_date="2026-09-22", as_of="2026-09-21T21:28:00Z", basis="rolling_24h",
        venue="Synthetic consolidated feed", reported_change=2, change_unit="%",
        evidence=[{"source_id": "source-1", "quote": "비트코인 22일 오전 6시 28분 현재 100달러, 24시간 대비 2% 상승."}])]
    assert validate(p, b) == {}
