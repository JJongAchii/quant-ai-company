"""Source choice and main-post coverage at the real PostgreSQL editorial boundary."""

import json
from copy import deepcopy
from datetime import timedelta
from html import escape
from pathlib import Path

import pytest

from quant_company.briefing.contracts import (
    BriefProposal,
    BriefReview,
    CalendarEvent,
    EditorialPatch,
    SourcePlan,
    item_map,
)
from quant_company.briefing.editor import prompt, render, revision_bundle, validate, validate_review
from quant_company.briefing.inputs import document
from quant_company.briefing.planning import apply_plan, discovery_spans, plan_prompt, required_sources
from quant_company.briefing.qualification import replay
from quant_company.briefing.runner import BriefEditor
from quant_company.briefing.store import priority_pending

from .test_briefing import CONTENT, brief, bundle, definition, proposal, response, review, seed  # noqa: F401
from .test_briefing_content import doc


def planning_bundle():
    data = bundle()
    data["candidate_documents"] = [*deepcopy(data["documents"]),
                                   doc("opposing", "Iran rejects proposed talks").model_dump(mode="json")]
    return data


def plan(*ids):
    return SourcePlan(selections=[{"source_id": i, "reason": "시장 흐름을 바꾸는 별도 전개와 반대 근거를 확인"} for i in ids],
                      priorities=["협상 기대와 거절 보도가 어떤 시점에 해당하는가?"])


def test_complete_original_tail_is_preserved_and_partial_original_is_rejected():
    edition = definition()
    text = "Market background. "*340 + "The proposed talks were subsequently rejected."
    receipt = {"ok": True, "url": "https://example.org/source", "content": text,
               "original_sha256": "a"*64, "retrieved_at": edition.cutoff.isoformat(),
               "excerpt_truncated": False, "article_chars": len(text)}
    result = document(receipt, "fixture", "fixture", "media")
    assert result.content == text and len(text) > 6000
    data = bundle()
    data["documents"] = [result.model_dump(mode="json")]
    assert "subsequently rejected" in prompt(data, "write")
    assert document({**receipt, "excerpt_truncated": True}, "fixture", "fixture", "media") is None


def test_plan_only_selects_frozen_ids_and_cannot_drop_required_close():
    data = planning_bundle()
    data["candidate_documents"][0]["title"] = "S&P 500 market close"
    assert required_sources(data) == ["source-1"]
    with pytest.raises(ValueError, match="unknown_or_duplicate"):
        apply_plan(data, plan("invented"))
    with pytest.raises(ValueError, match="unknown_or_duplicate"):
        apply_plan(data, plan("source-1", "source-1"))
    with pytest.raises(ValueError, match="missing_session_reports"):
        apply_plan(data, plan("opposing"))
    selected = apply_plan(data, plan("source-1", "opposing"))
    assert [d["id"] for d in selected["documents"]] == ["source-1", "opposing"]
    assert data["documents"] == bundle()["documents"]
    assert "candidate_documents" not in prompt(selected, "write")


def test_plan_rejects_late_sources_and_source_budget_overflow():
    data = planning_bundle()
    data["candidate_documents"][1]["retrieved_at"] = (definition().cutoff+timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="after_cutoff"):
        apply_plan(data, plan("source-1", "opposing"))
    data["candidate_documents"] = [doc(f"s{i}", f"Policy decision {i}", content="x"*6000).model_dump(mode="json")
                                   for i in range(8)]
    with pytest.raises(ValueError, match="source_budget"):
        apply_plan(data, plan(*(d["id"] for d in data["candidate_documents"])))


def test_plan_context_is_bounded_and_does_not_send_full_candidate_corpus():
    data = planning_bundle()
    data["candidate_documents"] = [doc(f"s{i}", f"Iran policy decision {i}", content="x"*6000).model_dump(mode="json")
                                   for i in range(96)]
    request = plan_prompt(data)
    assert len(request) < 88000 and "x"*401 not in request
    assert len(json.loads(request.split("CANDIDATES:\n")[1])["candidates"]) == 96


