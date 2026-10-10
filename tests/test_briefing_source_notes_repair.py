import json
from copy import deepcopy
from datetime import timedelta

import pytest
from psycopg.types.json import Jsonb

from quant_company.briefing.contracts import SourceAssessment, SourceNotesPatch
from quant_company.briefing.editor import (
    SourceNotesValidationError,
    apply_source_notes_patch,
    prompt,
    source_notes_revision_bundle,
    validate_source_notes,
)
from quant_company.briefing.qualification import qualify, replay

from .test_briefing import brief, definition, response, review, seed  # noqa: F401
from .test_briefing_source_notes import notes_case


def failed_case():
    b, p = notes_case()
    p.issues[0].fact.text = '매출은 증가했지만 구체적인 수치는 별도 확인해야 한다.'
    with pytest.raises(SourceNotesValidationError) as failure:
        validate_source_notes(p, b)
    return b, p, failure.value.violations


def test_mapping_repair_transports_complete_affected_original_only_and_keeps_frozen_bundle():
    b, p, failures = failed_case()
    other = {**deepcopy(b['documents'][0]), 'id': 'unaffected', 'content': 'UNCHANGED_ORIGINAL_SENTINEL'}
    b['documents'].append(other)
    b['quote_reference_version'] = 1
    p.source_notes.append(SourceAssessment(source_id='unaffected', treatment='background',
        reason='동일한 보도의 반복으로 새로운 핵심 사실이 없음', material_facts=[]))
    original = deepcopy(b)
    repaired = source_notes_revision_bundle(b, p.model_dump(mode='json'), failures)
    request = prompt(repaired, 'revise')
    data = json.loads(request.split('BRIEF DATA JSON:\n')[1])
    assert [d['id'] for d in data['documents']] == ['source-1']
    assert ''.join(text for _, index, text in data['original_quotes'] if index == 0) == b['documents'][0]['content']
    assert 'UNCHANGED_ORIGINAL_SENTINEL' not in request
    assert repaired['documents'] == b['documents'] and b == original
    assert data['revision_feedback']['source_ids'] == ['source-1']


def test_mapping_patch_preserves_unaffected_notes_prices_and_protected_numeric_values():
    b, p, failures = failed_case()
    b['documents'].append({**deepcopy(b['documents'][0]), 'id': 'unaffected'})
    unaffected = SourceAssessment(source_id='unaffected', treatment='background',
        reason='관련 사실은 첫 번째 원문의 본문에서 이미 설명함', material_facts=[])
    p.source_notes.append(unaffected)
    repaired = source_notes_revision_bundle(b, p.model_dump(mode='json'), failures)
    patch = SourceNotesPatch(source_notes=[p.source_notes[0]], edits=[
        {'id': 'fact', 'text': '매출은 100억원에서 120억원으로 증가했다.'}])
    result = apply_source_notes_patch(p, patch, repaired['revision_feedback'])
    validate_source_notes(result, b)
    assert result.observations == p.observations
    assert result.summary == p.summary and result.calendar == p.calendar
    assert next(n for n in result.source_notes if n.source_id == 'unaffected') == unaffected
    with pytest.raises(ValueError, match='editorial_patch_loses_verified_numbers'):
        apply_source_notes_patch(result, SourceNotesPatch(source_notes=[p.source_notes[0]],
            edits=[{'id': 'fact', 'text': '매출은 120억원으로 증가했다.'}]), repaired['revision_feedback'])
    with pytest.raises(ValueError, match='source_notes_patch_scope_rejected'):
        apply_source_notes_patch(p, SourceNotesPatch(source_notes=[SourceAssessment(
            source_id='unregistered', treatment='background', reason='공급되지 않은 자료이므로 사용할 수 없음', material_facts=[])]),
            repaired['revision_feedback'])
    with pytest.raises(ValueError, match='editorial_patch_scope_rejected'):
        apply_source_notes_patch(p, SourceNotesPatch(source_notes=[p.source_notes[0]],
            edits=[{'id': p.summary[0].id, 'text': '핵심 요약을 임의로 바꾸면 안 된다.'}]),
            repaired['revision_feedback'])


