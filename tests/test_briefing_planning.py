"""Source choice and main-post coverage at the real PostgreSQL editorial boundary."""

import json
from copy import deepcopy
from datetime import timedelta

import pytest

from quant_company.briefing.contracts import BriefReview, Claim, SourcePlan
from quant_company.briefing.editor import prompt, revision_bundle, validate_review
from quant_company.briefing.inputs import document
from quant_company.briefing.planning import apply_plan, plan_prompt, required_sources
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


def test_original_tail_is_preserved_and_upstream_truncation_is_visible():
    edition = definition()
    text = "Market background. "*180 + "The proposed talks were subsequently rejected."
    result = document({"ok": True, "url": "https://example.org/source", "content": text,
                       "original_sha256": "a"*64, "retrieved_at": edition.cutoff.isoformat(),
                       "excerpt_truncated": True}, "fixture", "fixture", "media")
    assert result.content == text and len(text) > 3000
    data = bundle()
    data["documents"] = [result.model_dump(mode="json")]
    assert "subsequently rejected" in prompt(data, "write")
    assert '"excerpt_truncated":true' in prompt(data, "write")


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


def test_material_fact_must_be_visible_not_only_in_thread_or_a_citation():
    original = review().model_dump()
    original["source_assessments"][0]["material_facts"][0]["main_item_ids"] = ["view"]
    with pytest.raises(ValueError, match="not_in_main_post"):
        validate_review(BriefReview.model_validate(original), proposal(), bundle())
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


def test_review_compression_preserves_exact_quotes_and_raw_proposal():
    p = proposal().model_dump(mode="json")
    before = deepcopy(p)
    request = prompt(bundle(), "review", p)
    payload = json.loads(request.split("BRIEF DATA JSON:\n")[1])
    ref = payload["proposal"]["summary"][0]["evidence"][0]
    assert payload["evidence_quotes"][ref] == {"source_id": "source-1", "quote": CONTENT}
    parts = payload["documents"][0]["content_parts"]
    reconstructed = "".join(p if isinstance(p, str) else payload["evidence_quotes"][p["quote_ref"]]["quote"] for p in parts)
    assert reconstructed == bundle()["documents"][0]["content"]
    assert "view" not in payload["main_post_item_ids"] and "fact" in payload["main_post_item_ids"]
    assert p == before


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
    revised = proposal()
    revised.issues[0].counterpoint = Claim(id="denial", text="당국자는 직접 회담 일정이 잡혔다는 주장을 부인했습니다.",
                                         evidence=[{"source_id": "opposing", "quote": denial}])
    store.commit(response(repair, revised))
    final = review().model_dump()
    final["source_assessments"].append({"source_id": "opposing", "treatment": "covered", "reason": "추가 원문의 협상 부인이 본문 반대 근거에 반영됨",
        "item_ids": ["denial"], "material_facts": [{"fact": "당국자가 직접 회담 일정을 부인함", "quote": denial, "main_item_ids": ["denial"]}]})
    store.commit(response(store.prepare()["request"], BriefReview.model_validate(final)))
    clock["at"] = edition.due_at
    store.flush()
    with store.db.transaction() as conn:
        row = conn.execute("SELECT * FROM brief_editions WHERE id=%s", (edition.id,)).fetchone()
        first = conn.execute("SELECT request FROM brief_calls WHERE phase='write'").fetchone()["request"]
    assert first == original_request
    assert row["quality"]["revision_used"] and not row["quality"]["reduced"]
    assert revised.issues[0].counterpoint.text in row["rendered"][0]
    assert replay(row)["ok"]


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
