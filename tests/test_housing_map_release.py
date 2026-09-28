import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_failed_map_cutover_restores_existing_feed_without_discarding_pending(tmp_path, monkeypatch):
    path = Path(__file__).parents[1] / 'deploy/housing_map_release.py'
    spec = importlib.util.spec_from_file_location('housing_map_operator_release', path)
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    monkeypatch.setattr(release, 'STATE', tmp_path)
    (tmp_path / 'config').mkdir()
    raw = (b'HOUSING_FEED_ENABLED=true\nHOUSING_FEED_PUBLISH_ENABLED=true\n'
           b'HOUSING_FEED_CHANNEL_ID=CHOUSING\nHOUSING_FEED_OWNER_USER=UOWNER\nSLACK_TEAM_ID=TTEAM\n')
    envfile = tmp_path / 'config/runtime.env'
    envfile.write_bytes(raw)
    journal = tmp_path / 'journal.json'
    journal.write_text(json.dumps({'phase': 'staged', 'base': 'old', 'commit': 'new',
                                   'env_sha256': hashlib.sha256(raw).hexdigest()}))
    args = SimpleNamespace(base='old', commit='new', channel='CHOUSING', owner='UOWNER')
    previous, target = tmp_path / 'old', tmp_path / 'new'
    actions = []
    original = {f'/quant-company-{name}-1': {'running': True, 'image': 'quant-company:old'}
                for name in ('dispatch', 'housing-feed-worker')}

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
    assert actions[-1] == ('compose', previous, ('up', '-d', '--no-deps', 'dispatch', 'housing-feed-worker'))
    assert "status='uncertain'" in next(item[1] for item in actions if item[0] == 'sql')
    assert "status='pending'" not in next(item[1] for item in actions if item[0] == 'sql')