def test_structurally_invalid_inventory_can_be_corrected_without_changing_prose():
    b, p = notes_case()
    p.source_notes.append(deepcopy(p.source_notes[0]))
    with pytest.raises(SourceNotesValidationError) as failure:
        validate_source_notes(p, b)
    repaired = source_notes_revision_bundle(b, p.model_dump(mode='json'), failure.value.violations)
    result = apply_source_notes_patch(p, SourceNotesPatch(source_notes=[p.source_notes[0]]),
                                     repaired['revision_feedback'])
    validate_source_notes(result, b)
    assert result.issues == p.issues and len(result.source_notes) == 1


@pytest.mark.parametrize('fixed', [True, False])
def test_real_postgres_preflight_repair_runs_once_and_review_requires_valid_proof(brief, fixed):  # noqa: F811
    store, clock = brief
    store.company.settings.briefing_publish_enabled = False
    store.company.settings.briefing_source_notes_enabled = True
    store.company.settings.briefing_max_revisions = 1
    edition = seed(brief)
    b, p, _ = failed_case()
    with store.db.transaction() as conn:
        conn.execute('UPDATE brief_editions SET bundle=%s WHERE id=%s', (Jsonb(b), edition.id))
    first = store.prepare()
    store.commit(response(first['request'], p))
    correction = store.prepare()
    assert correction['request']['request_id'].endswith('-revise')
    patch = SourceNotesPatch(source_notes=p.source_notes, edits=[
        {'id': 'fact', 'text': '매출은 100억원에서 120억원으로 증가했다.'}] if fixed else [])
    store.commit(response(correction['request'], patch))
    final = store.prepare()
    with store.db.transaction() as conn:
        phases = [r['phase'] for r in conn.execute(
            'SELECT phase FROM brief_calls WHERE edition_id=%s ORDER BY phase', (edition.id,)).fetchall()]
    if not fixed:
        assert final['state'] == 'blocked' and store.prepare() == {'state': 'idle'}
        assert phases == ['revise', 'write']
        return
    assert final['request']['request_id'].endswith('-review')
    assert phases == ['review', 'revise', 'write']
    store.commit(response(final['request'], review()))
    clock['at'] = edition.due_at
    store.flush()
    with store.db.transaction() as conn:
        row = conn.execute('SELECT * FROM brief_editions WHERE id=%s', (edition.id,)).fetchone()
        assert row['state'] == 'previewed' and not row['publish']
        assert row['quality']['source_notes_repair_used'] and row['quality']['revision_used']
        assert not row['quality']['reduced']
        assert conn.execute('SELECT count(*) AS n FROM brief_messages').fetchone()['n'] == 0
    assert replay(row)['ok']
    broken = deepcopy(row)
    broken['proposal']['source_notes'][0]['material_facts'][0]['fact'] = '매출은 150억원이었다.'
    assert not replay(broken)['ok']
    with store.db.transaction() as conn:
        conn.execute("UPDATE brief_editions SET review=jsonb_set(review,'{checks,materiality}','false') WHERE id=%s",
                     (edition.id,))
    report = qualify(store.company, at=definition('pm').due_at+timedelta(minutes=15))
    observed = next(c for c in report['editions'] if c['id'] == edition.id)
    assert 'content_quality_not_verified' in observed['reasons']


def test_real_postgres_no_patch_without_time_or_authorized_budget(brief):  # noqa: F811
    store, clock = brief
    store.company.settings.briefing_source_notes_enabled = True
    edition = seed(brief)
    b, p, _ = failed_case()
    with store.db.transaction() as conn:
        conn.execute('UPDATE brief_editions SET bundle=%s WHERE id=%s', (Jsonb(b), edition.id))
    first = store.prepare()
    store.commit(response(first['request'], p))
    clock['at'] = edition.due_at
    assert store.prepare()['state'] == 'blocked'
    with store.db.transaction() as conn:
        assert conn.execute('SELECT count(*) AS n FROM brief_calls WHERE edition_id=%s',
                            (edition.id,)).fetchone()['n'] == 1


