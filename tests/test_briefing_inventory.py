"""Frozen fact conservation; real PostgreSQL lifecycle with simulated model output."""

import json
from copy import deepcopy

import pytest
from psycopg.types.json import Jsonb

from quant_company.briefing.contracts import (
    BriefComposition,
    EditorialPatch,
    FactInventory,
    FragmentFactInventory,
    SourcePlan,
)
from quant_company.briefing.editor import (
    SourceNotesValidationError,
    prompt,
    revision_bundle,
    validate_source_notes,
)
from quant_company.briefing.execution import timeout_seconds
from quant_company.briefing.inventory import compose, facts, freeze_inventory, inventory_prompt
from quant_company.briefing.qualification import replay
from quant_company.contracts import ProviderRequest
from quant_company.providers.codex_runner import output_schema

from .test_briefing import brief, bundle, proposal, response, review, seed  # noqa: F401


def inventory():
    return FactInventory(sources=[{
        'source_id': 'source-1', 'treatment': 'covered',
        'reason': 'INVENTORY_PRIVATE_SENTINEL: 시장 상승의 참여 범위를 구분하는 필수 사실이다.',
        'material_facts': [{'fact': '반도체가 상승을 주도했지만 기술주 밖의 참여는 혼조입니다.',
            'quote': 'Semiconductor shares led the advance, but participation outside technology was mixed.',
            'qualifiers': [{'kind': 'counterevidence', 'text': '기술주 밖의 참여는 혼조'}]}]}])


def composition():
    p = proposal().model_dump(mode='json')
    p['issues'][0]['fact']['text'] = inventory().sources[0].material_facts[0].fact
    return BriefComposition(**p, fact_placements=[{'fact_id': 'f1', 'main_item_ids': ['fact']}])


def fragment_inventory():
    value = inventory().model_dump(mode='json')
    for source in value['sources']:
        for fact in source['material_facts']:
            fact['quote'] = [fact['quote']]
            fact['qualifiers'] = [q['text'] for q in fact['qualifiers']]
    return FragmentFactInventory.model_validate(value)


def frozen():
    return freeze_inventory(inventory(), bundle() | {'source_notes_required': True,
        'fact_inventory_required': True, 'quote_reference_version': 1})


def test_separate_inventory_preserves_full_original_and_requires_exact_source_accounting():
    b = bundle() | {'source_notes_required': True, 'quote_reference_version': 1}
    original = deepcopy(b)
    text = inventory_prompt(b)
    assert 'September 21, 2026' in text and '한국 반도체 업종' in text
    assert b == original
    result = freeze_inventory(inventory(), b)
    assert result['documents'] == b['documents'] and 'fact_inventory' not in b
    for modify, reason in [
        (lambda v: v.sources.append(deepcopy(v.sources[0])), 'source_accounting'),
        (lambda v: setattr(v.sources[0], 'treatment', 'background'), 'materiality_shape'),
        (lambda v: setattr(v.sources[0].material_facts[0], 'quote', 'An invented original statement.'), 'not_supported'),
        (lambda v: setattr(v.sources[0].material_facts[0].qualifiers[0], 'text', '전년 대비'), 'qualifier_not_in_fact'),
    ]:
        v = inventory()
        modify(v)
        with pytest.raises(ValueError, match=reason):
            freeze_inventory(v, b)


def test_writer_places_committed_facts_and_cannot_replace_or_silently_drop_them():
    b = frozen()
    p = compose(composition(), b)
    validate_source_notes(p, b)
    assert p.source_notes[0].material_facts[0].fact == inventory().sources[0].material_facts[0].fact
    bad = composition()
    bad.fact_placements = []
    with pytest.raises(ValueError, match='placement_accounting'):
        compose(bad, b)
    p.source_notes[0].material_facts = []
    with pytest.raises(SourceNotesValidationError) as failure:
        validate_source_notes(p, b)
    assert 'committed_inventory_fact_removed' in {v['reason'] for v in failure.value.violations}
    b['fact_inventory']['sources'][0]['reason'] = '원래의 사실 확정 기록을 변경하면 안 됩니다.'
    with pytest.raises(ValueError, match='missing_or_changed'):
        facts(b)


