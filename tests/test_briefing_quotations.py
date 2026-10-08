import json
from copy import deepcopy

import pytest

from quant_company.briefing.contracts import BriefProposal, BriefReview
from quant_company.briefing.editor import artifact, prompt, validate, validate_review
from quant_company.briefing.quotations import (
    compact_reference_payload,
    ordered_reference_payload,
    quotation_index,
    reference_payload,
    resolve_candidate_requests,
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


def large_review_case():
    b = referenced_bundle()
    b['documents'] = [{**b['documents'][0], 'id': f'source-{i}', 'content': CONTENT * 6}
                      for i in range(20)]
    b['candidate_documents'] = [*b['documents'], *[{**b['documents'][0], 'id': f'unselected-{i}',
        'title': ('Synthetic policy and market story with contrasting evidence ' + str(i)).ljust(150, 'x')}
                                                for i in range(76)]]
    p = proposal().model_dump(mode='json')
    sentence = '정책과 성장 전망의 변화는 가격과 자금조달 부담에 서로 다른 영향을 주므로 조건과 반대 근거를 함께 판단합니다. '
    p['issues'] = [deepcopy(p['issues'][0]) for _ in range(6)]
    for i, issue in enumerate(p['issues']):
        for field, length in [('fact', 250), ('interpretation', 300), ('next_check', 75)]:
            issue[field]['id'] += f'_{i}'
            issue[field]['text'] = (sentence * 12)[:length]
        for field in ['mechanism', 'alternative']:
            issue['analysis'][field]['id'] += f'_{i}'
            issue['analysis'][field]['text'] = (sentence * 12)[:150]
    p['overview'] = [{**deepcopy(p['overview'][0]), 'id': f'overview_{i}', 'text': (sentence * 12)[:500]}
                     for i in range(2)]
    p['internals'] = [{**deepcopy(p['overview'][0]), 'id': f'internal_{i}', 'text': (sentence * 12)[:500]}
                      for i in range(2)]
    return b, p


def test_large_final_review_retains_complete_originals_and_entire_unselected_catalog():
    b, p = large_review_case()
    text = prompt(b, 'final_review', p)
    data = json.loads(text.split('BRIEF DATA JSON:\n')[1])
    assert len(text) <= 88000
    assert data['original_quote_layout'] == 'ordered_rows'
    for index, source in enumerate(b['documents']):
        assert ''.join(row[2] for row in data['original_quotes'] if row[1] == index) == source['content']
    assert len(data['unselected_source_index']) == 76
    assert {row[0] for row in data['unselected_source_index']} == {f'unselected-{i}' for i in range(76)}


def test_compact_final_review_preserves_long_catalog_identities_and_all_originals():
    b, p = large_review_case()
    for i, document in enumerate(b['candidate_documents'][20:]):
        document['id'] = f'{i:040x}'
    b['market_context'] = [{'source_id': f'source-{i}', 'text': CONTENT} for i in range(12)]
    frozen = deepcopy(b)
    text = prompt(b, 'final_review', p)
    data = json.loads(text.split('BRIEF DATA JSON:\n')[1])
    assert len(text) <= 88000 and data['original_quote_layout'] == 'compact_ordered_rows'
    assert 'source_notes' not in data['proposal']
    assert [i['fact']['text'] for i in data['proposal']['issues']] == [i['fact']['text'] for i in p['issues']]
    assert [i['interpretation']['text'] for i in data['proposal']['issues']] == [i['interpretation']['text'] for i in p['issues']]
    for i, original in enumerate(b['documents']):
        assert ''.join(row[2] for row in data['original_quotes'] if row[1] == i) == original['content']
    for context, encoded in zip(b['market_context'], data['market_context'], strict=True):
        start, end = encoded['original_text_range']
        assert frozen['documents'][int(context['source_id'].split('-')[1])]['content'][start:end] == context['text']
    for identity, *_ in data['unselected_source_index']:
        decoded = resolve_candidate_requests({'source_requests': [{'source_id': identity}]}, b)
        assert decoded['source_requests'][0]['source_id'] in {d['id'] for d in b['candidate_documents'][20:]}
    assert b == frozen


def test_compact_quote_and_candidate_references_cannot_cross_sources_or_invent_rows():
    b = referenced_bundle()
    b['documents'].append({**b['documents'][0], 'id': 'source-2'})
    b['candidate_documents'] = [*b['documents'], {**b['documents'][0], 'id': 'unselected'}]
    payload = reference_payload({'documents': deepcopy(b['documents']), 'unselected_source_index': [
        ['unselected', 'A distinct counterfact', 100, None]]}, b)
    compact = compact_reference_payload(ordered_reference_payload(payload), b)
    identity, position, exact = compact['original_quotes'][0]
    assert position == 0 and resolve_quotations({'source_id': 'source-1', 'quote': identity}, b)['quote'] == exact
    for source, quote in [('source-2', identity), ('source-1', '@q:9999'), ('source-1', '@q:-1')]:
        with pytest.raises(ValueError, match='quote_reference'):
            resolve_quotations({'source_id': source, 'quote': quote}, b)
    with pytest.raises(ValueError, match='candidate_source_reference'):
        resolve_candidate_requests({'source_requests': [{'source_id': '@candidate:0'}]}, b)
    with pytest.raises(ValueError, match='candidate_source_reference'):
        resolve_candidate_requests({'source_requests': [{'source_id': '@candidate:9999'}]}, b)
    raw = review().model_dump(mode='json')
    raw['source_requests'] = [{'source_id': '@candidate:2', 'reason': 'distinct counterfact'}]
    restored = artifact(response({'request_id': 'fixture-compact-review'}, BriefReview.model_validate(raw)), BriefReview, b)
    assert restored.source_requests[0].source_id == 'unselected'


def test_compact_transport_never_rewrites_prose_that_looks_like_a_reference():
    b = referenced_bundle()
    identity = source_spans(b['documents'][0])[0][0]
    b['documents'].append({**b['documents'][0], 'id': 'source-2', 'content': identity})
    payload = reference_payload({'documents': deepcopy(b['documents']), 'proposal': {
        'text': identity, 'quote': identity}}, b)
    compact = compact_reference_payload(ordered_reference_payload(payload), b)
    assert compact['proposal']['text'] == identity
    assert compact['proposal']['quote'] != identity
    assert ''.join(row[2] for row in compact['original_quotes'] if row[1] == 1) == identity


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