def test_real_postgres_deadline_preserves_frozen_data_once_and_does_not_refresh_inputs(brief):  # noqa: F811
    store, clock = brief
    store.company.settings.briefing_publish_enabled = False
    store.company.settings.briefing_source_notes_enabled = True
    store.company.settings.briefing_max_revisions = 0
    edition = seed(brief)
    b, p, _ = failed_case()
    dataset = {**deepcopy(b['documents'][0]), 'id': 'collected-context', 'kind': 'dataset'}
    with store.db.transaction() as conn:
        conn.execute('UPDATE brief_editions SET bundle=%s,market_data=%s WHERE id=%s',
            (Jsonb(b), Jsonb({'documents': [dataset], 'observations': [], 'contexts': [], 'diagnostics': []}), edition.id))
    first = store.prepare()
    store.commit(response(first['request'], p))
    assert store.prepare()['state'] == 'blocked'
    with store.db.transaction() as conn:
        frozen = conn.execute('SELECT bundle FROM brief_editions WHERE id=%s', (edition.id,)).fetchone()['bundle']
    assert frozen['inputs_frozen'] and len(frozen['documents']) == 2
    clock['at'] = edition.due_at+timedelta(minutes=10)
    store.flush()
    with store.db.transaction() as conn:
        final = conn.execute('SELECT bundle FROM brief_editions WHERE id=%s', (edition.id,)).fetchone()['bundle']
    assert final == frozen


def test_six_issue_context_capacity_matches_producer_schema_and_per_issue_limit():
    b, p, failures = failed_case()
    original = deepcopy(p.issues[0])
    p.issues = []
    for index in range(6):
        issue = deepcopy(original)
        for claim in (issue.fact, issue.interpretation, issue.next_check,
                      issue.analysis.mechanism, issue.analysis.alternative, issue.counterpoint):
            if claim:
                claim.id += f'-{index}'
        issue.fact.id = f'issue-{index}'
        issue.context = [original.fact.model_copy(update={'id': f'context-{index}-{n}'})
                         for n in range(2 if index < 4 else 1)]
        p.issues.append(issue)
    p = type(p).model_validate(p.model_dump())
    feedback = source_notes_revision_bundle(b, p.model_dump(mode='json'), failures)['revision_feedback']
    assert feedback['context_addition_limit'] == 2
    changes = [{'issue_fact_id': f'issue-{index}', 'claim': original.fact.model_copy(
        update={'id': f'repair-context-{index}'}).model_dump()} for index in (4, 5)]
    result = apply_source_notes_patch(p, SourceNotesPatch(source_notes=p.source_notes,
        context_additions=changes), feedback)
    assert sum(len(issue.context) for issue in result.issues) == 12
    assert all(len(issue.context) == 2 for issue in result.issues)
    assert type(result).model_validate_json(result.model_dump_json()) == result
    assert type(result).model_json_schema()['$defs']['Issue']['properties']['context']['maxItems'] == 2
    assert result.context_slots() == 0
    assert p.context_slots() == 2
    next_feedback = source_notes_revision_bundle(b, result.model_dump(mode='json'), failures)['revision_feedback']
    assert next_feedback['context_addition_limit'] == 0
    assert next_feedback['allowed_context_issue_ids'] == []
    with pytest.raises(ValueError, match='context_budget_rejected'):
        apply_source_notes_patch(result, SourceNotesPatch(source_notes=p.source_notes,
            context_additions=[changes[0]]), next_feedback)
    result.issues[0].context.append(original.fact.model_copy(update={'id': 'third-context'}))
    with pytest.raises(ValueError, match='at most 2 items'):
        type(result).model_validate(result.model_dump())


def test_offline_recomposed_draft_keeps_raw_receipt_and_runs_content_validation():
    from scripts import evaluate_briefing

    b, p = notes_case()
    written = response({'request_id': 'original-writer'}, p)
    original = written.model_dump_json()
    result = evaluate_briefing.assess(b, written, derived_proposal=p.model_dump())
    assert result['raw_proposal'] == p.model_dump(mode='json')
    assert not result['rejected'] and not result['source_notes_violations']
    changed = p.model_copy(deep=True)
    changed.issues[0].fact.text = '매출은 999억원으로 증가했다.'
    rejected = evaluate_briefing.assess(b, written, derived_proposal=changed.model_dump())
    assert rejected['rejected']['fact'] == 'unsupported_prose_number'
    assert written.model_dump_json() == original