def test_source_discovery_exposes_middle_counterevidence_and_body_tail_before_footer():
    opposing = "However, existing inventory can absorb the disruption for two weeks."
    tail = "The policymaker's baseline assumes energy supply improves later, with considerable uncertainty."
    text = ("New investment announcement.\n"*35 + opposing + "\n" + "Operational background.\n"*40
            + tail + "\nChoose CNBC as your preferred source on Google and never miss a moment.\n")
    spans = discovery_spans(text)
    assert all(excerpt == text[start:end] for start, end, excerpt in spans)
    assert sum(len(excerpt) for _, _, excerpt in spans) <= 520
    assert opposing in "\n".join(excerpt for _, _, excerpt in spans)
    assert tail in "\n".join(excerpt for _, _, excerpt in spans)
    assert all("Choose CNBC" not in excerpt for _, _, excerpt in spans)


def test_source_discovery_keeps_korean_exception_and_full_writer_original():
    data = planning_bundle()
    text = ("원유 공급 차질과 운송비 부담이 커질 수 있다는 발표 내용입니다.\n"*18
            + "다만 이미 대체 항로를 이용하고 있어 당장의 공급 감소로 확정할 수 없습니다.\n"
            + "시장 전반의 수요를 확인하려면 서로 다른 업종의 참여도와 비용 변화를 살펴야 합니다.\n"*12
            + "일부 기업은 요건을 충족하면 코넥스로 이전할 수 있어 곧바로 퇴출되는 것은 아닙니다.\n"
            + "<저작권자(c) 연합뉴스, 무단 전재-재배포, AI 학습 및 활용 금지>\n공유하기\n")
    data["candidate_documents"][1]["content"] = text
    original = deepcopy(data)
    payload = json.loads(plan_prompt(data).split("CANDIDATES:\n")[1])
    candidate = payload["candidates"][1]
    excerpts = "\n".join(part[2] for part in candidate["discovery_spans"])
    assert "이미 대체 항로" in excerpts and "코넥스로 이전" in excerpts
    assert "저작권자" not in excerpts and not candidate["discovery_complete"]
    assert data == original
    assert apply_plan(data, plan("source-1", "opposing"))["documents"][1]["content"] == text


def test_source_discovery_shrinks_only_samples_for_long_metadata_without_losing_candidates():
    data = planning_bundle()
    data["candidate_documents"] = [doc(f"s{i}", "정책 발표와 공급 여건 "*30,
        content="Reported facts.\n"*100 + "However, the funding still requires approval.\n" + "Background.\n"*100
                + "The final implementation date remains conditional and has not been agreed.").model_dump(mode="json")
        for i in range(96)]
    for source in data["candidate_documents"]:
        source.update(publisher="Publisher"*20, origin_group="Origin"*30, id=source["id"]+"a"*60)
    before = deepcopy(data)
    raw = plan_prompt(data)
    rows = json.loads(raw.split("CANDIDATES:\n")[1])["candidates"]
    assert len(raw) <= 88000 and len(rows) == 96 and data == before
    for row, source in zip(rows, data["candidate_documents"], strict=True):
        assert row["id"] == source["id"] and row["source_chars"] == len(source["content"])
        assert not row["discovery_complete"]
        assert all(part[2] == source["content"][part[0]:part[1]] for part in row["discovery_spans"])
        assert "funding still requires approval" in "\n".join(part[2] for part in row["discovery_spans"])


def test_material_fact_must_be_visible_not_only_in_thread_or_a_citation():
    p, data = proposal(), bundle()
    p.calendar = [CalendarEvent(id="release", title="경제지표 발표", at=None,
        source_timezone="미국 현지", status="time_unconfirmed", evidence=p.watchpoints[0].evidence)]
    p.watchpoints += [p.watchpoints[0].model_copy(update={"id": identity}) for identity in ("watch2", "watch3")]
    original = review().model_dump()
    original["source_assessments"][0]["material_facts"][0]["main_item_ids"] = ["watch3"]
    with pytest.raises(ValueError, match="not_in_main_post"):
        validate_review(BriefReview.model_validate(original), p, data)
    original["source_assessments"][0]["material_facts"][0]["main_item_ids"] = []
    with pytest.raises(ValueError, match="missing_fact_cannot_pass"):
        validate_review(BriefReview.model_validate(original), proposal(), bundle())
    original.update(verdict="reduce", concerns=["중요한 반대 근거가 본문에서 빠졌습니다."])
    original["checks"]["coverage"] = False
    critique = BriefReview.model_validate(original)
    validate_review(critique, proposal(), bundle())
    feedback = revision_bundle(bundle(), proposal().model_dump(mode="json"), critique, {})["revision_feedback"]
    assert feedback["source_assessments"][0]["material_facts"][0]["quote"] in CONTENT


