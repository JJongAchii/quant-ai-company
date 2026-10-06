import importlib.util
import json
import os
import stat
from copy import deepcopy
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


def test_live_inventory_survives_staged_json_receipt_and_still_detects_drift(monkeypatch):
    row = {'Name': '/quant-company-news-worker-1', 'Id': 'container', 'RestartCount': 0,
           'State': {'Running': True, 'OOMKilled': False}, 'Config': {'Image': 'verified-image'},
           'Mounts': [{'Source': '/state/roles', 'Destination': '/etc/roles', 'RW': False}]}

    def run(command, **kwargs):
        return (b'quant-company-news-worker-1\n' if command[1] == 'ps'
                else json.dumps([row]).encode())

    monkeypatch.setattr(release, 'run', run)
    staged = json.loads(json.dumps(release.inventory()))
    assert release.inventory() == staged
    row['Mounts'][0]['RW'] = True
    assert release.inventory() != staged


def test_profile_schema_run_uses_an_already_verified_image_without_replacing_api():
    receipt = {'source_profiles': [{'services': ['news-worker']}],
               'images': [{'target': 'app', 'tag': 'verified-profile', 'source_verified': True,
                           'services': ['news-worker', 'dispatch']}]}
    assert release.schema_service_overlay(receipt) == {'services': {'api': {'image': 'verified-profile'}}}
    receipt['images'][0]['source_verified'] = False
    with pytest.raises(ValueError, match='schema_image_not_verified'):
        release.schema_service_overlay(receipt)
    assert release.schema_service_overlay({'source_profiles': []}) is None


def test_only_canonical_owned_analyst_can_receive_permission_correction():
    director = {'id': 'director', 'active': True, 'tools': ['custom_tool']}
    prior = {'id': release.ROLE, 'active': False, 'tools': ['read_source', 'briefing_status']}
    proposed = {**prior, 'tools': ['read_source']}
    existing = [director, {**prior, 'active': True}]
    assert release.merged_roles(existing, [director, proposed], [director, prior]) == [
        director, {**proposed, 'active': True}]
    custom = [director, {**prior, 'active': True, 'mission': 'owner customized'}]
    with pytest.raises(ValueError, match='configured_differently'):
        release.merged_roles(custom, [director, proposed], [director, prior])


@pytest.mark.parametrize('preserve_runtime', [False, True])
def test_busy_model_drain_restores_only_stopped_workers_without_killing_primary(tmp_path, monkeypatch, preserve_runtime):
    state = tmp_path / 'state'
    (state / 'config').mkdir(parents=True)
    (state / 'codex/jobs').mkdir(parents=True)
    raw = b'SLACK_ALLOWED_CHANNELS=["CEXISTING"]\nPINNED_CODEX_RUNTIME_IMAGE=old-codex-runtime\n'
    roles = b'[{"id":"director","active":true}]'
    (state / 'config/runtime.env').write_bytes(raw)
    (state / 'config/roles.json').write_bytes(roles)
    previous, target, _ = trees(tmp_path)
    journal = state / 'preview.json'
    journal.write_text(json.dumps({'phase': 'staged', 'base': previous.name, 'commit': target.name,
                                  'owner_approval': 'user-preview-approved',
                                  'evaluation_edition': '00000000-0000-0000-0000-000000000000',
                                  'preserve_codex_runtime': preserve_runtime,
                                  'env_sha256': release.digest(raw), 'roles_sha256': release.digest(roles)}))
    args = SimpleNamespace(base=previous.name, commit=target.name, approval='user-preview-approved',
                           channel='CBRIEF', owner='UOWNER', evaluation_edition='00000000-0000-0000-0000-000000000000')
    before = {'/quant-company-' + name + '-1': {'running': True, 'image': 'old-' + name}
              for name in release.SERVICES}
    before['/quant-company-' + release.NEW_SERVICE + '-1'] = {'running': True, 'image': 'owned-older-data-image'}
    calls = []
    module = SimpleNamespace(atomic=lambda path, data: path.write_bytes(data), link=lambda path: None)
    monkeypatch.setattr(release, 'STATE', state)
    monkeypatch.setattr(release, 'inventory', lambda: before)
    monkeypatch.setattr(release, 'available_memory', lambda: 2048)
    monkeypatch.setattr(release, 'compose', lambda *a, **kw: calls.append((a[2:], kw)))
    monkeypatch.setattr(release.fcntl, 'flock', lambda *a: (_ for _ in ()).throw(BlockingIOError('model busy')))
    with pytest.raises(BlockingIOError):
        release.cutover(args, previous, target, module, journal)
    assert calls[0][0] == ('stop', '-t', '1100', 'news-worker', 'dispatch', release.NEW_SERVICE)
    assert calls[1][0][-3:] == ('news-worker', 'dispatch', release.NEW_SERVICE)
    overlay = json.loads(journal.with_suffix('.rollback.compose.json').read_text())
    assert overlay['services'][release.NEW_SERVICE]['image'] == 'owned-older-data-image'
    assert ('codex-runtime' in overlay['services']) is not preserve_runtime
    assert all('codex-runtime' not in a and 'api' not in a for a, _ in calls)
    assert (state / 'config/runtime.env').read_bytes() == raw
    assert (state / 'config/roles.json').read_bytes() == roles


