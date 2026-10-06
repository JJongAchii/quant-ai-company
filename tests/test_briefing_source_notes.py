import json
from copy import deepcopy

import pytest

from quant_company.briefing.contracts import SourceAssessment
from quant_company.briefing.editor import prompt, validate_source_notes

from .test_briefing import brief, bundle, definition, proposal, response, review, seed  # noqa: F401


def notes_case():
    b, p = bundle(), proposal()
    b['source_notes_required'] = True
    quote = '매출은 100억원에서 120억원으로 증가했고 영업이익률은 30%에서 25%로 하락했다.'
    b['documents'][0]['content'] += '\n'+quote
    p.issues[0].fact.text = '매출은 100억원에서 120억원으로 증가했다.'
    p.issues[0].fact.evidence[0].quote = quote
    p.source_notes = [SourceAssessment(source_id='source-1', treatment='covered',
        reason='매출의 실제 변화와 비교 기준을 본문에서 설명함', item_ids=['fact'],
        material_facts=[{'fact': '매출은 100억원에서 120억원으로 증가했다.',
                         'quote': quote, 'main_item_ids': ['fact']}])]
    return b, p


def test_source_notes_supported_material_fact_maps_to_visible_main():
    b, p = notes_case()
    validate_source_notes(p, b)


def test_old_frozen_proposal_remains_compatible_when_not_enabled():
    validate_source_notes(proposal(), bundle())


@pytest.mark.parametrize('mutation,reason', [
    ('missing', 'source_notes_incomplete_or_duplicate'),
    ('duplicate', 'source_notes_incomplete_or_duplicate'),
    ('unknown', 'source_notes_incomplete_or_duplicate'),
    ('unquoted', 'source_notes_fact_not_supported'),
    ('wrong_number', 'source_notes_fact_not_supported'),
    ('unmapped', 'source_notes_fact_not_in_main'),
    ('unknown_item', 'source_notes_fact_not_in_main'),
    ('hidden_in_quote', 'source_notes_material_numbers_missing_from_main'),
    ('covered_without_facts', 'source_notes_covered_without_material_facts'),
])
def test_source_notes_fail_before_spending_a_review(mutation, reason):
    b, p = notes_case()
    note = p.source_notes[0]
    fact = note.material_facts[0]
    if mutation == 'missing':
        p.source_notes = []
    elif mutation == 'duplicate':
        p.source_notes.append(note.model_copy(deep=True))
    elif mutation == 'unknown':
        note.source_id = 'not-frozen'
    elif mutation == 'unquoted':
        fact.quote = 'This purported original was never supplied.'
    elif mutation == 'wrong_number':
        fact.fact = '매출은 150억원으로 증가했다.'
    elif mutation == 'unmapped':
        fact.main_item_ids = []
    elif mutation == 'unknown_item':
        fact.main_item_ids = ['not-in-main']
    elif mutation == 'hidden_in_quote':
        p.issues[0].fact.text = '매출은 증가했지만 실적의 자세한 구성은 별도로 확인해야 한다.'
    else:
        note.material_facts = []
    with pytest.raises(ValueError, match=reason):
        validate_source_notes(p, b)


def test_source_notes_fact_cannot_map_to_different_source():
    b, p = notes_case()
    second = deepcopy(b['documents'][0])
    second.update(id='source-2')
    b['documents'].append(second)
    p.source_notes.append(SourceAssessment(source_id='source-2', treatment='background',
        reason='같은 수치와 사건의 반복 보도이므로 별도 핵심 사실이 없음', item_ids=[], material_facts=[]))
    p.issues[0].fact.evidence[0].source_id = 'source-2'
    with pytest.raises(ValueError, match='source_notes_mapping_not_cited'):
        validate_source_notes(p, b)


def test_critic_never_receives_the_writer_self_inventory():
    b, p = notes_case()
    p.source_notes[0].reason = 'WRITER_PRIVATE_INVENTORY_SENTINEL'
    text = prompt(b, 'review', p.model_dump(mode='json'))
    data = json.loads(text.split('BRIEF DATA JSON:\n')[1])
    assert 'source_notes' not in data['proposal']
    assert 'WRITER_PRIVATE_INVENTORY_SENTINEL' not in text


def test_new_note_transport_keeps_every_original_character_once():
    b, _ = notes_case()
    b['quote_reference_version'] = 1
    original = deepcopy(b)
    data = json.loads(prompt(b, 'write').split('BRIEF DATA JSON:\n')[1])
    assert data['original_quote_layout'] == 'ordered_rows'
    for index, doc in enumerate(original['documents']):
        assert ''.join(text for _, position, text in data['original_quotes'] if position == index) == doc['content']
    assert b == original


