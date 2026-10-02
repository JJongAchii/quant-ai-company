import json
from copy import deepcopy

import pytest

from quant_company.briefing.contracts import BriefProposal, BriefReview
from quant_company.briefing.editor import artifact, prompt, validate, validate_review
from quant_company.briefing.quotations import (
    quotation_index,
    reference_payload,
    resolve_quotations,
    source_spans,
)

from .test_briefing import CONTENT, brief, bundle, proposal, response, review, seed  # noqa: F401


def referenced_bundle():
    return {**bundle(), "quote_reference_version": 1}


def test_complete_original_reconstruction_and_input_bound_references():
    b = referenced_bundle()
    original = "Full original.\n"*730 + "Exact final tail."
    b["documents"][0]["content"] = original
    raw = prompt(b, "write")
    data = json.loads(raw.split("BRIEF DATA JSON:\n")[1])
    reconstructed = "".join(data["original_quotes"][reference][1]
                            for reference in data["documents"][0]["original_quote_refs"])
    assert reconstructed == original
    assert all(10 <= len(text) <= 400 for _, text in source_spans(b["documents"][0]))
    reference = next(iter(quotation_index(b)))
    claim = {"source_id": "source-1", "quote": reference}
    assert resolve_quotations(claim, b)["quote"] in original
    changed = deepcopy(b)
    changed["documents"][0]["content"] += " changed tail"
    with pytest.raises(ValueError, match="quote_reference"):
        resolve_quotations(claim, changed)


def test_quote_reference_cannot_invent_or_cross_assign_sources():
    b = referenced_bundle()
    b["documents"].append({**b["documents"][0], "id": "source-2"})
    other = source_spans(b["documents"][1])[0][0]
    for reference in (other, "@original:" + "0"*20):
        with pytest.raises(ValueError, match="quote_reference"):
            resolve_quotations({"source_id": "source-1", "quote": reference}, b)
    assert b["documents"][0]["content"] == CONTENT


def test_reference_resolution_keeps_numeric_rejection_and_typed_review_bounds():
    b = referenced_bundle()
    reference, exact = source_spans(b["documents"][0])[0]
    p = proposal().model_dump(mode="json")
    p["summary"][0]["evidence"] = [{"source_id": "source-1", "quote": reference}]
    p["summary"][0]["text"] = "기업 이익이 9999배 증가했습니다."
    written = response({"request_id": "news-brief-test-write"}, BriefProposal.model_validate(p))
    restored = artifact(written, BriefProposal, b)
    assert restored.summary[0].evidence[0].quote == exact
    assert validate(restored, b)["summary"] == "unsupported_prose_number"
    r = review().model_dump(mode="json")
    r["source_assessments"][0]["material_facts"][0]["quote"] = reference
    reviewed = response({"request_id": "news-brief-test-review"}, BriefReview.model_validate(r))
    restored_review = artifact(reviewed, BriefReview, b)
    assert restored_review.source_assessments[0].material_facts[0].quote == exact
    validate_review(restored_review, proposal(), b)


def test_reference_review_prompt_preserves_original_and_evidence_without_duplicate_text():
    b = referenced_bundle()
    p = proposal()
    reference, exact = source_spans(b["documents"][0])[0]
    p.summary[0].evidence[0].quote = exact
    data = json.loads(prompt(b, "review", p.model_dump(mode="json")).split("BRIEF DATA JSON:\n")[1])
    assert reference in {q[1] for q in data["evidence_quotes"].values()}
    assert "".join(data["original_quotes"][reference][1]
                   for reference in data["documents"][0]["original_quote_refs"]) == CONTENT


def test_nested_repair_protection_preserves_exact_quotes_without_mutating_frozen_feedback():
    b = referenced_bundle()
    reference, exact = source_spans(b['documents'][0])[0]
    payload = {'documents': deepcopy(b['documents']), 'revision_feedback': {
        'protected_claims': [{'id': 'fact', 'text': '검증된 원래 문장', 'evidence': [
            {'source_id': 'source-1', 'quote': exact}]}],
        'source_assessments': [{'source_id': 'source-1', 'material_facts': [
            {'fact': '본문에서 누락된 사실', 'quote': exact, 'main_item_ids': []}]}]}}
    frozen = deepcopy(payload)
    encoded = reference_payload(payload, b)
    protection = encoded['revision_feedback']['protected_claims'][0]
    assert protection['evidence'][0]['quote'] == reference
    assert encoded['revision_feedback']['source_assessments'][0]['material_facts'][0]['quote'] == reference
    assert resolve_quotations(encoded['revision_feedback'], b) == frozen['revision_feedback']
    assert payload['revision_feedback'] == frozen['revision_feedback']
    assert b['documents'][0]['content'] == CONTENT


def test_repair_context_keeps_twenty_complete_originals_and_reconstructible_protected_claims():
    b = referenced_bundle()
    b['documents'] = [{**b['documents'][0], 'id': f'source-{i}',
                       'content': (f'Synthetic original {i} has a baseline, a revision, a funding limit and a supply offset.\n' * 20)}
                      for i in range(20)]
    protected = []
    for i in range(26):
        document = b['documents'][i % 20]
        protected.append({'id': f'protected-{i}', 'text': '이미 검증한 경제적 변화와 조건을 보존합니다.',
                          'kind': 'fact', 'evidence': [{'source_id': document['id'], 'quote': text}
                                                     for _, text in source_spans(document)[:4]]})
    previous = proposal().model_dump(mode='json')
    b['revision_feedback'] = {'repair_mode': 'editorial_patch', 'previous_draft': previous,
                             'allowed_sources': {item['id']: [e['source_id'] for e in item['evidence']]
                                                 for item in protected},
                             'allowed_ids': [item['id'] for item in protected],
                             'protected_claims': protected, 'checks': {}, 'concerns': [],
                             'source_assessments': [], 'protected_material_facts': []}
    original = deepcopy(b)
    request_text = prompt(b, 'revise')
    assert len(request_text) <= 88000
    data = json.loads(request_text.split('BRIEF DATA JSON:\n')[1])
    assert resolve_quotations(data['revision_feedback']['protected_claims'], b) == protected
    for source, document in zip(original['documents'], data['documents'], strict=True):
        assert ''.join(data['original_quotes'][ref][1] for ref in document['original_quote_refs']) == source['content']
    assert b == original


def test_real_postgresql_producer_consumer_preserves_exact_quotes_and_raw_receipts(brief):  # noqa: F811
    store, _ = brief
    edition = seed(brief)
    request = store.prepare()["request"]
    with store.db.transaction() as conn:
        b = conn.execute("SELECT bundle FROM brief_editions WHERE id=%s", (edition.id,)).fetchone()["bundle"]
    assert b["quote_reference_version"] == 1
    reference, exact = source_spans(b["documents"][0])[0]
    p = proposal()
    p.summary[0].evidence[0].quote = reference
    written = response(request, p)
    assert store.commit(written)["state"] == "completed"
    with store.db.transaction() as conn:
        row = conn.execute("SELECT proposal FROM brief_editions WHERE id=%s", (edition.id,)).fetchone()
        raw = conn.execute("SELECT response FROM brief_calls WHERE id=%s", (request["request_id"],)).fetchone()
    assert row["proposal"]["summary"][0]["evidence"][0]["quote"] == exact
    assert reference in raw["response"]["decision"]["artifacts"][0]["content"]
    critic_request = store.prepare()["request"]
    r = review()
    r.source_assessments[0].material_facts[0].quote = reference
    assert store.commit(response(critic_request, r))["state"] == "completed"
    assert store.commit(written)["duplicate"]