def test_material_fact_requires_real_original_and_cannot_be_silently_unchecked():
    original = review().model_dump()
    original["source_assessments"][0]["material_facts"][0]["quote"] = "Invented official denial of the talks."
    with pytest.raises(ValueError, match="fact_not_in_original"):
        validate_review(BriefReview.model_validate(original), proposal(), bundle())
    original["source_assessments"][0]["material_facts"] = []
    with pytest.raises(ValueError, match="material_facts_required"):
        validate_review(BriefReview.model_validate(original), proposal(), bundle())


def test_negative_review_can_describe_a_rejected_paragraph_without_crediting_coverage():
    original = review().model_dump()
    original["source_assessments"][0]["material_facts"][0]["main_item_ids"] = ["fact"]
    original["rejected_ids"] = ["fact"]
    with pytest.raises(ValueError, match="missing_fact_cannot_pass"):
        validate_review(BriefReview.model_validate(original), proposal(), bundle())
    original.update(verdict="reduce", concerns=["문단의 숫자는 제외하고 별도로 근거가 있는 사실을 복구해야 합니다."])
    original["checks"]["coverage"] = False
    critique = BriefReview.model_validate(original)
    validate_review(critique, proposal(), bundle())
    feedback = revision_bundle(bundle(), proposal().model_dump(mode="json"), critique,
                               {"fact": "semantic_review"})["revision_feedback"]
    assert feedback["source_assessments"][0]["material_facts"][0]["main_item_ids"] == ["fact"]
    data = {**bundle(), "revision_feedback": feedback}
    before = deepcopy(data)
    payload = json.loads(prompt(data, "revise").split("BRIEF DATA JSON:\n")[1])
    key = payload["revision_feedback"]["previous_draft"]["summary"][0]["evidence"][0]
    assert payload["previous_draft_sources"][key] == "source-1"
    assert payload["documents"][0]["content"] == data["documents"][0]["content"]
    assert data == before


def test_review_compression_preserves_exact_quotes_and_raw_proposal():
    p = proposal().model_dump(mode="json")
    before = deepcopy(p)
    request = prompt(bundle(), "review", p)
    payload = json.loads(request.split("BRIEF DATA JSON:\n")[1])
    ref = payload["proposal"]["summary"][0]["evidence"][0]
    assert payload["evidence_quotes"][ref] == [0, CONTENT]
    parts = payload["documents"][0]["content_parts"]
    reconstructed = "".join(p if isinstance(p, str) else payload["evidence_quotes"][p["quote_ref"]][1] for p in parts)
    assert reconstructed == bundle()["documents"][0]["content"]
    items = item_map(proposal())
    main = "".join(part if isinstance(part, str) else escape(items[part["item_text"]].text, quote=False)
                   for part in payload["main_post_preview"])
    assert main == render(proposal(), bundle())[0][0]
    assert "view" in payload["main_post_item_ids"] and "fact" in payload["main_post_item_ids"]
    assert p == before
    schema = json.loads(request.split("SCHEMA:\n")[1].split("\nBRIEF DATA JSON:\n")[0])
    assert len(schema["$defs"]["ReviewChecks"]["required"]) == 12
    assert schema["$defs"]["FactAssessment"]["properties"]["quote"]["minLength"] == 10
    procedure = json.loads(request.split("SPECIALIST PROCEDURE JSON:\n")[1].split("\nINSTRUMENTS:\n")[0])
    assert procedure["employee"] == "market_brief" and procedure["digest"]
    assert "procedure" not in procedure


def test_repeated_visible_claims_compact_without_losing_text_or_actual_item_ids():
    p, data = proposal(), bundle()
    p.summary[0].text = p.issues[0].interpretation.text
    request = prompt(data, "review", p.model_dump(mode="json"))
    payload = json.loads(request.split("BRIEF DATA JSON:\n")[1])
    items = item_map(p)
    main = "".join(part if isinstance(part, str) else escape(items[part["item_text"]].text, quote=False)
                   for part in payload["main_post_preview"])
    assert main == render(p, data)[0][0]
    assert {"view", "summary"} <= set(payload["main_post_item_ids"])
    assert sum(isinstance(part, dict) and items[part["item_text"]].text == p.summary[0].text
               for part in payload["main_post_preview"]) == 2