def test_non_numeric_counterevidence_or_comparison_cannot_disappear_during_compression():
    b = frozen()
    p = compose(composition(), b)
    p.issues[0].fact.text = '반도체가 상승을 주도했습니다.'
    with pytest.raises(SourceNotesValidationError) as failure:
        validate_source_notes(p, b)
    assert failure.value.violations == [{
        'source_id': 'source-1', 'fact_index': 0, 'fact_id': 'f1',
        'reason': 'material_qualifier_missing_from_main', 'missing': ['기술주 밖의 참여는 혼조'],
        'main_item_ids': ['fact']}]
    # Space-only Korean variants retain the same economic phrase.
    p.issues[0].fact.text = '반도체가 주도했지만 기술주 밖의 참여는  혼조입니다.'
    validate_source_notes(p, b)


def test_independent_critic_gets_originals_and_main_without_inventory_or_selector_verdict():
    b = frozen()
    p = compose(composition(), b)
    writer = prompt(b, 'write', direct_output=True)
    critic = prompt(b, 'review', p.model_dump(mode='json'), direct_output=True)
    assert 'committed_inventory' in writer and 'fact_placements' in writer
    assert 'committed_inventory' not in critic and 'INVENTORY_PRIVATE_SENTINEL' not in critic
    assert 'Semiconductor shares led' in critic and p.issues[0].fact.text in critic


def test_large_writer_input_preserves_all_originals_facts_qualifiers_and_attribution():
    from quant_company.briefing.inventory import composition_inventory
    from quant_company.briefing.quotations import resolve_quotations

    from .test_briefing import CONTENT
    from .test_briefing_quotations import original_body

    b = frozen()
    b['documents'] = [{**b['documents'][0], 'id': f'source-{i:040x}',
        'title': 'Synthetic market baseline and follow-through report. ' * 7,
        'content': CONTENT * 8} for i in range(20)]
    sources = []
    for document in b['documents']:
        source = deepcopy(inventory().sources[0])
        source.source_id = document['id']
        source.material_facts = [deepcopy(source.material_facts[0]) for _ in range(3)]
        for fact in source.material_facts:
            fact.fact *= 4
        sources.append(source)
    b = freeze_inventory(FactInventory(sources=sources), b)
    original = deepcopy(b)
    text = prompt(b, 'write', direct_output=True)
    data = json.loads(text.split('BRIEF DATA JSON:\n')[1])
    assert len(text) <= 88000 and data['original_quote_layout'] == 'grouped_documents'
    documents = [dict(zip(data['document_columns'], row, strict=True)) for row in data['documents']]
    for index, document in enumerate(b['documents']):
        assert original_body(data, index) == document['content']
        assert documents[index]['id'] == document['id'] and documents[index]['title'] == document['title']
    committed = data['committed_inventory']
    for expected, row in zip(composition_inventory(b)['facts'], committed['facts'], strict=True):
        actual = dict(zip(committed['fact_columns'], row, strict=True))
        actual['source_id'] = documents[actual.pop('source_document_index')]['id']
        assert resolve_quotations(actual, b) == expected
    assert b == original


def test_new_original_notes_cannot_replace_previously_committed_source():
    b = frozen()
    b['documents'].append({**deepcopy(b['documents'][0]), 'id': 'supplement'})
    v = composition()
    with pytest.raises(ValueError, match='supplement_accounting'):
        compose(v, b)
    v.supplemental_source_notes = [{'source_id': 'source-1', 'treatment': 'background',
        'reason': '기존 사실을 보완 자료로 위장하여 제거해서는 안 된다.', 'material_facts': []}]
    with pytest.raises(ValueError, match='supplement_accounting'):
        compose(BriefComposition.model_validate(v.model_dump()), b)
    v = composition().model_dump()
    v['supplemental_source_notes'] = [{'source_id': 'supplement', 'treatment': 'background',
        'reason': '동일한 원문의 중복이며 추가로 전달할 핵심 사실이 없음.', 'material_facts': []}]
    p = compose(BriefComposition.model_validate(v), b)
    validate_source_notes(p, b)
    assert len(p.source_notes) == 2


def test_supported_but_incomplete_mechanism_can_receive_targeted_semantic_repair():
    p = proposal()
    r = review()
    r.verdict = 'reduce'
    r.checks.update(numbers=False, alternatives=False, readability=False)
    r.concerns = ['누락된 비교 기준과 대안 설명을 기존 문장에 보완해야 함']
    b = revision_bundle(bundle(), p.model_dump(mode='json'), r, {})
    assert b['revision_feedback']['repair_mode'] == 'editorial_patch'
    assert {'mechanism', 'alternative', 'summary'} <= set(b['revision_feedback']['allowed_ids'])
    assert 'sp500' not in b['revision_feedback']['allowed_ids']


