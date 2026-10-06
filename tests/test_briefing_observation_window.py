from datetime import timedelta

import pytest

from quant_company.config import Settings

from .test_briefing import brief, definition, response, review, seed  # noqa: F401


def test_observation_window_advances_to_next_frozen_edition_then_stops(brief):  # noqa: F811
    store, clock = brief
    first = definition()
    second = definition(day=first.day+timedelta(days=1))
    excluded = definition(day=second.day+timedelta(days=1))
    store.company.settings.briefing_evaluation_edition_ids = [first.id, second.id]
    store.company.settings.briefing_max_revisions = 0
    store.company.settings.briefing_publish_enabled = False
    for edition in (first, second):
        seed(brief, edition)
        store.commit(response(store.prepare()['request']))
        store.commit(response(store.prepare()['request'], review()))
        clock['at'] = edition.due_at
        store.flush()
    seed(brief, excluded)
    assert store.prepare() == {'state': 'idle'}
    store.flush()
    store.flush()
    with store.db.transaction() as conn:
        calls = conn.execute('SELECT edition_id,phase FROM brief_calls ORDER BY edition_id,phase').fetchall()
        assert {str(c['edition_id']) for c in calls} == {first.id, second.id}
        assert len(calls) == 4 and {c['phase'] for c in calls} == {'write', 'review'}
        assert conn.execute('SELECT count(*) AS n FROM outbox').fetchone()['n'] == 0
        reports = conn.execute("SELECT detail FROM events WHERE kind='briefing_observation_finished'").fetchall()
        assert len(reports) == 1
        assert reports[0]['detail']['qualification']['required_days'] == 5
        assert not reports[0]['detail']['qualification']['ready_for_slack_acceptance']
        assert not reports[0]['detail']['automatic_publication_changed']


@pytest.mark.parametrize('values', [
    {'briefing_evaluation_edition_ids': ['bad']},
    {'briefing_evaluation_edition_ids': ['00000000-0000-0000-0000-000000000000']*2},
    {'briefing_evaluation_edition_ids': ['00000000-0000-0000-0000-000000000000'],
     'briefing_evaluation_edition_id': '00000000-0000-0000-0000-000000000001'},
    {'briefing_evaluation_edition_ids': ['00000000-0000-0000-0000-000000000000'],
     'briefing_max_revisions': 1},
])
def test_observation_scope_cannot_be_ambiguous_or_open_ended(values):
    with pytest.raises(ValueError):
        Settings(**{'briefing_max_revisions': 0, **values})