def test_unknown_event_time_does_not_require_an_invented_iana_timezone():
    p = proposal()
    p.calendar = [CalendarEvent(id="release", title="경제지표 발표", at=None,
        source_timezone="미국 현지·세부 시간대 미명시", status="time_unconfirmed",
        note="원문상 발표 예정이며 정확한 시각은 확인되지 않았습니다.",
        evidence=[{"source_id": "source-1", "quote": CONTENT}])]
    assert "release" not in validate(p, bundle())
    assert "시각 미확인" in render(p, bundle())[0][0]
    p.calendar[0].at = definition().cutoff+timedelta(hours=1)
    p.calendar[0].status = "scheduled"
    assert "release" in validate(p, bundle())


def test_expanded_real_source_packet_fits_and_keeps_the_third_calendar_in_main():
    saved = Path(__file__).resolve().parents[1]/"docs/project/evidence/briefing-pipeline-20260929/development-v19-repaired/assessment.json"
    data = json.loads(saved.read_text())
    request = prompt(data["bundle"], "review", data["proposal"])
    payload = json.loads(request.split("BRIEF DATA JSON:\n")[1])
    assert len(request) <= 88000 and len(payload["documents"]) == 20
    assert "pce" in payload["main_post_item_ids"]
    assert "PCE" in render(BriefProposal.model_validate(data["proposal"]), data["bundle"])[0][0]


def test_reviewer_keeps_all_used_instrument_definitions_and_writer_keeps_catalog():
    p = proposal()
    reviewed = prompt(bundle(), "review", p.model_dump(mode="json"))
    definitions = json.loads(reviewed.split("INSTRUMENTS:\n")[1].split("\nSCHEMA:\n")[0])
    assert set(definitions) == {o.instrument for o in p.observations}
    assert definitions["sp500"] == ["S&P 500", "pt", "US"]
    written = prompt(bundle(), "write")
    catalog = json.loads(written.split("INSTRUMENTS:\n")[1].split("\nSCHEMA:\n")[0])
    assert "btc" in catalog and "kospi" in catalog


def test_reviewer_can_request_only_frozen_omitted_originals_and_cannot_publish_them_unread():
    data = planning_bundle()
    raw = review().model_dump()
    raw["source_requests"] = [{"source_id": "opposing", "reason": "검토 목록에서 협상 부인이라는 별도 중요 전개를 발견했습니다."}]
    with pytest.raises(ValueError, match="unread_source_cannot_pass"):
        validate_review(BriefReview.model_validate(raw), proposal(), data)
    raw.update(verdict="reduce", concerns=["빠진 원문을 읽고 반대 근거를 확인해야 합니다."])
    raw["checks"]["coverage"] = False
    critique = BriefReview.model_validate(raw)
    validate_review(critique, proposal(), data)
    repaired = revision_bundle(data, proposal().model_dump(mode="json"), critique, {})
    assert [d["id"] for d in repaired["documents"]] == ["source-1", "opposing"]
    assert repaired["documents"][-1] == data["candidate_documents"][-1]
    assert len(data["documents"]) == 1
    payload = json.loads(prompt(data, "review", proposal().model_dump(mode="json")).split("BRIEF DATA JSON:\n")[1])
    assert payload["unselected_source_index"][0][0] == "opposing"
    raw["source_requests"][0]["source_id"] = "not-frozen"
    with pytest.raises(ValueError, match="unknown_or_duplicate_source"):
        validate_review(BriefReview.model_validate(raw), proposal(), data)


def test_supplemented_sources_replay_with_the_original_plan_and_exact_frozen_content():
    data = apply_plan(planning_bundle(), plan("source-1"))
    raw = review().model_dump()
    raw.update(verdict="reduce", concerns=["협상 부인 원문을 추가해 누락을 확인해야 합니다."],
               source_requests=[{"source_id": "opposing", "reason": "협상 부인이라는 별도 중요 전개를 확인해야 합니다."}])
    raw["checks"]["coverage"] = False
    repaired = revision_bundle(data, proposal().model_dump(mode="json"), BriefReview.model_validate(raw), {})
    assert apply_plan(repaired, plan("source-1"))["documents"] == repaired["documents"]