@pytest.mark.parametrize('phase,contract,seconds', [
    ('inventory', 'brief_inventory_v1', 480), ('write', 'brief_compose_v1', 960),
    ('inventory', 'brief_inventory_v2', 1080),
    ('write', 'brief_write_v1', 1440), ('revise', 'brief_compose_v1', 360),
])
def test_phase_contracts_have_bounded_budgets_and_official_structured_shapes(phase, contract, seconds):
    req = ProviderRequest(request_id='news-brief-00000000-0000-0000-0000-000000000001-'+phase,
                          model='gpt-6-astra', prompt='fixture', output_contract=contract)
    assert timeout_seconds(req) == seconds
    assert output_schema(req)['additionalProperties'] is False


def test_real_postgres_inventory_is_committed_before_write_and_reused_after_restart(brief):  # noqa: F811
    store, clock = brief
    store.company.settings.briefing_source_notes_enabled = True
    store.company.settings.briefing_publish_enabled = False
    edition = seed(brief)
    b = bundle()
    b['candidate_documents'] = b['documents']
    with store.db.transaction() as conn:
        conn.execute('UPDATE brief_editions SET bundle=%s WHERE id=%s', (Jsonb(b), edition.id))
    planned = store.prepare()['request']
    assert planned['output_contract'] == 'brief_plan_v1'
    store.commit(response(planned, SourcePlan(selections=[{'source_id': 'source-1',
        'reason': '시장의 종가와 업종 참여를 함께 설명하는 원문이다.'}], priorities=['시장 참여의 폭을 확인한다.'])))
    reading = store.prepare()['request']
    assert reading['output_contract'] == 'brief_inventory_v2'
    assert store.prepare()['request'] == reading
    store.commit(response(reading, fragment_inventory()))
    writer = store.prepare()['request']
    assert writer['output_contract'] == 'brief_compose_v1'
    with store.db.transaction() as conn:
        saved = conn.execute('SELECT bundle FROM brief_editions WHERE id=%s', (edition.id,)).fetchone()['bundle']
    assert saved['fact_inventory_version'] == 2
    assert saved['fact_inventory_digest'] == freeze_inventory(fragment_inventory(), bundle())['fact_inventory_digest']
    store.commit(response(writer, composition()))
    critic = store.prepare()['request']
    assert critic['output_contract'] == 'brief_review_v2'
    assert 'INVENTORY_PRIVATE_SENTINEL' not in critic['prompt']
    store.commit(response(critic, review()))
    clock['at'] = edition.due_at
    store.flush()
    with store.db.transaction() as conn:
        row = conn.execute('SELECT * FROM brief_editions WHERE id=%s', (edition.id,)).fetchone()
        calls = conn.execute('SELECT phase,requested_at FROM brief_calls WHERE edition_id=%s', (edition.id,)).fetchall()
    assert row['state'] == 'previewed' and replay(row)['ok']
    assert {c['phase'] for c in calls} == {'plan', 'inventory', 'write', 'review'}
    assert all(c['requested_at'] for c in calls)


def test_real_postgres_new_inventory_path_allows_one_semantic_patch_and_final_review(brief):  # noqa: F811
    store, clock = brief
    store.company.settings.briefing_source_notes_enabled = True
    store.company.settings.briefing_max_revisions = 1
    store.company.settings.briefing_publish_enabled = False
    edition = seed(brief)
    b = bundle() | {'candidate_documents': bundle()['documents']}
    with store.db.transaction() as conn:
        conn.execute('UPDATE brief_editions SET bundle=%s WHERE id=%s', (Jsonb(b), edition.id))
    plan = SourcePlan(selections=[{'source_id': 'source-1', 'reason': '종가와 업종 참여를 확인할 핵심 자료'}],
                      priorities=['업종 참여를 확인한다.'])
    for value in (plan, fragment_inventory(), composition()):
        store.commit(response(store.prepare()['request'], value))
    critic = review()
    critic.verdict = 'reduce'
    critic.checks['alternatives'] = False
    critic.checks['readability'] = False
    critic.concerns = ['대안 설명을 더 명확하게 써야 한다.']
    store.commit(response(store.prepare()['request'], critic))
    request = store.prepare()['request']
    assert request['output_contract'] == 'brief_editorial_v2'
    edit = EditorialPatch(edits=[{'id': 'alternative',
        'text': '반도체 자체의 재료가 더 크게 작용했을 가능성도 있어 금리만으로 상승을 설명할 수 없습니다.'}])
    store.commit(response(request, edit))
    final = store.prepare()['request']
    assert final['request_id'].endswith('-final_review')
    store.commit(response(final, review()))
    clock['at'] = edition.due_at
    store.flush()
    with store.db.transaction() as conn:
        row = conn.execute('SELECT * FROM brief_editions WHERE id=%s', (edition.id,)).fetchone()
        count = conn.execute('SELECT count(*) AS n FROM brief_calls WHERE edition_id=%s', (edition.id,)).fetchone()['n']
    assert count == 6 and row['quality']['revision_used'] and not row['quality']['reduced']
    assert row['proposal']['source_notes'][0]['material_facts'][0]['fact'] == inventory().sources[0].material_facts[0].fact
    assert replay(row)['ok']


