"""Repair retries (owner rule, 2026-10-12): the retry carries the rejected draft and fixes only what the check named."""

import json

import pytest
from pydantic import ValidationError

from quant_company.briefing.contracts import BriefReview
from quant_company.briefing.repair import PROMPT_LIMIT, failure, repair_prompt, retry_note
from quant_company.contracts import AgentDecision, ProviderResponse

from .test_briefing import brief  # noqa: F401
from .test_briefing_inventory import composition, frozen


def answer(content):
    return ProviderResponse(request_id="news-brief-1", provider="fixture", decision=AgentDecision(
        status="complete", say="", artifacts=[{"title": "brief", "content": content, "source_ids": []}]))


def test_a_placement_failure_is_repaired_from_the_draft_with_the_missing_facts():
    """2026-10-12 PM: the write draft left committed facts without placements and the edition fell back to facts."""
    bundle = frozen()
    bad = composition()
    bad.fact_placements = []
    draft = bad.model_dump_json()
    text = repair_prompt(bundle, "write", draft, "inventory_placement_accounting")
    detail = json.loads(text.split("DETAIL: ", 1)[1].split("\n", 1)[0])
    assert detail["missing"] == ["f1"] and detail["all_fact_ids"] == ["f1"] and not detail["repeated"]
    assert detail["missing_facts"][0]["quote"].startswith("Semiconductor shares led the advance")
    assert detail["missing_facts"][0]["qualifiers"] == ["기술주 밖의 참여는 혼조"]
    assert "Change only what the check requires" in text and text.endswith("PREVIOUS_DRAFT:\n" + draft)


def test_only_draft_local_failures_that_fit_get_a_repair_prompt():
    bundle, draft = frozen(), composition().model_dump_json()
    assert repair_prompt(bundle, "write", draft, "brief_plan_source_budget") is None  # needs the full context
    assert repair_prompt(bundle, "write", None, "inventory_placement_accounting") is None
    assert repair_prompt(bundle, "write", "{" + "x" * PROMPT_LIMIT + "}", "brief_output_schema") is None
    assert "brief_plan_source_budget" in retry_note("brief_plan_source_budget")


def test_failure_keeps_fixed_labels_schema_locations_and_the_models_own_draft():
    label, draft, detail = failure(ValueError("inventory_placement_accounting"), answer('{"a": 1}'))
    assert (label, draft, detail) == ("inventory_placement_accounting", '{"a": 1}', None)
    assert failure(ValueError("free text from somewhere"), None) == (None, None, None)
    with pytest.raises(ValidationError) as schema:
        BriefReview.model_validate({"verdict": "publish"})
    label, draft, detail = failure(schema.value, answer("{}"))
    assert label == "brief_output_schema" and draft == "{}"
    assert {"loc": "checks", "type": "missing"} in detail and all(set(d) == {"loc", "type"} for d in detail)


def test_a_composed_revision_sends_the_fact_placements_not_the_restorable_source_notes():
    """2026-10-12 PM: the notes went out twice (about 40K characters) and every full revision exceeded the limit."""
    from quant_company.briefing.editor import revision_bundle
    from quant_company.briefing.inventory import compose

    from .test_briefing import review

    bundle = frozen()
    draft = compose(composition(), bundle).model_dump(mode="json")
    value = review().model_dump()
    value.update(verdict="reduce", concerns=["가독성: 반복 문장을 줄여야 합니다."])
    value["checks"]["readability"] = False
    feedback = revision_bundle(bundle, draft, BriefReview.model_validate(value), {})["revision_feedback"]
    assert feedback["source_notes"] == [] and "source_notes" not in feedback["previous_draft"]
    assert feedback["previous_draft"]["fact_placements"] == [{"fact_id": "f1", "main_item_ids": ["fact"]}]


def test_a_full_revision_over_the_input_limit_becomes_a_patch_of_the_reviewed_problems(brief, monkeypatch):  # noqa: F811
    """Owner rule (2026-10-12): fix only what is wrong. The server-rejected item stays out; the review is patched."""
    from quant_company.briefing import store as store_module

    from .test_briefing import response, review, seed

    store, _ = brief
    edition = seed(brief)
    store.commit(response(store.prepare()["request"]))
    real = store_module.prompt

    def limited(bundle, phase, *args, **kwargs):
        if phase == "revise" and bundle["revision_feedback"]["repair_mode"] == "full_proposal":
            raise ValueError("brief_context_limit")
        return real(bundle, phase, *args, **kwargs)

    monkeypatch.setattr(store_module, "prompt", limited)
    value = review().model_dump()
    value.update(verdict="reduce", rejected_ids=["sp500"], concerns=["반복 문장이 핵심 변화를 가립니다."])
    value["checks"]["readability"] = False
    store.commit(response(store.prepare()["request"], BriefReview.model_validate(value)))
    with store.db.transaction() as conn:
        row = conn.execute("SELECT state,bundle,quality FROM brief_editions WHERE id=%s", (edition.id,)).fetchone()
    assert row["state"] == "revising" and row["quality"]["repair_patch_fallback"] == "brief_context_limit"
    assert row["bundle"]["revision_feedback"]["repair_mode"] == "editorial_patch"
    assert "sp500" in row["bundle"]["revision_feedback"]["rejected"]
    revision = store.prepare()["request"]
    assert revision["request_id"].endswith("-revise") and revision["output_contract"].startswith("brief_editorial_v")
