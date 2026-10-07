"""Disjoint original evidence, compact wire schemas and scheduling headroom."""

import json
from copy import deepcopy

import pytest
from jsonschema import validate as validate_schema

from quant_company.briefing.contracts import BriefProposal, FragmentFactInventory
from quant_company.briefing.editor import SourceNotesValidationError, validate_review, validate_source_notes
from quant_company.briefing.execution import remaining_seconds
from quant_company.briefing.inventory import compose, freeze_inventory
from quant_company.briefing.quotations import (
    compact_reference_payload,
    ordered_reference_payload,
    reference_payload,
    resolve_quotations,
    source_spans,
)
from quant_company.providers.codex_runner import output_schema

from .test_briefing import bundle, proposal, review
from .test_briefing_execution import request
from .test_briefing_inventory import composition, fragment_inventory


def disjoint_bundle():
    b = bundle() | {'fact_inventory_version': 2, 'fact_inventory_required': True, 'source_notes_required': True}
    b['documents'][0]['content'] = 'First original evidence. ' * 12 + 'Unrelated passage. ' * 20 + 'Final original evidence. ' * 12
    b['documents'].append({**deepcopy(b['documents'][0]), 'id': 'source-2'})
    return b


@pytest.mark.parametrize('quote', ['@q:1; @q:3', '@q:1, @q:3', ['@q:1', '@q:3']])
def test_multi_span_reference_roundtrip_never_creates_a_false_contiguous_quote(quote):
    b = disjoint_bundle()
    spans = source_spans(b['documents'][0])
    raw = {'source_id': 'source-1', 'material_facts': [{'quote': quote}]}
    expected = [spans[0][1], spans[2][1]]
    restored = resolve_quotations(raw, b)
    assert restored['material_facts'][0]['quote'] == expected
    payload = {'documents': deepcopy(b['documents']), 'facts': [restored]}
    original = deepcopy(payload)
    encoded = compact_reference_payload(ordered_reference_payload(reference_payload(payload, b)), b)
    assert encoded['facts'][0]['material_facts'][0]['quote'] == ['@q:1', '@q:3']
    assert resolve_quotations(encoded['facts'], b) == original['facts']
    assert payload['facts'] == original['facts']


@pytest.mark.parametrize('quote', ['@q:1; @q:99999', '@q:1; explanation', '@q:1;',
                                   ['@q:1'] * 2, ['@q:1'] * 5, []])
def test_multiple_quotes_still_reject_missing_malformed_duplicate_and_unbounded_references(quote):
    with pytest.raises(ValueError, match='quote'):
        resolve_quotations({'source_id': 'source-1', 'quote': quote}, disjoint_bundle())


def test_one_valid_reference_cannot_launder_another_original():
    b = disjoint_bundle()
    other = source_spans(b['documents'][1])[0][0]
    with pytest.raises(ValueError, match='wrong_source'):
        resolve_quotations({'source_id': 'source-1', 'quote': ['@q:1', other]}, b)


def test_claim_fragment_expansion_preserves_existing_evidence_limit():
    b = disjoint_bundle()
    p = proposal().model_dump(mode='json')
    p['summary'][0]['evidence'] = [{'source_id': 'source-1', 'quote': '@q:1; @q:2; @q:3'},
                                  {'source_id': 'source-1', 'quote': '@q:2; @q:3'}]
    restored = resolve_quotations(p, b)
    assert len(restored['summary'][0]['evidence']) == 5
    with pytest.raises(ValueError, match='at most 4'):
        BriefProposal.model_validate(restored)


def test_fragments_are_validated_individually_and_qualifiers_survive_composition():
    b = bundle() | {'fact_inventory_version': 2, 'fact_inventory_required': True, 'source_notes_required': True}
    value = fragment_inventory()
    fact = value.sources[0].material_facts[0]
    text = fact.quote[0]
    fact.quote = [text[:36], text[36:]]
    frozen = freeze_inventory(value, b)
    p = compose(composition(), frozen)
    validate_source_notes(p, frozen)
    r = review()
    r.source_assessments[0].material_facts[0].quote = fact.quote
    validate_review(r, p, frozen)
    p.issues[0].fact.text = '반도체가 상승을 주도했습니다.'
    with pytest.raises(SourceNotesValidationError, match='material_qualifier_missing_from_main'):
        validate_source_notes(p, frozen)
    fact.quote[1] = 'Unrelated invented evidence.'
    with pytest.raises(ValueError, match='not_supported'):
        freeze_inventory(value, b)


def test_wire_array_allows_short_references_but_domain_requires_resolved_originals():
    value = fragment_inventory().model_dump(mode='json')
    value['sources'][0]['material_facts'][0]['quote'] = ['@q:1']
    schema = output_schema(request('inventory', 'brief_inventory_v2'))
    validate_schema(value, schema)
    with pytest.raises(ValueError, match='at least 10'):
        FragmentFactInventory.model_validate(value)
    assert 'kind' not in json.dumps(schema)
    # References inside the review's string/array union need the same wire treatment.
    r = review().model_dump(mode='json')
    r['source_assessments'][0]['material_facts'][0]['quote'] = ['@q:1']
    validate_schema(r, output_schema(request('review', 'brief_review_v1')))


def test_new_inventory_headroom_is_reserved_before_starting_and_legacy_is_unchanged():
    b = {'fact_inventory_required': True, 'fact_inventory_version': 2}
    assert remaining_seconds('plan', b) == 3120
    assert remaining_seconds('inventory', b) == 2760
    assert remaining_seconds('write', b) == 1680
    assert remaining_seconds('plan', {}) == 2520
    assert remaining_seconds('inventory', {}) == 2160


def test_fragment_inventory_cannot_add_an_unquoted_date_or_number():
    b = bundle() | {'fact_inventory_version': 2}
    for extra in ('7일 ', '9999% '):
        value = fragment_inventory()
        value.sources[0].material_facts[0].fact = extra + value.sources[0].material_facts[0].fact
        with pytest.raises(ValueError, match='not_supported'):
            freeze_inventory(value, b)
