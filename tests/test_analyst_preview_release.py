import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('analyst_preview_release',
                                             Path(__file__).parents[1] / 'deploy/analyst_preview_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def trees(tmp_path):
    previous, target = tmp_path / ('a' * 40), tmp_path / ('b' * 40)
    for root in (previous, target):
        (root / 'src/quant_company').mkdir(parents=True)
        (root / 'deploy').mkdir()
        (root / 'src/quant_company/roles.json').write_text(json.dumps([{'id': 'director', 'active': True}]))
        (root / 'deploy/qdata-source.json').write_text(json.dumps({'commit': 'c' * 40, 'publicFunctions': []}))
    roles = target / 'src/quant_company/roles.json'
    roles.write_text(json.dumps([{'id': 'director', 'active': True}, {'id': release.ROLE, 'active': False}]))
    manifest = {'previous_commit': previous.name, 'candidate_commit': target.name,
                'runtime_changes': [{'path': 'src/quant_company/roles.json',
                    'before_sha256': release.digest((previous / 'src/quant_company/roles.json').read_bytes()),
                    'after_sha256': release.digest(roles.read_bytes())}]}
    return previous, target, manifest


def test_owner_preview_rejects_unlisted_source_and_changed_existing_role(tmp_path):
    previous, target, manifest = trees(tmp_path)
    assert release.validate_candidate(previous, target, manifest, target.name) == 1
    path = target / 'src/quant_company/hidden.py'
    path.write_text('unexpected change')
    with pytest.raises(ValueError, match='outside_manifest'):
        release.validate_candidate(previous, target, manifest, target.name)
    path.unlink()
    roles = target / 'src/quant_company/roles.json'
    roles.write_text(json.dumps([{'id': 'director', 'active': False}, {'id': release.ROLE, 'active': False}]))
    manifest['runtime_changes'][0]['after_sha256'] = release.digest(roles.read_bytes())
    with pytest.raises(ValueError, match='existing_role_changed'):
        release.validate_candidate(previous, target, manifest, target.name)


def test_owner_preview_preserves_live_roles_and_unrelated_configuration():
    existing = [{'id': 'director', 'active': False, 'tools': ['custom_tool']}]
    proposed = [{'id': 'director', 'active': True}, {'id': release.ROLE, 'active': False}]
    assert release.merged_roles(existing, proposed) == [*existing, {'id': release.ROLE, 'active': True}]
    raw = b'QUANT_FEED_PUBLISH_ENABLED=true\nSLACK_ALLOWED_USERS=["UOWNER"]\nCUSTOM=value\n'
    values = dict.fromkeys(release.SETTINGS, 'scoped')
    values['BRIEFING_PUBLISH_ENABLED'] = 'false'
    result = release.updated(raw, values)
    assert result.startswith(raw)
    values['BRIEFING_PUBLISH_ENABLED'] = 'true'
    with pytest.raises(ValueError, match='configuration_scope'):
        release.updated(raw, values)


def test_busy_model_drain_restores_only_stopped_workers_without_killing_primary(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    (state / 'config').mkdir(parents=True)
    (state / 'codex/jobs').mkdir(parents=True)
    raw = b'SLACK_ALLOWED_CHANNELS=["CEXISTING"]\n'
    roles = b'[{"id":"director","active":true}]'
    (state / 'config/runtime.env').write_bytes(raw)
    (state / 'config/roles.json').write_bytes(roles)
    previous, target, _ = trees(tmp_path)
    journal = state / 'preview.json'
    journal.write_text(json.dumps({'phase': 'staged', 'base': previous.name, 'commit': target.name,
                                  'owner_approval': 'user-preview-approved',
                                  'evaluation_edition': '00000000-0000-0000-0000-000000000000',
                                  'env_sha256': release.digest(raw), 'roles_sha256': release.digest(roles)}))
    args = SimpleNamespace(base=previous.name, commit=target.name, approval='user-preview-approved',
                           channel='CBRIEF', owner='UOWNER', evaluation_edition='00000000-0000-0000-0000-000000000000')
    before = {'/quant-company-' + name + '-1': {'running': True, 'image': 'old-' + name}
              for name in release.SERVICES}
    calls = []
    module = SimpleNamespace(atomic=lambda path, data: path.write_bytes(data), link=lambda path: None)
    monkeypatch.setattr(release, 'STATE', state)
    monkeypatch.setattr(release, 'inventory', lambda: before)
    monkeypatch.setattr(release, 'available_memory', lambda: 2048)
    monkeypatch.setattr(release, 'compose', lambda *a, **kw: calls.append((a[2:], kw)))
    monkeypatch.setattr(release.fcntl, 'flock', lambda *a: (_ for _ in ()).throw(BlockingIOError('model busy')))
    with pytest.raises(BlockingIOError):
        release.cutover(args, previous, target, module, journal)
    assert calls[0][0] == ('stop', '-t', '1100', 'news-worker', 'dispatch')
    assert calls[1][0][-2:] == ('news-worker', 'dispatch')
    assert all('codex-runtime' not in a and 'api' not in a for a, _ in calls)
    assert (state / 'config/runtime.env').read_bytes() == raw
    assert (state / 'config/roles.json').read_bytes() == roles


@pytest.mark.parametrize('name', release.BASE_INPUTS)
def test_code_only_build_never_inherits_changed_base_input(name):
    assert release.code_only_build({'runtime_changes': [{'path': 'src/quant_company/briefing/editor.py'}]})
    assert not release.code_only_build({'runtime_changes': [{'path': name}]})


def test_cutover_rejects_different_evaluation_before_stopping_services(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    (state / 'config').mkdir(parents=True)
    raw, roles = b'', b'[]'
    (state / 'config/runtime.env').write_bytes(raw)
    (state / 'config/roles.json').write_bytes(roles)
    previous, target, _ = trees(tmp_path)
    journal = state / 'preview.json'
    journal.write_text(json.dumps({'phase': 'staged', 'base': previous.name, 'commit': target.name,
                                  'owner_approval': 'user-preview-approved', 'evaluation_edition': 'old',
                                  'env_sha256': release.digest(raw), 'roles_sha256': release.digest(roles)}))
    args = SimpleNamespace(base=previous.name, commit=target.name, approval='user-preview-approved',
                           evaluation_edition='new')
    monkeypatch.setattr(release, 'STATE', state)
    with pytest.raises(ValueError, match='staged_configuration_changed'):
        release.cutover(args, previous, target, SimpleNamespace(), journal)