def two_fact_inventory(second_quote, second_fact='3월 비상계획에서 약 3억2500만 배럴이 공급됐다.'):
    value = inventory().model_dump(mode='json')
    value['sources'][0]['material_facts'].append({'fact': second_fact, 'quote': second_quote, 'qualifiers': []})
    return FactInventory.model_validate(value)


def test_unsupported_fact_is_withheld_and_recorded_without_blocking_the_inventory():
    b = bundle() | {'source_notes_required': True, 'quote_reference_version': 1}
    result = freeze_inventory(two_fact_inventory('An invented original statement about barrels.'), b)
    kept = result['fact_inventory']['sources'][0]['material_facts']
    assert [f['fact'] for f in kept] == [inventory().sources[0].material_facts[0].fact]
    assert result['fact_inventory_dropped'] == [{'source_id': 'source-1', 'reason': 'quote_not_in_original',
                                                 'fact': '3월 비상계획에서 약 3억2500만 배럴이 공급됐다.'}]
    # The committed digest covers only the kept facts; nothing is repaired or invented.
    assert facts(result)[1].keys() == {'f1'}


def test_source_left_without_supported_facts_becomes_background_and_all_unsupported_still_blocks():
    b = bundle() | {'source_notes_required': True, 'quote_reference_version': 1}
    v = inventory()
    v.sources[0].material_facts[0].quote = 'An invented original statement.'
    with pytest.raises(ValueError, match='inventory_fact_not_supported'):
        freeze_inventory(v, b)
    value = inventory().model_dump(mode='json')
    extra = deepcopy(value['sources'][0])
    extra['source_id'] = 'source-2'
    extra['material_facts'][0]['quote'] = 'An invented original statement.'
    docs = deepcopy(b['documents'])
    docs.append(deepcopy([d for d in docs if d['id'] == 'source-1'][0]) | {'id': 'source-2'})
    result = freeze_inventory(FactInventory.model_validate({'sources': value['sources'] + [extra]}), b | {'documents': docs})
    second = result['fact_inventory']['sources'][1]
    assert second['treatment'] == 'background' and second['material_facts'] == []
    assert second['reason'].startswith('원문 대조를 통과한 사실이 없어 배경으로 둔다')
    assert [d['source_id'] for d in result['fact_inventory_dropped']] == ['source-2']


def long_inventory_bundle(paragraphs):
    from .test_briefing import CONTENT

    b = frozen()
    filler = ' Additional background paragraph about regional market conditions.' * paragraphs
    b['documents'] = [{**b['documents'][0], 'id': f'source-{i:040x}',
        'title': 'Synthetic market baseline and follow-through report. ' * 7,
        'content': CONTENT+filler} for i in range(20)]
    sources = []
    for document in b['documents']:
        source = deepcopy(inventory().sources[0])
        source.source_id = document['id']
        sources.append(source)
    return freeze_inventory(FactInventory(sources=sources), b)


def test_composition_over_the_input_limit_keeps_committed_quote_rows_and_records_trimmed_originals():
    from quant_company.briefing.quotations import resolve_quotations

    fits = prompt(long_inventory_bundle(40), 'write', direct_output=True)
    assert len(fits) <= 88000 and 'trimmed_originals' not in fits and 'Input limit:' not in fits
    b = long_inventory_bundle(64)
    original = deepcopy(b)
    text = prompt(b, 'write', direct_output=True)
    data = json.loads(text.split('BRIEF DATA JSON:\n')[1])
    assert len(text) <= 88000 and 'Input limit: original_quotes keeps every row' in text
    assert data['trimmed_originals'] and all(row[1] > 0 and row[2] > 0 for row in data['trimmed_originals'])
    quote = inventory().sources[0].material_facts[0].quote
    kept = [row[1] for group in data['original_quotes'] for row in group]
    # Every document keeps the row holding its committed fact quote; the frozen bundle is unchanged.
    assert sum(quote in row for row in kept) == 20
    assert b == original and resolve_quotations(data['committed_inventory'], b)