@pytest.mark.parametrize('phase', ['write', 'review'])
def test_large_collector_metadata_cannot_displace_originals_or_data_warnings(phase):
    b, p = notes_case()
    b['quote_reference_version'] = 1
    b['source_coverage'] = {'collector_only': 'unused-keyword-map' * 10000}
    b['collection_errors'] = [{'error': 'collector_only' * 10000}]
    b['evaluation'] = {'scope': 'collector_only' * 10000}
    b['source_plan'] = {'priorities': ['KEEP_EDITORIAL_PRIORITY']}
    b['data_diagnostics'] = [{'error': 'KEEP_STALE_MARKET_DATA_WARNING'}]
    original = deepcopy(b)
    text = prompt(b, phase, p.model_dump(mode='json') if phase == 'review' else None)
    data = json.loads(text.split('BRIEF DATA JSON:\n')[1])
    assert len(text) <= 88000
    assert not {'source_coverage', 'collection_errors', 'evaluation'} & data.keys()
    assert data['data_diagnostics'] == b['data_diagnostics']
    if phase == 'write':
        assert data['source_plan'] == b['source_plan']
    for index, doc in enumerate(original['documents']):
        assert ''.join(s for _, position, s in data['original_quotes'] if position == index) == doc['content']
    assert b == original


def test_removed_issue_cannot_cover_a_fact_through_a_surviving_id():
    b, p = notes_case()
    p.issues[0].next_check.text = '금리가 999%까지 상승하는지 확인한다.'
    with pytest.raises(ValueError, match='source_notes_fact_not_in_main'):
        validate_source_notes(p, b)


def test_real_postgres_missing_inventory_blocks_without_reserving_review(brief):  # noqa: F811
    store, _ = brief
    store.company.settings.briefing_source_notes_enabled = True
    edition = seed(brief)
    first = store.prepare()
    store.commit(response(first['request']))
    with store.db.transaction() as conn:
        before = conn.execute('SELECT count(*) AS n FROM brief_calls WHERE edition_id=%s', (edition.id,)).fetchone()['n']
    assert store.prepare() == {'state': 'blocked', 'reason': 'source_notes_incomplete_or_duplicate'}
    assert store.prepare() == {'state': 'idle'}
    with store.db.transaction() as conn:
        assert conn.execute('SELECT count(*) AS n FROM brief_calls WHERE edition_id=%s', (edition.id,)).fetchone()['n'] == before == 1
        saved = conn.execute('SELECT state,error,proposal FROM brief_editions WHERE id=%s', (edition.id,)).fetchone()
    assert saved['state'] == 'blocked' and saved['error'] == 'source_notes_incomplete_or_duplicate'
    assert saved['proposal']


def test_real_postgres_valid_inventory_reaches_independent_review(brief):  # noqa: F811
    store, _ = brief
    store.company.settings.briefing_source_notes_enabled = True
    seed(brief)
    first = store.prepare()
    p = proposal()
    p.source_notes = [SourceAssessment(source_id='source-1', treatment='covered',
        reason='업종 참여의 제한이 핵심 본문에 직접 보임', item_ids=['summary'],
        material_facts=[{'fact': '반도체가 상승을 주도했고 기술주 밖의 참여는 혼조',
            'quote': 'Semiconductor shares led the advance, but participation outside technology was mixed.',
            'main_item_ids': ['summary']}])]
    store.commit(response(first['request'], p))
    second = store.prepare()
    assert second['state'] == 'ready' and second['request']['request_id'].endswith('-review')
    assert 'source_notes' not in json.loads(second['request']['prompt'].split('BRIEF DATA JSON:\n')[1])['proposal']


def test_real_postgres_single_edition_scope_never_reserves_other_cases(brief):  # noqa: F811
    store, _ = brief
    store.company.settings.briefing_evaluation_edition_id = '00000000-0000-0000-0000-000000000000'
    seed(brief)
    assert store.prepare() == {'state': 'idle'}
    with store.db.transaction() as conn:
        assert conn.execute('SELECT count(*) AS n FROM brief_calls').fetchone()['n'] == 0


def test_real_postgres_zero_repair_budget_closes_failed_review(brief):  # noqa: F811
    store, _ = brief
    store.company.settings.briefing_max_revisions = 0
    store.company.settings.briefing_evaluation_edition_id = definition().id
    edition = seed(brief)
    first = store.prepare()
    store.commit(response(first['request']))
    second = store.prepare()
    critique = review()
    critique.verdict = 'reduce'
    critique.checks['materiality'] = False
    critique.concerns = ['중요한 비교 기준이 본문에 빠져 보완 전에는 통과할 수 없음']
    store.commit(response(second['request'], critique))
    assert store.prepare() == {'state': 'idle'}
    with store.db.transaction() as conn:
        phases = conn.execute('SELECT phase FROM brief_calls WHERE edition_id=%s ORDER BY phase', (edition.id,)).fetchall()
        saved = conn.execute('SELECT state,quality FROM brief_editions WHERE id=%s', (edition.id,)).fetchone()
    assert [row['phase'] for row in phases] == ['review', 'write']
    assert saved['state'] == 'ready' and not saved['quality']['editorial_review']['checks']['materiality']
