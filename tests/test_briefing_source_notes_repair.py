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
    assert final['request']['request_id'].endswith('-final_review')
    assert phases == ['final_review', 'revise', 'write']
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
