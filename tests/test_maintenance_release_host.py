import importlib.util
import io
import json
import os
import tarfile
from pathlib import Path
from uuid import uuid4

import pytest

spec = importlib.util.spec_from_file_location('maintenance_release', Path(__file__).parents[1]/'deploy/maintenance_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def archive(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as bundle:
        for path, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo('repository/'+path)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    return stream.getvalue()


def test_release_archive_remains_readable_by_container_user_with_private_umask(tmp_path):
    target = tmp_path/'release'
    original_umask = os.umask(0o077)
    try:
        release.unpack(archive({'deploy/entrypoint.py': 'print("startup")', 'src/package/data.json': '{}'}), target)
    finally:
        os.umask(original_umask)
    # Docker COPY keeps source modes; root-only files break the image's UID 10001 entrypoint.
    assert (target/'deploy/entrypoint.py').stat().st_mode & 0o444 == 0o444
    assert (target/'src/package/data.json').stat().st_mode & 0o444 == 0o444
    assert all(path.stat().st_mode & 0o111 == 0o111 for path in [target, target/'deploy', target/'src', target/'src/package'])


@pytest.mark.parametrize('path', ['../../outside', '/outside'])
def test_archive_traversal_never_writes_outside_release(tmp_path, path):
    data = archive({path: 'injected'})
    if path.startswith('/'):
        # The producer already adds a prefix; explicitly exercise an absolute member.
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as tar:
            member = tarfile.TarInfo(path)
            tar.addfile(member)
        data = stream.getvalue()
    with pytest.raises(ValueError):
        release.unpack(data, tmp_path/'release')
    assert not (tmp_path/'outside').exists()


def test_host_rejects_protected_or_symlink_changes_before_build(tmp_path):
    previous, target = tmp_path/'old', tmp_path/'new'
    for root in [previous, target]:
        (root/'deploy').mkdir(parents=True)
        (root/'deploy/Dockerfile').write_text('original')
    (target/'deploy/Dockerfile').write_text('unapproved root command')
    with pytest.raises(ValueError, match='protected_file'):
        release.validate_tree(previous, target)
    (target/'deploy/Dockerfile').unlink()
    (target/'deploy/Dockerfile').symlink_to(previous/'deploy/Dockerfile')
    with pytest.raises(ValueError, match='symlink'):
        release.validate_tree(previous, target)


def test_host_accepts_procedures_but_rejects_grader_changes(tmp_path):
    previous, target = tmp_path/'old', tmp_path/'new'
    relative = 'src/quant_company/staff/playbooks/data.md'
    for root in [previous, target]:
        (root/relative).parent.mkdir(parents=True)
        (root/relative).write_text('old procedure')
    (target/relative).write_text('reviewed procedure')
    release.validate_tree(previous, target)
    (target/'src/quant_company/staff/cases.py').write_text('altered exam oracle')
    with pytest.raises(ValueError, match='protected_file'):
        release.validate_tree(previous, target)


@pytest.mark.parametrize('name', ['staff/independent_review.py', 'staff/review_contract.py',
                                  'staff/progress.py', 'staff/comparisons.py', 'maintenance/evaluation.py'])
def test_host_cannot_rewrite_evaluator_or_promotion_rules(tmp_path, name):
    previous, target = tmp_path/'old', tmp_path/'new'
    previous.mkdir()
    path = target/'src/quant_company'/name
    path.parent.mkdir(parents=True)
    path.write_text('altered judge')
    with pytest.raises(ValueError, match='protected_file'):
        release.validate_tree(previous, target)


def test_optional_claude_follows_installed_code_and_activation(tmp_path, monkeypatch):
    monkeypatch.setattr(release, 'STATE', tmp_path/'state')
    env = release.STATE/'config/runtime.env'
    env.parent.mkdir(parents=True)
    env.write_text('COMPANY_STAFF_REVIEW_ENABLED=true\n')
    root = tmp_path/'release'
    assert 'claude-runtime' not in release.services(root)
    module = root/'src/quant_company/providers/claude_runtime.py'
    module.parent.mkdir(parents=True)
    module.write_text('qualified runtime')
    assert 'claude-runtime' in release.services(root)
    env.write_text('COMPANY_STAFF_REVIEW_ENABLED=false\n')
    assert 'claude-runtime' not in release.services(root)


def fixture_host(tmp_path, monkeypatch, *, fail_health=False):
    state = tmp_path/'state'
    (state/'config').mkdir(parents=True)
    root = tmp_path/'opt'
    previous = root/'releases'/('a'*40)
    previous.mkdir(parents=True)
    (previous/'qdata').mkdir()
    (previous/'qdata/pinned.py').write_text('original qdata')
    roles = [{'id': 'director', 'model': 'repo-model', 'mission': 'old', 'instructions': 'old'}]
    files = {'src/quant_company/roles.json': json.dumps(roles), 'deploy/Dockerfile': 'trusted',
             'deploy/qdata-source.json': json.dumps({'commit': 'b'*40}), 'src/quant_company/tools.py': 'old code'}
    for name, content in files.items():
        path = previous/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    files['src/quant_company/tools.py'] = 'new code'
    roles[0]['mission'] = 'new mission'
    files['src/quant_company/roles.json'] = json.dumps(roles)
    deployed = [{**roles[0], 'model': 'operator-model', 'mission': 'old'}]
    (state/'config/roles.json').write_text(json.dumps(deployed))
    (state/'config/roles.json').chmod(0o444)  # Root-owned host file is read by UID 10001 containers.
    oldenv = b'RELEASE_COMMIT='+b'a'*40+b'\nCUSTOM_VALUE=keep\n'
    (state/'config/runtime.env').write_bytes(oldenv)
    (state/'config/runtime.env').chmod(0o600)
    current = root/'current'
    current.symlink_to(previous)
    monkeypatch.setattr(release, 'STATE', state)
    monkeypatch.setattr(release, 'CURRENT', current)
    commands, reports, backups = [], [], []
    monkeypatch.setattr(release, 'compose', lambda root, *args, **kw: commands.append((root, args)))
    monkeypatch.setattr(release, 'run', lambda *a, **kw: b'[{"Id":"unchanged-postgres"}]')
    monkeypatch.setattr(release, 'take_backup', lambda *args: backups.append(args))

    def health(commit, postgres):
        assert postgres == 'unchanged-postgres'
        if fail_health and commit == 'c'*40:
            raise ValueError('release_service_unhealthy')
        return {'healthy_services': 7, 'postgres_recreated': False}

    def protocol(action, identity=None, data=None):
        if action == 'archive':
            return archive(files)
        if action == 'activity':
            return {'active': 0, 'outbox': 0}
        assert action == 'finish'
        reports.append(data)
        return data

    monkeypatch.setattr(release, 'health', health)
    monkeypatch.setattr(release.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(release, 'protocol', protocol)
    return state, current, previous, commands, reports, backups, oldenv


@pytest.mark.parametrize('failed', [False, True])
def test_real_files_cutover_or_rollback_preserves_overrides_and_db(tmp_path, monkeypatch, failed):
    state, current, previous, commands, reports, backups, oldenv = fixture_host(tmp_path, monkeypatch, fail_health=failed)
    item = {'id': str(uuid4()), 'commit': 'c'*40}
    release.execute(item)
    assert len(backups) == 1
    assert reports[-1]['state'] == ('rolled_back' if failed else 'complete')
    assert all('postgres' not in args for _, args in commands)
    roles = json.loads((state/'config/roles.json').read_text())
    assert roles[0]['model'] == 'operator-model'
    assert (state/'config/roles.json').stat().st_mode & 0o777 == 0o444
    assert (state/'config/runtime.env').stat().st_mode & 0o777 == 0o600
    assert (state/'releases'/(item['id']+'.roles')).stat().st_mode & 0o777 == 0o600
    if failed:
        assert current.resolve() == previous and (state/'config/runtime.env').read_bytes() == oldenv
        assert roles[0]['mission'] == 'old'
    else:
        assert current.resolve().name == 'c'*40 and roles[0]['mission'] == 'new mission'
    count = len(commands)
    release.execute(item)  # A lost DB acknowledgement is reconciled without a second cutover.
    assert len(commands) == count


def test_interrupted_cutover_recovers_previous_release(tmp_path, monkeypatch):
    state, current, previous, commands, reports, _, oldenv = fixture_host(tmp_path, monkeypatch)
    identity = str(uuid4())
    records = state/'releases'
    records.mkdir()
    (records/(identity+'.env')).write_bytes(oldenv)
    (records/(identity+'.roles')).write_bytes((state/'config/roles.json').read_bytes())
    (records/(identity+'.json')).write_text(json.dumps({'commit': 'c'*40, 'previous': str(previous),
                        'postgres_id': 'unchanged-postgres', 'state': 'cutover'}))
    (state/'config/runtime.env').write_text('interrupted config')
    release.execute({'id': identity, 'commit': 'c'*40})
    assert current.resolve() == previous and (state/'config/runtime.env').read_bytes() == oldenv
    assert reports[-1]['state'] == 'rolled_back'
    assert (state/'config/roles.json').stat().st_mode & 0o777 == 0o444
    assert not any('build' in args for _, args in commands)


def test_new_tool_module_and_registration_pass_both_review_boundaries(tmp_path):
    from quant_company.maintenance.policy import Patch, apply_patch

    role_path = 'src/quant_company/roles.json'
    contract_path = 'src/quant_company/contracts.py'
    module_path = 'src/quant_company/new_capability.py'
    contract = 'from typing import Literal\nclass ToolRequest:\n    name: Literal["calculate"]\n'
    roles = [{'id': 'director', 'active': True, 'model': 'unchanged', 'version': '1',
              'mission': 'Analyze', 'instructions': 'Use evidence', 'tools': ['calculate'], 'can_delegate_to': []}]
    originals = {role_path: json.dumps(roles), contract_path: contract}
    roles[0]['tools'].append('new_capability')
    changes = apply_patch(Patch(summary='Implement a declared tool with a consumer regression', edits=[
        {'path': contract_path, 'old': 'Literal["calculate"]', 'new': 'Literal["calculate", "new_capability"]'},
        {'path': module_path, 'old': '', 'new': 'def run():\n    return {"ok": True}\n'},
        {'path': role_path, 'old': originals[role_path], 'new': json.dumps(roles)},
        {'path': 'tests/test_maintenance_regression_abc.py', 'old': '', 'new': 'def test_new():\n    assert True\n'},
    ]), originals, 'abc', new_paths=[module_path])
    previous, target = tmp_path / 'before', tmp_path / 'after'
    for root, files in [(previous, originals), (target, {**originals, **changes})]:
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    release.validate_tree(previous, target)
    roles[0]['model'] = 'unauthorized-model'
    (target / role_path).write_text(json.dumps(roles))
    with pytest.raises(ValueError, match='release_role_permissions_changed'):
        release.validate_tree(previous, target)