def test_preserved_runtime_rejects_provider_or_dependency_changes_and_checks_its_inventory():
    manifest = {'preserve_codex_runtime': True, 'runtime_changes': [
        {'path': 'src/quant_company/briefing/quotations.py'}]}
    release.validate_preserved_runtime(manifest)
    assert release.selected_services(manifest) == ('api', 'news-worker', 'dispatch')
    for name in ('uv.lock', 'src/quant_company/providers/codex_runner.py', 'src/quant_company/contracts.py'):
        with pytest.raises(ValueError, match='preserved_runtime_incompatible'):
            release.validate_preserved_runtime({**manifest, 'runtime_changes': [{'path': name}]})
    with pytest.raises(ValueError, match='preserved_runtime_mode'):
        release.validate_preserved_runtime({**manifest, 'preserve_codex_runtime': 'false'})
    for invalid in ({**manifest, 'preserve_api': 'false'},
                    {**manifest, 'preserve_api': True, 'preserve_codex_runtime': False}):
        with pytest.raises(ValueError, match='preserved_api_mode'):
            release.validate_preserved_runtime(invalid)
    assert release.selected_services({**manifest, 'preserve_api': True}) == ('news-worker', 'dispatch')
    before = {'/quant-company-codex-runtime-1': {'id': 'unchanged', 'image': 'pinned'}}
    release.preserved(before, deepcopy_inventory := {k: dict(v) for k, v in before.items()}, release.selected_services(manifest))
    deepcopy_inventory['/quant-company-codex-runtime-1']['id'] = 'replaced'
    with pytest.raises(ValueError, match='unrelated_service_changed'):
        release.preserved(before, deepcopy_inventory, release.selected_services(manifest))


def test_installed_source_profiles_preserve_other_features_and_reject_undeclared_changes(tmp_path, monkeypatch):
    root = 'runtime-source/'+('a'*12)+'/src'
    source = tmp_path/root
    (source/'quant_company/briefing').mkdir(parents=True)
    (source/'quant_company/company.py').write_text('preserved company')
    (source/'quant_company/briefing/store.py').write_text('new analyst')
    prior = {'quant_company/company.py':release.digest(b'preserved company'),
             'quant_company/briefing/store.py':release.digest(b'old analyst')}
    profile = {'base_image_id':'sha256:'+('a'*64), 'base_image_tag':'installed-overlay',
               'source_root':root,'services':['news-worker','dispatch',release.NEW_SERVICE],
               'before':prior,'after':release.source_inventory(source),
               'changed':['quant_company/briefing/store.py']}
    manifest = {'preserve_api':True,'preserve_codex_runtime':True,'source_profiles':[profile],
                'runtime_changes':[{'path':'src/quant_company/briefing/store.py'}]}
    before = {'/quant-company-'+name+'-1':{'image':'installed-overlay'} for name in profile['services']}
    monkeypatch.setattr(release,'run',lambda *a,**kw: json.dumps([{'Id':profile['base_image_id']}]).encode())
    monkeypatch.setattr(release,'image_sources',lambda image: prior)
    assert release.validate_source_profiles(tmp_path,manifest,before) == [profile]
    (source/'quant_company/company.py').write_text('unexpected replacement')
    profile['after'] = release.source_inventory(source)
    profile['changed'].append('quant_company/company.py')
    with pytest.raises(ValueError,match='outside_analyst_scope'):
        release.validate_source_profiles(tmp_path,manifest,before)
    (source/'quant_company/company.py').write_text('preserved company')
    profile['after'] = release.source_inventory(source)
    profile['changed'].remove('quant_company/company.py')
    profile['services'].append('news-worker')
    with pytest.raises(ValueError,match='incomplete_or_duplicate'):
        release.validate_source_profiles(tmp_path,manifest,before)
    profile['services'].pop()
    before['/quant-company-news-worker-1']['image'] = 'later-overlay'
    with pytest.raises(ValueError,match='base_changed'):
        release.validate_source_profiles(tmp_path,manifest,before)


