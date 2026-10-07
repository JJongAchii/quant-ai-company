import json
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

import pytest

from quant_company.briefing.contracts import SourceAssessment
from quant_company.briefing.editor import SourceNotesValidationError, prompt, prune, validate_source_notes
from quant_company.briefing.quality import reconcile

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


def test_reconciled_observation_updates_only_removed_note_references():
    b, p = notes_case()
    redundant = p.observations[0].model_copy(update={'id': 'duplicate-sp500'})
    p.observations.append(redundant)
    b['locked_observations'] = [p.observations[0].model_dump(mode='json')]
    note = p.source_notes[0]
    note.item_ids.append(redundant.id)
    note.material_facts[0].main_item_ids.append(redundant.id)
    accepted, conflicts = reconcile(p, b)
    assert not conflicts
    assert accepted.source_notes[0].material_facts[0].main_item_ids == ['fact']
    assert accepted.source_notes[0].item_ids == ['fact']
    validate_source_notes(accepted, b)
    assert note.material_facts[0].main_item_ids == ['fact', 'duplicate-sp500']
    note.material_facts[0].main_item_ids = ['duplicate-sp500']
    accepted, _ = reconcile(p, b)
    with pytest.raises(SourceNotesValidationError, match='source_notes_fact_not_in_main'):
        validate_source_notes(accepted, b)


def test_large_review_keeps_originals_and_visible_main_without_duplicate_writer_inventory():
    from html import escape

    from quant_company.briefing.editor import item_map, render

    b, p = notes_case()
    b['quote_reference_version'] = 1
    base = deepcopy(b['documents'][0])
    b['documents'] = [{**base, 'id': 'source-'+str(i+1),
        'title': 'Complete synthetic market source '+str(i)+' '+('reported context ' * 8),
        'content': base['content']+'\n'+('original text '+str(i)+' ')*145} for i in range(20)]
    first = deepcopy(p.issues[0])
    for i in range(1, 6):
        issue = first.model_copy(deep=True)
        for claim in (issue.fact, issue.interpretation, issue.next_check,
                      issue.analysis.mechanism, issue.analysis.alternative):
            claim.id += '-'+str(i)
        p.issues.append(issue)
    b['candidate_documents'] = [*b['documents'], *[
        {**base, 'id': 'candidate-'+str(i), 'title': ('Distinct discovery title '+str(i)+' ')*6}
        for i in range(76)]]
    original = deepcopy(b)
    text = prompt(b, 'review', p.model_dump(mode='json'), direct_output=True)
    data = json.loads(text.split('BRIEF DATA JSON:\n')[1])
    assert len(text) <= 88000
    assert data['original_quote_layout'] == 'grouped_documents'
    assert 'proposal' not in data and 'source_notes' not in data
    for index, doc in enumerate(original['documents']):
        assert ''.join(s for _, s in data['original_quotes'][index]) == doc['content']
    actual = ''.join(part[1] if isinstance(part, list) else part for part in data['main_post_preview'])
    rendered = render(p, b)[0][0]
    import re
    rendered = re.sub(r'<[^<>|\n]+\|\[(\d+)\]>', r'[\1]', rendered)
    assert actual == rendered
    annotated = {part[0]: part[1] for part in data['main_post_preview'] if isinstance(part, list)}
    for identity, exact in annotated.items():
        assert exact == escape(item_map(p)[identity].text, quote=False)
    assert len(data['unselected_source_index']) == 76
    assert b == original


def test_observation_inventory_checks_the_visible_signed_row_not_hidden_previous_value():
    b, p = notes_case()
    quote = 'The S&P 500 closed at 5300.00 points, down 0.89 percent.'
    b['documents'][0]['content'] += '\n'+quote
    obs = p.observations[0]
    obs.evidence[0].quote = quote
    obs.previous_value = obs.previous_session_date = None
    obs.reported_change, obs.change_unit = Decimal('-0.89'), '%'
    obs = type(obs).model_validate(obs.model_dump())
    p.observations[0] = obs
    fact = p.source_notes[0].material_facts[0]
    fact.fact, fact.quote, fact.main_item_ids = 'S&P 500 5300.00·0.89% 하락', quote, [obs.id]
    validate_source_notes(p, b)
    fact.fact = 'S&P 500 5300.00·0.88% 하락'
    with pytest.raises(SourceNotesValidationError, match='source_notes_fact_not_supported'):
        validate_source_notes(p, b)
    # The previous close is an input to the displayed return, not a visible row.
    original = proposal().observations[0]
    p.observations[0] = original
    fact.fact, fact.quote = '직전 종가는 5200.00이었다.', original.evidence[0].quote
    with pytest.raises(SourceNotesValidationError, match='source_notes_material_numbers_missing_from_main'):
        validate_source_notes(p, b)


def test_signed_decline_inventory_matches_explicit_korean_decrease_in_main():
    b, p = notes_case()
    quote = '영업이익은 4.4% 감소할 것으로 전망했다.'
    b['documents'][0]['content'] += '\n'+quote
    p.issues[0].fact.text = quote
    p.issues[0].fact.evidence[0].quote = quote
    fact = p.source_notes[0].material_facts[0]
    fact.fact = '영업이익 전망은 -4.4%다.'
    fact.quote = quote
    validate_source_notes(p, b)
    p.issues[0].fact.text = '영업이익은 4.4% 증가할 것으로 전망했다.'
    with pytest.raises(SourceNotesValidationError):
        validate_source_notes(p, b)