def test_real_store_freezes_selection_then_writes_without_recollecting(brief):  # noqa: F811
    store, clock = brief
    edition = definition()
    clock["at"] = edition.cutoff-timedelta(minutes=1)
    store.register()
    claimed = store.claim_collection()
    data = planning_bundle()
    store.save_collection(claimed, data)
    clock["at"] = edition.cutoff
    first = store.prepare()["request"]
    assert first["request_id"].endswith("-plan")
    assert priority_pending(store.company)
    assert store.prepare()["request"] == first
    receipt = response(first, plan("source-1"))
    assert store.commit(receipt)["phase"] == "plan"
    assert store.commit(receipt)["duplicate"]
    writer = store.prepare()["request"]
    assert writer["request_id"].endswith("-write") and "candidate_documents" not in writer["prompt"]
    store.commit(response(writer))
    store.commit(response(store.prepare()["request"], review()))
    clock["at"] = edition.due_at
    store.flush()
    with store.db.transaction() as conn:
        saved = conn.execute("SELECT * FROM brief_editions WHERE id=%s", (edition.id,)).fetchone()
        calls = conn.execute("SELECT phase FROM brief_calls WHERE edition_id=%s", (edition.id,)).fetchall()
    assert {c["phase"] for c in calls} == {"plan", "write", "review"}
    assert saved["bundle"]["candidate_documents"] == data["candidate_documents"]
    assert replay(saved)["ok"]


def test_real_store_repair_reads_omitted_original_and_reviews_the_expanded_bundle(brief):  # noqa: F811
    from psycopg.types.json import Jsonb

    store, clock = brief
    edition = seed(brief)
    data = planning_bundle()
    denial = "The official denied that any direct talks had been scheduled."
    data["candidate_documents"][1]["content"] = denial
    with store.db.transaction() as conn:
        conn.execute("UPDATE brief_editions SET bundle=%s", (Jsonb(data),))
    store.commit(response(store.prepare()["request"], plan("source-1")))
    original_request = store.prepare()["request"]
    assert denial not in original_request["prompt"]
    store.commit(response(original_request))
    critique = review().model_dump()
    critique.update(verdict="reduce", concerns=["협상 부인 원문을 읽고 반대 근거를 추가해야 합니다."],
                    source_requests=[{"source_id": "opposing", "reason": "별도 협상 부인 보도가 기존 이야기와 충돌할 수 있습니다."}])
    critique["checks"]["coverage"] = False
    store.commit(response(store.prepare()["request"], BriefReview.model_validate(critique)))
    repair = store.prepare()["request"]
    assert repair["request_id"].endswith("-revise") and denial in repair["prompt"]
    revised = proposal().issues[0].interpretation
    text = revised.text+" 당국자는 직접 회담 일정이 잡혔다는 주장을 부인했습니다."
    patch = EditorialPatch.model_validate({"edits": [{"id": revised.id, "text": text,
        "evidence": [{"source_id": "opposing", "quote": denial}]}]})
    store.commit(response(repair, patch))
    final = review().model_dump()
    final["source_assessments"].append({"source_id": "opposing", "treatment": "covered", "reason": "추가 원문의 협상 부인이 본문 반대 근거에 반영됨",
        "item_ids": [revised.id], "material_facts": [{"fact": "당국자가 직접 회담 일정을 부인함", "quote": denial, "main_item_ids": [revised.id]}]})
    store.commit(response(store.prepare()["request"], BriefReview.model_validate(final)))
    clock["at"] = edition.due_at
    store.flush()
    with store.db.transaction() as conn:
        row = conn.execute("SELECT * FROM brief_editions WHERE id=%s", (edition.id,)).fetchone()
        first = conn.execute("SELECT request FROM brief_calls WHERE phase='write'").fetchone()["request"]
    assert first == original_request
    assert row["quality"]["revision_used"] and not row["quality"]["reduced"]
    assert text in row["rendered"][0]
    assert replay(row)["ok"]
    from scripts import evaluate_briefing
    offline = evaluate_briefing.assess(data, response(repair, patch), response({"request_id": "final-review"}, BriefReview.model_validate(final)),
        previous=response(original_request), correction_review=response({"request_id": "review"}, BriefReview.model_validate(critique)))
    assert offline["passed"] and {d["id"] for d in offline["bundle"]["documents"]} == {"source-1", "opposing"}


@pytest.mark.asyncio
async def test_invalid_plan_is_blocked_before_writer_or_publication(brief):  # noqa: F811
    store, clock = brief
    seed(brief)
    with store.db.transaction() as conn:
        from psycopg.types.json import Jsonb
        conn.execute("UPDATE brief_editions SET bundle=%s", (Jsonb(planning_bundle()),))

    class InvalidPlanner:
        async def run(self, request):
            return response(request.model_dump(), plan("invented"))

    assert (await BriefEditor(store.company, InvalidPlanner()).tick())["state"] == "blocked"
    with store.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM brief_calls WHERE phase='write'").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM brief_messages").fetchone()["n"] == 0