def test_inherited_model_overlay_needs_active_matching_root_receipts(tmp_path,monkeypatch):
    (tmp_path/'releases').mkdir()
    monkeypatch.setattr(release,'STATE',tmp_path)
    owned = {'phase':'preview_active','owner_approval':'owner-preview',
             'images':[{'target':'app','id':'base'}]}
    image = {'Id':'derived','Config':{'Labels':{'io.quant-company.model-assignments.base-id':'base',
                                              'io.quant-company.model-assignments':'commit'}}}
    assert not release.owned_model_overlay(image,owned,'owner-preview')
    stage = {'state':'staged','commit':'commit','images':{'base':{'id':'derived','services':[release.NEW_SERVICE]}}}
    cutover = {'state':'active','commit':'commit'}
    (tmp_path/'releases/model-assignments-20261006-stage.json').write_text(json.dumps(stage))
    path = tmp_path/'releases/model-assignments-20261006-cutover.json'
    path.write_text(json.dumps(cutover))
    assert release.owned_model_overlay(image,owned,'owner-preview')
    assert not release.owned_model_overlay(image,owned,'another-approval')
    path.write_text(json.dumps({**cutover,'state':'reconciliation_required'}))
    assert not release.owned_model_overlay(image,owned,'owner-preview')


def test_runtime_overlay_keeps_existing_feature_environment_and_reversible_runtime():
    row = {'Config':{'Env':['MODEL_ASSIGNMENTS_ENABLED=true','TREND_FEED_ENABLED=true',
                           'BRIEFING_PUBLISH_ENABLED=false','COMPANY_CODE_COMMIT=old'],
                     'Cmd':['quant-company','run-dispatch'],'Entrypoint':['python','/app/entrypoint.py'],
                     'User':'10001:10001','WorkingDir':'/opt/company'},
           'Mounts':[{'Type':'bind','Source':'/state/config','Destination':'/etc/company','RW':False}]}
    rows = {'/quant-company-dispatch-1':row}
    settings = {'RELEASE_COMMIT':'new','BRIEFING_PUBLISH_ENABLED':'false','BRIEFING_EVALUATION_EDITION_IDS':'["case"]'}
    overlay = release.runtime_overlay(rows,{'dispatch':'new-image'},settings)['services']['dispatch']
    assert overlay['environment'] == {'MODEL_ASSIGNMENTS_ENABLED':'true','TREND_FEED_ENABLED':'true',
                                    'BRIEFING_PUBLISH_ENABLED':'false','COMPANY_CODE_COMMIT':'new',
                                    'BRIEFING_EVALUATION_EDITION_IDS':'["case"]'}
    assert overlay['command'] == row['Config']['Cmd'] and overlay['entrypoint'] == row['Config']['Entrypoint']
    assert overlay['volumes'] == [{'type':'bind','source':'/state/config','target':'/etc/company','read_only':True}]
    rollback = release.runtime_overlay(rows,{'dispatch':'old-image'}, {})['services']['dispatch']
    assert rollback['environment'] == release.configuration(('\n'.join(row['Config']['Env'])+'\n').encode())
    after = deepcopy(rows)
    after['/quant-company-dispatch-1']['Config']['Env'] = [k+'='+v for k,v in overlay['environment'].items()]
    release.verify_profile_runtime(rows,after,{'dispatch':'new-image'},settings)
    after['/quant-company-dispatch-1']['Config']['Env'][0] = 'MODEL_ASSIGNMENTS_ENABLED=false'
    with pytest.raises(ValueError,match='existing_runtime_binding_changed'):
        release.verify_profile_runtime(rows,after,{'dispatch':'new-image'},settings)


