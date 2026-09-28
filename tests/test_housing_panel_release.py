import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def operator():
    path = Path(__file__).parents[1] / 'deploy/housing_panel_release.py'
    spec = importlib.util.spec_from_file_location('housing_panel_operator_release', path)
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    return release


def test_failed_panel_cutover_restores_socket_and_feed_without_replaying_sends(tmp_path, monkeypatch):
    release = operator()
    monkeypatch.setattr(release, 'STATE', tmp_path)
    (tmp_path / 'config').mkdir()
    raw = (b'HOUSING_FEED_ENABLED=true\nHOUSING_FEED_PUBLISH_ENABLED=true\n'
           b'HOUSING_FEED_CHANNEL_ID=CHOUSING\nHOUSING_FEED_OWNER_USER=UOWNER\nSLACK_TEAM_ID=TTEAM\n')
    envfile = tmp_path / 'config/runtime.env'
    envfile.write_bytes(raw)
    previous, target = tmp_path / 'old', tmp_path / 'new'
    original = {f'/quant-company-{name}-1': {'running': True, 'image': 'quant-company:old',
                                              'oom': False, 'restarts': 0}
                for name in release.SERVICES}
    journal = tmp_path / 'journal.json'
    journal.write_text(json.dumps({'phase': 'staged', 'base': 'old', 'commit': 'new',
                                   'env_sha256': hashlib.sha256(raw).hexdigest(),
                                   'selected_before': original}))
    args = SimpleNamespace(base='old', commit='new', channel='CHOUSING', owner='UOWNER')
    actions = []

    def compose(root, *command, **kwargs):
        actions.append(('compose', root, command))
        if root == target and command[:1] == ('up',):
            raise RuntimeError('synthetic_start_failure')

    base = SimpleNamespace(configuration=lambda data: dict(
                               line.split('=', 1) for line in data.decode().splitlines()),
                           slack_access=lambda *args: {'ok': True}, inventory=lambda: original,
                           updated=lambda data, updates: data + b'RELEASE_COMMIT=new\n',
                           oneoff=lambda *args: actions.append(('sql', args[-1])))
    helper = SimpleNamespace(compose=compose, atomic=lambda path, data: path.write_bytes(data),
                             link=lambda root: actions.append(('link', root)))

    with pytest.raises(RuntimeError, match='synthetic_start_failure'):
        release.activate(args, previous, target, base, helper, journal)
    assert envfile.read_bytes() == raw
    assert json.loads(journal.read_text())['phase'] == 'rolled_back'
    assert ('link', previous) in actions
    assert actions[-1] == ('compose', previous, ('up', '-d', '--no-deps', *release.SERVICES))
    sql = next(item[1] for item in actions if item[0] == 'sql')
    assert "status='uncertain'" in sql and "status='stale'" in sql


def test_panel_cutover_preserves_unrelated_services():
    release = operator()
    before = {'/quant-company-slack-socket-1': {'id': 'old'},
              '/quant-company-dispatch-1': {'id': 'old'},
              '/quant-company-housing-feed-worker-1': {'id': 'old'},
              '/quant-company-postgres-1': {'id': 'stable'}}
    after = {**before, '/quant-company-slack-socket-1': {'id': 'new'},
             '/quant-company-dispatch-1': {'id': 'new'},
             '/quant-company-housing-feed-worker-1': {'id': 'new'}}
    release.preserved(before, after)
    after['/quant-company-postgres-1'] = {'id': 'wrong'}
    with pytest.raises(ValueError, match='unrelated_service_changed'):
        release.preserved(before, after)