@pytest.mark.parametrize('fixed', [True, False])
@pytest.mark.parametrize('missing_coverage', [True, False])
def test_combined_mapping_and_content_repair_uses_one_correction_after_review(brief, fixed, missing_coverage):  # noqa: F811
    from quant_company.briefing.contracts import EditorialPatch

    store, clock = brief
    store.company.settings.briefing_publish_enabled = False
    store.company.settings.briefing_source_notes_enabled = True
    store.company.settings.briefing_max_revisions = 1
    edition = seed(brief)
    b, p, _ = failed_case()
    b.update(combined_editorial_repair=True)
    correct_notes = deepcopy(p.source_notes)
    if missing_coverage:
        p.source_notes[0].item_ids = ['absent-item']
        p.source_notes[0].material_facts[0].main_item_ids = ['absent-item']
    with store.db.transaction() as conn:
        conn.execute('UPDATE brief_editions SET bundle=%s WHERE id=%s', (Jsonb(b), edition.id))
    first = store.prepare()
    store.commit(response(first['request'], p))
    critic = store.prepare()
    assert critic['request']['request_id'].endswith('-review')
    assert 'pre_review_source_notes_violations' not in critic['request']['prompt']
    verdict = review()
    if missing_coverage:
        verdict.verdict = 'reduce'
        verdict.checks['coverage'] = False
        verdict.concerns = ['본문에 매출의 이전 수치와 변경된 수치가 빠져 있습니다.']
        verdict.source_assessments = deepcopy(correct_notes)
        verdict.source_assessments[0].material_facts[0].main_item_ids = []
    store.commit(response(critic['request'], verdict))
    correction = store.prepare()
    assert correction['request']['request_id'].endswith('-revise')
    assert correction['request']['output_contract'] in {'brief_editorial_v1', 'brief_editorial_v2'}
    patch = EditorialPatch(edits=[{'id': 'fact', 'text':
        '매출은 100억원에서 120억원으로 증가했다.' if fixed else p.issues[0].fact.text}],
        source_notes=correct_notes if missing_coverage and fixed else None)
    store.commit(response(correction['request'], patch))
    final = store.prepare()
    if not fixed:
        assert final['state'] == 'blocked'
    else:
        assert final['request']['request_id'].endswith('-final_review')
        store.commit(response(final['request'], review()))
        clock['at'] = edition.due_at
        store.flush()
        with store.db.transaction() as conn:
            row = conn.execute('SELECT * FROM brief_editions WHERE id=%s', (edition.id,)).fetchone()
            assert row['state'] == 'previewed' and not row['quality']['reduced']
            assert row['quality']['revision_used']
            assert not row['quality']['source_notes_violations']
    with store.db.transaction() as conn:
        phases = [r['phase'] for r in conn.execute(
            'SELECT phase FROM brief_calls WHERE edition_id=%s ORDER BY requested_at', (edition.id,))]
        assert phases.count('revise') == 1
        assert set(phases) == {'write','review','revise'} | ({'final_review'} if fixed else set())


def test_unresolved_mapping_cannot_publish_full_when_no_repair_time_remains(brief):  # noqa: F811
    store, clock = brief
    store.company.settings.briefing_source_notes_enabled = True
    edition = seed(brief)
    b, p, _ = failed_case()
    b.update(combined_editorial_repair=True)
    with store.db.transaction() as conn:
        conn.execute('UPDATE brief_editions SET bundle=%s WHERE id=%s', (Jsonb(b), edition.id))
    store.commit(response(store.prepare()['request'], p))
    critic = store.prepare()
    clock['at'] = edition.due_at
    store.commit(response(critic['request'], review()))
    with store.db.transaction() as conn:
        row = conn.execute('SELECT * FROM brief_editions WHERE id=%s', (edition.id,)).fetchone()
        assert row['quality']['reduced'] and not row['quality']['substantive']
        assert row['quality']['source_notes_violations']
        assert p.issues[0].fact.text not in row['rendered'][0]