@pytest.mark.parametrize('preserve_api', [False, True])
def test_worker_only_cutover_keeps_a_busy_primary_runtime_and_its_image(tmp_path, monkeypatch, preserve_api):
    state = tmp_path/'state'
    (state/'config').mkdir(parents=True)
    (state/'codex/jobs').mkdir(parents=True)
    raw = b'SLACK_ALLOWED_CHANNELS=["CEXISTING"]\nPINNED_CODEX_RUNTIME_IMAGE=old-codex-runtime\n'
    roles = b'[{"id":"director","active":true}]'
    (state/'config/runtime.env').write_bytes(raw)
    (state/'config/roles.json').write_bytes(roles)
    previous, target, _ = trees(tmp_path)
    journal = state/'preview.json'
    journal.write_text(json.dumps({'phase': 'staged', 'base': previous.name, 'commit': target.name,
        'owner_approval': 'approved', 'evaluation_edition': 'case', 'preserve_codex_runtime': True,
        'preserve_api': preserve_api,
        'env_sha256': release.digest(raw), 'roles_sha256': release.digest(roles)}))
    args = SimpleNamespace(base=previous.name, commit=target.name, approval='approved',
                           channel='CBRIEF', owner='UOWNER', evaluation_edition='case')
    before = {'/quant-company-'+name+'-1': {'running': True, 'image': 'old-'+name,
              'restarts': 0, 'oom': False, 'id': 'old-id-'+name, 'mounts': ['preserved']}
              for name in (*release.SERVICES, release.NEW_SERVICE)}
    after = {name: dict(row) for name, row in before.items()}
    calls = []

    def compose(module, root, *command, **kwargs):
        calls.append(command)
        if root == target and command[:2] == ('up', '-d'):
            for service in (*release.selected_services({'preserve_codex_runtime': True, 'preserve_api': preserve_api}), release.NEW_SERVICE):
                after['/quant-company-'+service+'-1'].update(image='quant-company:'+target.name, id='new-'+service)

    def dump(command, **kwargs):
        kwargs['stdout'].write(b'simulated PostgreSQL backup')

    module = SimpleNamespace(atomic=lambda path, data: path.write_bytes(data), link=lambda path: None)
    monkeypatch.setattr(release, 'STATE', state)
    monkeypatch.setattr(release, 'inventory', lambda: {name: dict(row) for name, row in after.items()})
    monkeypatch.setattr(release, 'available_memory', lambda: 2048)
    monkeypatch.setattr(release, 'compose', compose)
    monkeypatch.setattr(release, 'run', lambda *a, **kw: b'{"sending":0}')
    monkeypatch.setattr(release.subprocess, 'run', dump)
    with (state/'codex/jobs/.runtime.lock').open('a') as busy:
        release.fcntl.flock(busy, release.fcntl.LOCK_EX | release.fcntl.LOCK_NB)
        result = release.cutover(args, previous, target, module, journal)
    assert result['phase'] == 'preview_active'
    assert all('codex-runtime' not in command for command in calls)
    assert after['/quant-company-codex-runtime-1'] == before['/quant-company-codex-runtime-1']
    assert release.configuration((state/'config/runtime.env').read_bytes())['PINNED_CODEX_RUNTIME_IMAGE'] == 'old-codex-runtime'
    expected = {'news-worker', 'dispatch', release.NEW_SERVICE}
    if preserve_api:
        assert after['/quant-company-api-1'] == before['/quant-company-api-1']
        assert all('api' not in command for command in calls if command[0] in {'stop', 'up'})
        assert all(len(command) > 4 for command in calls if command[0] == 'stop')
    else:
        expected.add('api')
    assert set(result['selected_services']) == expected


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


def test_new_drain_lock_uses_runtime_owner_and_keeps_exclusive_capacity(tmp_path, monkeypatch):
    path = tmp_path / '.runtime-brief.lock'
    changes = []
    real_chown = release.os.fchown

    def chown(fd, uid, gid):
        changes.append((uid, gid))
        real_chown(fd, uid, gid)

    monkeypatch.setattr(release.os, 'fchown', chown)
    with release.runtime_lock(path) as drained:
        owner = tmp_path.stat()
        assert changes == [(owner.st_uid, owner.st_gid)]
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        release.fcntl.flock(drained, release.fcntl.LOCK_EX | release.fcntl.LOCK_NB)
        fd = os.open(path, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                release.fcntl.flock(fd, release.fcntl.LOCK_EX | release.fcntl.LOCK_NB)
        finally:
            os.close(fd)
    # Match the runtime's real open/flock sequence after the cutover releases it.
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        release.fcntl.flock(fd, release.fcntl.LOCK_EX | release.fcntl.LOCK_NB)
    finally:
        os.close(fd)


def test_existing_drain_lock_preserves_owner_mode_inode_and_contents(tmp_path, monkeypatch):
    path = tmp_path / '.runtime-brief.lock'
    path.write_bytes(b'existing lock')
    path.chmod(0o640)
    before = path.stat()
    monkeypatch.setattr(release.os, 'fchown', lambda *a: pytest.fail('Existing lock ownership changed'))
    with release.runtime_lock(path):
        pass
    after = path.stat()
    assert (before.st_ino, before.st_uid, before.st_gid, before.st_mode) == (
        after.st_ino, after.st_uid, after.st_gid, after.st_mode)
    assert path.read_bytes() == b'existing lock'


def test_drain_lock_rejects_symlink_without_touching_target(tmp_path):
    target = tmp_path / 'protected'
    target.write_bytes(b'unchanged')
    link = tmp_path / '.runtime-brief.lock'
    link.symlink_to(target)
    with pytest.raises(OSError):
        release.runtime_lock(link)
    assert target.read_bytes() == b'unchanged'