def test_offline_missing_forecast_day_saves_failed_preview_without_review_call(tmp_path, monkeypatch):
    import asyncio
    import sys

    from scripts import evaluate_briefing

    b, p = notes_case()
    quote = '6일 원달러 예상 범위는 1337~1352원이다.'
    b['documents'][0]['content'] += '\n'+quote
    p.issues[0].fact.text = '원달러 예상 범위는 1337~1352원이다.'
    p.issues[0].fact.evidence[0].quote = quote
    fact = p.source_notes[0].material_facts[0]
    fact.fact, fact.quote = quote, quote
    written = response({'request_id': 'fixture-write'}, p)
    result = evaluate_briefing.assess(b, written)
    assert not result['passed'] and result['source_notes_violations'] == [{
        'reason': 'source_notes_material_numbers_missing_from_main', 'source_id': 'source-1',
        'fact_index': 0, 'main_item_ids': ['fact']}]
    with pytest.raises(SourceNotesValidationError):
        evaluate_briefing.request(b, 'review', result['proposal'])
    source, writer, output = tmp_path/'bundle.json', tmp_path/'write.json', tmp_path/'preview'
    source.write_text(json.dumps(b))
    writer.write_text(written.model_dump_json())
    monkeypatch.setattr(sys, 'argv', ['evaluate_briefing', '--bundle', str(source), '--writer',
                                    str(writer), '--output', str(output)])
    asyncio.run(evaluate_briefing.main())
    assert (output/'assessment.json').exists()
    assert '품질 통과 아님' in (output/'brief.md').read_text()
    assert '1337~1352' in (output/'brief.md').read_text()
    assert not (output/'review-request.json').exists()
    with pytest.raises(SourceNotesValidationError):
        evaluate_briefing.assess(b, written, response({'request_id': 'fixture-review'}, review()))


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


def test_pruned_redundant_reference_keeps_all_fact_numbers_in_surviving_main():
    b, p = notes_case()
    observation = p.observations[0].id
    p.source_notes[0].material_facts[0].main_item_ids.append(observation)
    accepted = prune(p, {observation: 'rejected_observation'})
    assert accepted.source_notes[0].material_facts[0].main_item_ids == ['fact']
    assert p.source_notes[0].material_facts[0].main_item_ids == ['fact', observation]
    validate_source_notes(accepted, b)


def test_pruning_never_hides_invented_references_or_missing_material_numbers():
    b, p = notes_case()
    p.source_notes[0].material_facts[0].main_item_ids.append('invented-id')
    accepted = prune(p, {'unrelated': 'rejected'})
    with pytest.raises(ValueError, match='source_notes_fact_not_in_main'):
        validate_source_notes(accepted, b)
    p.source_notes[0].material_facts[0].main_item_ids = ['fact', p.summary[0].id]
    p.issues[0].next_check.text = '금리가 999%인지 확인한다.'
    with pytest.raises(ValueError, match='source_notes_material_numbers_missing_from_main'):
        validate_source_notes(p, b)


@pytest.mark.parametrize('publication', [False, True])
def test_real_postgres_missing_inventory_blocks_without_reserving_review(brief, publication):  # noqa: F811
    store, clock = brief
    store.company.settings.briefing_source_notes_enabled = True
    store.company.settings.briefing_max_revisions = 0
    store.company.settings.briefing_publish_enabled = publication
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
    clock['at'] = edition.due_at+timedelta(minutes=10)
    store.flush()
    with store.db.transaction() as conn:
        saved = conn.execute('SELECT state,proposal,rendered,quality FROM brief_editions WHERE id=%s', (edition.id,)).fetchone()
        assert saved['state'] == ('committed' if publication else 'previewed') and saved['proposal'] is None
        assert saved['quality']['reduced']
        if publication:
            assert not saved['quality'].get('unreviewed_draft_preserved')
            assert '미국 증시는 반도체가 주도했으며' not in saved['rendered'][0]
        else:
            assert saved['quality']['unreviewed_draft_preserved']
            assert '품질 검사 미통과 초안' in saved['rendered'][0]
            assert '미국 증시는 반도체가 주도했으며' in saved['rendered'][0]
            assert conn.execute('SELECT count(*) AS n FROM brief_messages').fetchone()['n'] == 0


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


@pytest.mark.parametrize('scope', ['single', 'window'])
def test_bounded_evaluation_does_not_spend_a_search_before_inputs_are_sealed(brief, scope):  # noqa: F811
    store, clock = brief
    if scope == 'single':
        store.company.settings.briefing_evaluation_edition_id = definition().id
    else:
        store.company.settings.briefing_evaluation_edition_ids = [definition().id]
        store.company.settings.briefing_max_revisions = 0
    store.company.settings.briefing_search_enabled = True
    store.company.settings.company_web_enabled = True
    edition = seed(brief)
    clock['at'] = edition.cutoff-timedelta(seconds=30)
    assert store.prepare() == {'state': 'idle'}
    with store.db.transaction() as conn:
        assert conn.execute('SELECT count(*) AS n FROM brief_calls').fetchone()['n'] == 0


@pytest.mark.parametrize('scope', ['single', 'window'])
def test_bounded_evaluation_blocks_thin_originals_before_the_planner(brief, scope):  # noqa: F811
    from psycopg.types.json import Jsonb

    store, _ = brief
    if scope == 'single':
        store.company.settings.briefing_evaluation_edition_id = definition().id
    else:
        store.company.settings.briefing_evaluation_edition_ids = [definition().id]
        store.company.settings.briefing_max_revisions = 0
    edition = seed(brief)
    frozen = bundle(edition)
    frozen['candidate_documents'] = deepcopy(frozen['documents'])
    with store.db.transaction() as conn:
        conn.execute('UPDATE brief_editions SET bundle=%s WHERE id=%s', (Jsonb(frozen), edition.id))
    assert store.prepare() == {'state': 'blocked', 'reason': 'evaluation_source_coverage_incomplete'}
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
