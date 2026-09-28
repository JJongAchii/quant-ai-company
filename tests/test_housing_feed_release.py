import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def release():
    path = Path(__file__).parents[1] / 'deploy/housing_feed_release.py'
    spec = importlib.util.spec_from_file_location('housing_operator_release', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_destination_updates_preserve_other_feeds_and_require_owner(release):
    raw = b'QUANT_FEED_PUBLISH_ENABLED=false\nSLACK_ALLOWED_CHANNELS=["CNEWS"]\nSLACK_ALLOWED_USERS=["UOWNER"]\n'
    args = SimpleNamespace(commit='abc', channel='CHOUSING', owner='UOWNER')
    values = release.updates(args, release.configuration(raw))
    result = release.configuration(release.updated(raw, values))
    assert result['QUANT_FEED_PUBLISH_ENABLED'] == 'false'
    assert json.loads(result['SLACK_ALLOWED_CHANNELS']) == ['CNEWS', 'CHOUSING']
    assert result['HOUSING_FEED_PUBLISH_ENABLED'] == 'false'
    args.owner = 'UOTHER'
    with pytest.raises(ValueError, match='owner_not_authorized'):
        release.updates(args, release.configuration(raw))


def test_cutover_failure_closes_pending_housing_effects_before_restoring_dispatch(release, tmp_path, monkeypatch):
    (tmp_path / 'config').mkdir()
    envfile = tmp_path / 'config/runtime.env'
    raw = b'SLACK_TEAM_ID=TTEAM\nQUANT_FEED_PUBLISH_ENABLED=false\n'
    envfile.write_bytes(raw)
    monkeypatch.setattr(release, 'STATE', tmp_path)
    before = {'/quant-company-worker-1': {'id': 'original', 'running': True}}
    monkeypatch.setattr(release, 'inventory', lambda: before)
    monkeypatch.setattr(release, 'slack_access', lambda *args: {'verified': True})
    args = SimpleNamespace(base='old', commit='new', channel='CHOUSING', owner='UOWNER')
    journal = tmp_path / 'receipt.json'
    journal.write_text(json.dumps({'phase': 'staged', 'base': 'old', 'commit': 'new',
                                  'env_sha256': hashlib.sha256(raw).hexdigest(),
                                  'updates': {'HOUSING_FEED_CHANNEL_ID': 'CHOUSING',
                                              'HOUSING_FEED_OWNER_USER': 'UOWNER'}}))
    previous, target = tmp_path / 'old', tmp_path / 'new'
    actions = []

    def compose(root, *command, **kwargs):
        actions.append((root, command))
        if command == ('up', '-d', '--no-deps', 'housing-feed-worker'):
            raise RuntimeError('simulated_worker_start_failure')

    def oneoff(module, root, env, code):
        actions.append(('sql', code))

    monkeypatch.setattr(release, 'oneoff', oneoff)
    module = SimpleNamespace(compose=compose, atomic=lambda path, data: path.write_bytes(data), link=Mock())
    with pytest.raises(RuntimeError, match='simulated_worker_start_failure'):
        release.activate(args, previous, target, module, journal)
    assert envfile.read_bytes() == raw
    assert json.loads(journal.read_text())['phase'] == 'rolled_back'
    assert module.link.call_args.args == (previous,)
    assert actions[-1] == (previous, ('up', '-d', '--no-deps', 'dispatch'))
    assert actions[-3] == (target, ('stop', '-t', '30', 'housing-feed-worker', 'dispatch'))
    assert 'uncertain' in actions[-2][1] and 'housing_release_rollback' in actions[-2][1]
    assert all('worker' not in action[1] for action in actions if action[0] != 'sql')
