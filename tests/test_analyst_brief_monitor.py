import json
from datetime import timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from .test_briefing import brief, definition, seed  # noqa: F401

spec = spec_from_file_location('analyst_monitor', Path(__file__).parents[1]/'deploy/analyst_brief_monitor.py')
monitor = module_from_spec(spec)
spec.loader.exec_module(monitor)


def completed():
    d = definition().model_dump(mode='json')
    row = {'state': 'previewed', 'publish': False, 'policy_digest': 'fixture',
           'verdict': 'publish', 'checks': dict.fromkeys(monitor.CHECKS, True),
           'substantive': True, 'reduced': False, 'rejections': {}, 'inventory_digest': 'fixture',
           'committed_at': d['due_at'], 'calls': [
               {'phase': p, 'state': 'completed', 'provider': 'codex',
                'requested_at': d['cutoff'], 'completed_at': d['due_at']}
               for p in ('plan', 'inventory', 'write', 'review')]}
    return d, row, definition().due_at+timedelta(minutes=13)


def test_monitor_distinguishes_preview_quality_failure_and_pending_schedule():
    d, row, now = completed()
    good = monitor.assess(d, row, now)
    assert good['content_passed'] and good['delivery'] == 'preview_only' and not good['problems']
    row['checks']['numbers'] = False
    bad = monitor.assess(d, row, now)
    assert not bad['content_passed'] and bad['failed_checks'] == ['numbers']
    assert 'content_review_failed' in bad['problems']
    assert monitor.assess(d, None, definition().starts_at-timedelta(minutes=1))['problems'] == []
    assert monitor.assess(d, None, now)['problems'] == ['edition_not_registered']


def test_monitor_never_certifies_missing_inventory_or_fake_provider_and_flags_delivery():
    d, row, now = completed()
    row['calls'][1]['provider'] = 'fixture'
    row['publish'] = True
    row['expected_messages'] = 3
    row['delivered_messages'] = 1
    row['committed_at'] = (now-timedelta(minutes=1)).isoformat()
    result = monitor.assess(d, row, now)
    assert not result['content_passed']
    assert {'full_content_not_qualified', 'slack_delivery_incomplete', 'late_brief'} <= set(result['problems'])
    with pytest.raises(ValueError):
        monitor.query(["bad'); DROP TABLE brief_editions; --"])


def test_real_postgres_monitor_reads_only_metadata_and_does_not_create_a_call(brief):  # noqa: F811
    store, clock = brief
    d = seed(brief)
    with store.db.transaction() as conn:
        result = conn.execute(monitor.query([d.id])).fetchone()
        rows = next(iter(result.values()))
        assert len(rows) == 1 and rows[0]['calls'] == []
        assert not {'bundle', 'proposal', 'request', 'prompt', 'rendered'} & rows[0].keys()
        assert conn.execute('SELECT count(*) AS n FROM brief_calls').fetchone()['n'] == 0
    assert monitor.assess(d.model_dump(mode='json'), rows[0], clock['at'])['model_requests'] == 0


def test_continuous_monitor_uses_installed_calendar_and_keeps_missing_expected_editions(monkeypatch):
    d, _, now = completed()
    earlier = {**d, 'id': '00000000-0000-0000-0000-000000000000',
               'due_at': (definition().due_at-timedelta(days=1)).isoformat()}
    policy = {'mode': 'continuous', 'channel': 'C1', 'owner': 'U1',
              'starts_at': (definition().due_at-timedelta(hours=1)).isoformat()}
    calls = []
    def docker(command, **kwargs):
        calls.append((command, kwargs))
        if command[1] == 'inspect':
            return json.dumps([{'Config': {'Image': 'installed-calendar-image',
                'Env': ['BRIEFING_CHANNEL_ID=C1', 'BRIEFING_OWNER_USER=U1',
                        'BRIEFING_CALENDAR_OVERRIDES_FILE=']}}]).encode()
        assert '--network=none' in command and '--read-only' in command
        assert command[command.index('--entrypoint=python')+1] == 'installed-calendar-image'
        assert set(json.loads(kwargs['input'])) == {'now', 'channel', 'owner', 'overrides'}
        return json.dumps([earlier, d]).encode()
    monkeypatch.setattr(monitor.subprocess, 'check_output', docker)
    expected = monitor.definitions(policy, now)
    assert expected == [d] and len(calls) == 2
    assert monitor.assess(expected[0], None, now)['problems'] == ['edition_not_registered']


def test_continuous_monitor_accounts_for_optional_discovery_without_certifying_bad_content():
    d, row, now = completed()
    row['calls'].extend({'phase': p, 'state': 'completed', 'provider': 'codex'}
                        for p in ('search', 'revise', 'final_review'))
    row['checks']['coverage'] = False
    result = monitor.assess(d, row, now, maximum_requests=7)
    assert 'model_request_budget_exceeded' not in result['problems']
    assert not result['content_passed'] and result['failed_checks'] == ['coverage']
    row['calls'].append({'phase': 'search', 'state': 'completed', 'provider': 'codex'})
    assert 'model_request_budget_exceeded' in monitor.assess(d, row, now, maximum_requests=7)['problems']
