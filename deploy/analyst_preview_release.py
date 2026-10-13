#!/usr/bin/env python3
"""Owner-authorized Analyst preview only; never enables Slack publication.

Stage exact committed build inputs first. Cutover requires the staged receipt,
unchanged installed source/configuration, drained effects, and durable backups.
This operator path does not change the installed autonomous release policy.
"""

import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

STATE = Path('/var/lib/quant-company')
CURRENT = Path('/opt/quant-company/current')
SERVICES = ('api', 'news-worker', 'dispatch', 'codex-runtime')
NEW_SERVICE = 'briefing-data-worker'
ROLE = 'market_brief'
SETTINGS = {'RELEASE_COMMIT', 'QDATA_BUILD_CONTEXT', 'PINNED_CODEX_RUNTIME_IMAGE',
            'BRIEFING_ENABLED', 'BRIEFING_PUBLISH_ENABLED', 'BRIEFING_CHANNEL_ID',
            'BRIEFING_OWNER_USER', 'SLACK_ALLOWED_CHANNELS', 'BRIEFING_SOURCE_NOTES_ENABLED',
            'BRIEFING_MAX_REVISIONS', 'BRIEFING_EVALUATION_EDITION_ID', 'BRIEFING_EVALUATION_EDITION_IDS'}
BASE_INPUTS = ('pyproject.toml', 'uv.lock', 'deploy/qdata-source.json', 'deploy/Dockerfile', 'deploy/entrypoint.py')


def code_only_build(manifest):
    return not any(row['path'] in BASE_INPUTS for row in manifest['runtime_changes'])


def selected_services(receipt):
    return tuple(name for name in SERVICES
                 if not (name == 'codex-runtime' and receipt.get('preserve_codex_runtime'))
                 and not (name == 'api' and receipt.get('preserve_api')))


def execution_runtime_update(manifest):
    scope = manifest.get('execution_runtime_update')
    if scope is None:
        return False
    if (not isinstance(scope, dict) or set(scope) != {'owner_approval_id', 'approved', 'cli_version', 'scope'}
            or scope.get('approved') is not True or not isinstance(scope.get('owner_approval_id'), str)
            or not re.fullmatch(r'chat-analyst-execution-approved-[0-9]{8}', scope['owner_approval_id'])
            or scope['scope'] != 'Analyst typed output and phase deadlines; preserve all other runtime behavior'
            or scope['cli_version'] != '0.160.1'
            or manifest.get('preserve_api') is not True or manifest.get('preserve_codex_runtime') is not False
            or not code_only_build(manifest)):
        raise ValueError('preview_execution_update_requires_separate_owner_approval')
    permitted = {'deploy/analyst_preview_release.py', 'deploy/Dockerfile.analyst-code-preview', 'src/quant_company/contracts.py',
                 'src/quant_company/providers/client.py', 'src/quant_company/providers/codex_runner.py'}
    if any(row['path'] not in permitted and not row['path'].startswith('src/quant_company/briefing/')
           for row in manifest['runtime_changes']):
        raise ValueError('preview_execution_update_outside_scope')
    if manifest.get('profile_dependency_paths', []) not in ([], ['src/quant_company/briefing/contracts.py']):
        raise ValueError('preview_execution_dependency_outside_scope')
    return True


def validate_preserved_runtime(manifest):
    if execution_runtime_update(manifest):
        return
    if not isinstance(manifest.get('preserve_codex_runtime', False), bool):
        raise ValueError('preview_preserved_runtime_mode')
    if (not isinstance(manifest.get('preserve_api', False), bool)
            or (manifest.get('preserve_api') and not manifest.get('preserve_codex_runtime'))):
        raise ValueError('preview_preserved_api_mode')
    if not manifest.get('preserve_codex_runtime'):
        return
    permitted = {'deploy/.env.example', 'deploy/Dockerfile.analyst-code-preview',
                 'deploy/analyst_preview_release.py', 'deploy/compose.yaml',
                 'src/quant_company/config.py', 'src/quant_company/roles.json',
                 'src/quant_company/slack.py'}
    if not code_only_build(manifest) or any(
            row['path'] not in permitted and not row['path'].startswith('src/quant_company/briefing/')
            for row in manifest['runtime_changes']):
        raise ValueError('preview_preserved_runtime_incompatible_source')


def run(command, **kwargs):
    return subprocess.run(command, check=True, capture_output=True, timeout=1800, **kwargs).stdout


def digest(data):
    return hashlib.sha256(data).hexdigest()


def helper(root):
    spec = importlib.util.spec_from_file_location('analyst_release_helper', root / 'deploy/maintenance_release.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configuration(data):
    return dict(line.split('=', 1) for line in data.decode().splitlines()
                if '=' in line and not line.lstrip().startswith('#'))


def evaluation_editions(args):
    path = getattr(args, 'evaluation_editions_file', None)
    values = json.loads(path.read_text()) if path else []
    if (not isinstance(values, list) or len(values) > 12
            or any(not isinstance(value, str) or not re.fullmatch(
                r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value) for value in values)
            or len(set(values)) != len(values)
            or (path and (not values or args.evaluation_edition not in values))):
        raise ValueError('preview_invalid_evaluation_editions')
    return sorted(values)


def updated(data, values):
    if set(values) != SETTINGS or values['BRIEFING_PUBLISH_ENABLED'] != 'false':
        raise ValueError('preview_configuration_scope')
    lines = [line for line in data.decode().splitlines() if line.split('=', 1)[0] not in values]
    return ('\n'.join(lines + [key + '=' + value for key, value in values.items()]) + '\n').encode()


def merged_roles(existing, proposed, prior=None):
    candidate = next(r for r in proposed if r['id'] == ROLE)
    old = next((r for r in existing if r['id'] == ROLE), None)
    if old == {**candidate, 'active': True}:
        return existing
    canonical_prior = next((r for r in (prior or []) if r['id'] == ROLE), None)
    if old is not None and (canonical_prior is None or old != {**canonical_prior, 'active': True}):
        raise ValueError('analyst_already_configured_differently')
    if old:
        return [{**candidate, 'active': True} if r['id'] == ROLE else r for r in existing]
    return [*existing, {**candidate, 'active': True}]


def validate_candidate(previous, target, manifest, commit):
    if manifest['candidate_commit'] != commit or manifest['previous_commit'] != previous.name:
        raise ValueError('preview_source_identity')
    allowed = {r['path']: r for r in manifest['runtime_changes']}
    roots = ('src', 'deploy', 'slack-apps')
    paths = set()
    for base in (previous, target):
        for name in roots:
            paths.update(str(p.relative_to(base)) for p in (base / name).rglob('*')
                         if p.is_file() and '__pycache__' not in p.parts)
        paths.update(name for name in ('pyproject.toml', 'uv.lock', 'README.md', 'AGENTS.md')
                     if (base / name).exists())
    changed = set()
    for name in paths:
        old, new = previous / name, target / name
        if old.is_symlink() or new.is_symlink():
            raise ValueError('preview_source_symlink')
        if not new.is_file():
            raise ValueError('preview_source_deleted')
        if old.exists() and old.read_bytes() == new.read_bytes():
            continue
        row = allowed.get(name)
        if not row or digest(new.read_bytes()) != row['after_sha256']:
            raise ValueError('preview_source_outside_manifest')
        if old.exists() and digest(old.read_bytes()) != row['before_sha256']:
            raise ValueError('preview_base_source_changed')
        changed.add(name)
    if changed != set(allowed):
        raise ValueError('preview_manifest_changed_paths')
    before = {r['id']: r for r in json.loads((previous / 'src/quant_company/roles.json').read_text())}
    after = {r['id']: r for r in json.loads((target / 'src/quant_company/roles.json').read_text())}
    if set(after) != set(before) | {ROLE} or any(after[key] != value for key, value in before.items() if key != ROLE):
        raise ValueError('preview_existing_role_changed')
    old_pin = json.loads((previous / 'deploy/qdata-source.json').read_text())
    new_pin = json.loads((target / 'deploy/qdata-source.json').read_text())
    if {k: v for k, v in old_pin.items() if k != 'publicFunctions'} != {
            k: v for k, v in new_pin.items() if k != 'publicFunctions'}:
        raise ValueError('preview_qdata_archive_changed')
    return len(changed)


def inventory():
    names = run(['docker', 'ps', '-a', '--filter', 'name=quant-company-', '--format', '{{.Names}}']).decode().splitlines()
    return {r['Name']: {'id': r['Id'], 'running': r['State']['Running'], 'restarts': r['RestartCount'],
                        'oom': r['State']['OOMKilled'], 'image': r['Config']['Image'],
                        'mounts': sorted([m['Source'], m['Destination'], m['RW']] for m in r['Mounts'])}
            for r in json.loads(run(['docker', 'inspect', *names]))}


def preserved(before, after, services=SERVICES):
    selected = {'/quant-company-' + name + '-1' for name in (*services, NEW_SERVICE)}
    if any(after.get(name) != value for name, value in before.items() if name not in selected):
        raise ValueError('preview_unrelated_service_changed')


def source_inventory(root):
    if any(p.is_symlink() for p in root.rglob('*')):
        raise ValueError('preview_runtime_source_symlink')
    return {str(p.relative_to(root)): digest(p.read_bytes())
            for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}


def image_sources(image):
    code = """import importlib.util,json,pathlib,hashlib
root=pathlib.Path(importlib.util.find_spec('quant_company').origin).parent
print(json.dumps({'quant_company/'+str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}))"""
    return json.loads(run(['docker','run','--rm','--network=none','--memory=128m',
                          '--entrypoint','python',image,'-c',code]))


def validate_source_profiles(target, manifest, before):
    profiles = manifest.get('source_profiles', [])
    if not profiles:
        return []
    execution_update = execution_runtime_update(manifest)
    if not manifest.get('preserve_api') or not (manifest.get('preserve_codex_runtime') or execution_update):
        raise ValueError('preview_source_profiles_require_preserved_primary')
    permitted = {r['path'].removeprefix('src/') for r in manifest['runtime_changes']
                 if r['path'].startswith('src/quant_company/')}
    if execution_update:
        permitted.update(p.removeprefix('src/') for p in manifest.get('profile_dependency_paths', []))
    services = []
    for profile in profiles:
        if (not re.fullmatch(r'sha256:[0-9a-f]{64}', profile['base_image_id'])
                or profile['source_root'] != 'runtime-source/'+profile['base_image_id'][7:19]+'/src'
                or not profile['services']):
            raise ValueError('preview_source_profile_identity')
        if execution_update and (profile.get('package_parent') not in {
                '/opt/company/src', '/app/.venv/lib/python3.12/site-packages'}
                or ('codex-runtime' in profile['services'] and profile['services'] != ['codex-runtime'])):
            raise ValueError('preview_execution_package_identity')
        for name in profile['services']:
            if name not in ('news-worker','dispatch',NEW_SERVICE, *(('codex-runtime',) if execution_update else ())):
                raise ValueError('preview_source_profile_service')
            current = before['/quant-company-'+name+'-1']
            if current['image'] != profile['base_image_tag']:
                raise ValueError('preview_source_profile_base_changed')
        actual_image = json.loads(run(['docker','image','inspect',profile['base_image_tag']]))[0]
        if (actual_image['Id'] != profile['base_image_id']
                or image_sources(profile['base_image_id']) != profile['before']):
            raise ValueError('preview_source_profile_inventory_changed')
        actual = source_inventory(target/profile['source_root'])
        if actual != profile['after'] or not set(profile['before']) <= set(actual):
            raise ValueError('preview_source_profile_manifest')
        changed = {name for name,value in actual.items() if profile['before'].get(name) != value}
        if changed != set(profile['changed']) or not changed <= permitted:
            raise ValueError('preview_source_profile_outside_analyst_scope')
        for path in manifest.get('profile_dependency_paths', []):
            if (target/path).read_bytes() != (target/profile['source_root']/path.removeprefix('src/')).read_bytes():
                raise ValueError('preview_execution_dependency_changed')
        services.extend(profile['services'])
    expected = ('news-worker','dispatch',NEW_SERVICE, *(('codex-runtime',) if execution_update else ()))
    if sorted(services) != sorted(expected):
        raise ValueError('preview_source_profiles_incomplete_or_duplicate')
    return profiles


def owned_model_overlay(image, owned, approval):
    """Verify the later installed overlay against its root-owned effect receipts."""
    labels = image['Config']['Labels']
    base = labels.get('io.quant-company.model-assignments.base-id')
    commit = labels.get('io.quant-company.model-assignments')
    if (owned.get('phase') != 'preview_active' or owned.get('owner_approval') != approval
            or not any(r['target'] == 'app' and r['id'] == base for r in owned.get('images', []))):
        return False
    stage_path = STATE/'releases/model-assignments-20261006-stage.json'
    cutover_path = STATE/'releases/model-assignments-20261006-cutover.json'
    if not stage_path.is_file() or not cutover_path.is_file():
        return False
    stage, cutover = json.loads(stage_path.read_text()), json.loads(cutover_path.read_text())
    row = stage.get('images', {}).get(base, {})
    return bool(stage.get('state') == 'staged' and cutover.get('state') == 'active'
                and stage.get('commit') == cutover.get('commit') == commit
                and row.get('id') == image['Id'] and NEW_SERVICE in row.get('services', []))


def owned_data_worker(image, owned, manifest, approval, base):
    """A new runtime approval must explicitly name the preview it replaces."""
    prior_approval = manifest.get('previous_owner_approval_id', approval)
    if prior_approval != approval:
        if (not execution_runtime_update(manifest)
                or approval != manifest['execution_runtime_update']['owner_approval_id']
                or not isinstance(prior_approval, str)
                or not re.fullmatch(r'chat-analyst-preview-approved-[0-9]{8}', prior_approval)
                or owned.get('commit') != base or manifest.get('previous_commit') != base):
            return False, False
    original = (owned.get('phase') == 'preview_active' and owned.get('owner_approval') == prior_approval
                and any(row['target'] == 'app' and row['id'] == image['Id'] for row in owned.get('images', [])))
    return original, owned_model_overlay(image, owned, prior_approval)


def service_images(receipt):
    return {name: row['tag'] for row in receipt.get('images', []) for name in row.get('services', [])}


def schema_service_overlay(receipt):
    if not receipt.get('source_profiles'):
        return None
    image = next((r for r in receipt.get('images', []) if 'news-worker' in r.get('services', [])), None)
    if not image or image.get('target') != 'app' or image.get('source_verified') is not True:
        raise ValueError('preview_schema_image_not_verified')
    return {'services': {'api': {'image': image['tag']}}}


def sending_effects(values):
    # Do not load a second Python runtime inside the memory-limited live API.
    command = ['docker', 'exec', 'quant-company-postgres-1', 'psql', '-U', 'postgres',
               '-d', values.get('DATABASE_NAME', 'quant_company'), '-X', '-Atc',
               "SELECT json_build_object('sending',count(*)) FROM outbox WHERE status='sending'"]
    return json.loads(run(command))


def runtime_overlay(rows, images, settings):
    result = {}
    secrets = {}
    for name, image in images.items():
        row = rows['/quant-company-'+name+'-1']
        env = configuration(('\n'.join(row['Config']['Env'])+'\n').encode())
        env.update({k:v for k,v in settings.items() if k in env or k.startswith('BRIEFING_')})
        if settings:
            env['COMPANY_CODE_COMMIT'] = settings['RELEASE_COMMIT']
        result[name] = {'image':image, 'environment':env, 'command':row['Config']['Cmd'],
                        'entrypoint':row['Config']['Entrypoint'], 'user':row['Config']['User'],
                        'working_dir':row['Config']['WorkingDir'],
                        'volumes':[{'type':m['Type'], 'source':m.get('Name',m['Source']) if m['Type']=='volume' else m['Source'],
                                    'target':m['Destination'], 'read_only':not m['RW']}
                                   for m in row['Mounts'] if m['Type'] in {'bind','volume'}
                                   and not m['Destination'].startswith('/run/secrets/')]}
        preserved_secrets = []
        for mount in row['Mounts']:
            if not mount['Destination'].startswith('/run/secrets/'):
                continue
            if mount['Type'] != 'bind' or mount['RW'] or not re.fullmatch(r'/run/secrets/[^/]+', mount['Destination']):
                raise ValueError('preview_existing_secret_mount_unsupported')
            identity = 'preserved_' + digest((mount['Source']+'\0'+mount['Destination']).encode())[:20]
            secrets[identity] = {'file':mount['Source']}
            preserved_secrets.append({'source':identity, 'target':mount['Destination']})
        if preserved_secrets:
            result[name]['secrets'] = preserved_secrets
        host = row.get('HostConfig', {})
        for field,key in (('Memory','mem_limit'),('MemorySwap','memswap_limit'),('PidsLimit','pids_limit')):
            if host.get(field) is not None:
                result[name][key] = host[field]
        if 'NanoCpus' in host:
            result[name]['cpus'] = host['NanoCpus']/1000000000
    return {'services':result, **({'secrets':secrets} if secrets else {})}


def verify_profile_runtime(before, after, images, settings):
    expected = runtime_overlay(before, images, settings)['services']
    def mounts(row):
        return sorted((m['Type'],m['Source'],m['Destination'],m['RW']) for m in row['Mounts'])
    for name,value in expected.items():
        old, new = before['/quant-company-'+name+'-1'], after['/quant-company-'+name+'-1']
        env = configuration(('\n'.join(new['Config']['Env'])+'\n').encode())
        fields = ('Cmd','Entrypoint','User','WorkingDir')
        if (env != value['environment'] or any(new['Config'][key] != old['Config'][key] for key in fields)
                or mounts(new) != mounts(old)
                or any(new.get('HostConfig',{}).get(key) != old.get('HostConfig',{}).get(key)
                       for key in ('Memory','MemorySwap','NanoCpus','PidsLimit','ReadonlyRootfs','CapDrop',
                                   'SecurityOpt','RestartPolicy','PortBindings','Init','Privileged','NetworkMode'))):
            raise ValueError('preview_existing_runtime_binding_changed:' + name)


def compose(module, root, *args, env=None, overlay=None):
    command = [*module.compose_command(root), '--profile', 'briefing']
    values = configuration((STATE / 'config/runtime.env').read_bytes())
    if values.get('DATA_WATCH_ENABLED') == 'true':
        command += ['--profile', 'data-watch', '-f', str(root / 'deploy/data-watch.compose.yaml')]
    if overlay:
        command += ['-f', str(overlay)]
    return run([*command, *args], env=env)


def available_memory():
    values = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    return int(values['MemAvailable'].split()[0]) // 1024


def runtime_lock(path):
    """A root cutover must not create a lock the non-root model cannot open."""
    flags = os.O_RDWR | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
    except FileExistsError:
        fd, created = os.open(path, flags), False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('preview_runtime_lock_not_regular')
        if created:
            owner = path.parent.stat()
            os.fchown(fd, owner.st_uid, owner.st_gid)
        return os.fdopen(fd, 'a')
    except Exception:
        os.close(fd)
        raise


def stage(args, previous, target, module, journal):
    if target.exists() or journal.exists() or available_memory() < 640:
        raise ValueError('preview_stage_exists_or_memory_admission')
    data = args.archive.read_bytes()
    manifest = json.loads(args.manifest.read_text())
    if digest(data) != args.sha256 or digest(args.manifest.read_bytes()) != args.manifest_sha256:
        raise ValueError('preview_artifact_digest')
    code_only = code_only_build(manifest)
    validate_preserved_runtime(manifest)
    if execution_runtime_update(manifest) and args.approval != manifest['execution_runtime_update']['owner_approval_id']:
        raise ValueError('preview_execution_owner_approval_mismatch')
    minimum_disk = (768 * 1024**2) if code_only else (2 * 1024**3)
    if shutil.disk_usage(target.parent).free < minimum_disk:
        raise ValueError('preview_disk_admission')
    raw = (STATE / 'config/runtime.env').read_bytes()
    values = configuration(raw)
    if args.owner not in json.loads(values['SLACK_ALLOWED_USERS']):
        raise ValueError('preview_owner_not_admitted')
    credentials = json.loads((STATE / 'secrets/slack-credentials.json').read_text())
    if not credentials.get(ROLE, {}).get('bot_token'):
        raise ValueError('preview_analyst_credentials_missing')
    before = inventory()
    if '/quant-company-' + NEW_SERVICE + '-1' in before:
        worker = before['/quant-company-' + NEW_SERVICE + '-1']
        image = json.loads(run(['docker', 'image', 'inspect', worker['image']]))[0]
        revision = image['Config']['Labels'].get('org.opencontainers.image.revision', '')
        if not re.fullmatch('[0-9a-f]{40}', revision):
            raise ValueError('preview_data_worker_revision_missing')
        prior = STATE / 'releases' / ('analyst-preview-' + revision + '.json')
        owned = json.loads(prior.read_text()) if prior.is_file() else {}
        original, inherited = owned_data_worker(image, owned, manifest, args.approval, args.base)
        if (not (original or inherited)
                or values.get('BRIEFING_ENABLED') != 'true' or values.get('BRIEFING_PUBLISH_ENABLED') != 'false'):
            raise ValueError('preview_data_worker_not_owned')
        if inherited and not original and not manifest.get('source_profiles'):
            raise ValueError('preview_installed_overlay_requires_source_profiles')
    receipt = {'phase': 'staging', 'base': args.base, 'commit': args.commit,
               'owner_approval': args.approval, 'publication_enabled': False,
               'archive_sha256': args.sha256, 'manifest_sha256': args.manifest_sha256,
               'env_sha256': digest(raw), 'roles_sha256': digest((STATE / 'config/roles.json').read_bytes()),
               'started_at': time.time(), 'images': [], 'evaluation_edition': args.evaluation_edition,
               'evaluation_editions': evaluation_editions(args),
               'max_revisions': getattr(args, 'max_revisions', 0),
               'build_mode': 'code_only_identical_lock' if code_only else 'locked_dependency_sync',
               'minimum_disk_bytes': minimum_disk, 'service_inventory_before': before}
    receipt['preserve_codex_runtime'] = bool(manifest.get('preserve_codex_runtime'))
    receipt['preserve_api'] = bool(manifest.get('preserve_api'))
    receipt['execution_runtime_update'] = manifest.get('execution_runtime_update')
    receipt['previous_owner_approval_id'] = manifest.get('previous_owner_approval_id')
    module.atomic(journal, json.dumps(receipt).encode())
    target.mkdir()
    module.unpack(data, target)
    receipt['source_changes'] = validate_candidate(previous, target, manifest, args.commit)
    profiles = validate_source_profiles(target, manifest, before)
    receipt['source_profiles'] = profiles
    if code_only and any((previous / name).read_bytes() != (target / name).read_bytes() for name in BASE_INPUTS):
        raise ValueError('preview_code_only_base_identity_changed')
    qdata = Path(values['QDATA_BUILD_CONTEXT']).resolve()
    if not qdata.is_dir() or any(p.is_symlink() for p in qdata.rglob('*')):
        raise ValueError('preview_qdata_context_invalid')
    shutil.copytree(qdata, target / 'qdata')
    try:
        # The source-only build inherits the installed environment only when
        # every dependency/build input is byte-identical. Otherwise sync the
        # candidate lock in the normal preview build.
        for name in ('deploy/Dockerfile', 'deploy/entrypoint.py'):
            if (previous / name).read_bytes() != (target / name).read_bytes():
                raise ValueError('preview_base_runtime_changed')
        builds = [('codex' if profile['services'] == ['codex-runtime'] else 'app',
                   'quant-company-codex' if profile['services'] == ['codex-runtime'] else 'quant-company', profile)
                  for profile in profiles] if profiles else [('app','quant-company',None)]
        if not receipt['preserve_codex_runtime'] and not any(p['services'] == ['codex-runtime'] for p in profiles):
            builds.append(('codex', 'quant-company-codex', None))
        for position, (build_target, repository, profile) in enumerate(builds):
            tag = repository + ':' + args.commit + ('-profile'+str(position) if profile else '')
            if subprocess.run(['docker', 'image', 'inspect', tag], capture_output=True).returncode == 0:
                raise ValueError('preview_image_tag_already_exists')
            service = 'api' if build_target == 'app' else 'codex-runtime'
            base_tag = profile['base_image_tag'] if profile else before['/quant-company-' + service + '-1']['image']
            base_image = json.loads(run(['docker', 'image', 'inspect', base_tag]))[0]
            if base_image['Config']['Labels'].get('org.quant-company.qdata-revision') != values['QDATA_COMMIT']:
                raise ValueError('preview_base_qdata_revision_mismatch')
            command = ['docker', 'build', '--memory=512m', '--memory-swap=512m', '--cpu-quota=100000',
                       '--build-arg', 'BASE_IMAGE=' + base_tag, '--build-arg', 'RELEASE_COMMIT=' + args.commit,
                       '--build-arg', 'SOURCE_DIR='+profile['source_root'] if profile else 'SOURCE_DIR=src',
                       '--build-arg', 'SOURCE_DEST='+profile.get('package_parent', '/opt/company/src') if profile else 'SOURCE_DEST=/opt/company/src',
                       '-f', str(target / ('deploy/Dockerfile.analyst-code-preview' if code_only
                                           else 'deploy/Dockerfile.analyst-preview')), '-t', tag, str(target)]
            log = journal.with_name(journal.stem + '-' + build_target + '-'+str(position)+'.log')
            with log.open('wb') as output:
                subprocess.run(command, check=True, stdout=output, stderr=subprocess.STDOUT, timeout=1800,
                               env={**os.environ, 'DOCKER_BUILDKIT': '1'})
            expected = profile['after'] if profile else {str(p.relative_to(target / 'src')): digest(p.read_bytes())
                        for p in (target / 'src/quant_company').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
            code = """import hashlib,importlib.util,json,pathlib
root=pathlib.Path(importlib.util.find_spec('quant_company').origin).parent
print(json.dumps({'quant_company/'+str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}))"""
            actual = json.loads(run(['docker', 'run', '--rm', '--network=none', '--memory=128m',
                                    '--entrypoint', 'python', tag, '-c', code]))
            if actual != expected:
                raise ValueError('preview_image_source_mismatch')
            if json.loads(run(['docker', 'image', 'inspect', base_tag]))[0]['Id'] != base_image['Id']:
                raise ValueError('preview_base_image_changed')
            verify_dependencies = """import exchange_calendars,importlib.metadata,json,os
print(json.dumps({'calendar':importlib.metadata.version('exchange-calendars'),
'qdata_commit':os.environ.get('QDATA_CODE_COMMIT')}))"""
            dependencies = json.loads(run(['docker', 'run', '--rm', '--network=none', '--memory=128m',
                                           '--entrypoint', 'python', tag, '-c', verify_dependencies]))
            if dependencies['qdata_commit'] != values['QDATA_COMMIT']:
                raise ValueError('preview_image_qdata_revision_changed')
            image = json.loads(run(['docker', 'image', 'inspect', tag]))[0]
            receipt['images'].append({'target': build_target, 'id': image['Id'], 'source_verified': True,
                                      'tag': tag, 'services': profile['services'] if profile else [],
                                      'base_image_id': base_image['Id'], 'base_image_preserved': True,
                                      'locked_dependencies': dependencies})
            module.atomic(journal, json.dumps(receipt).encode())
        receipt['service_inventory_after'] = inventory()
        preserved(before, receipt['service_inventory_after'], selected_services(receipt))
        receipt.update(phase='staged', staged_at=time.time(), running_services_unchanged=True)
    except Exception as exc:
        receipt.update(phase='stage_failed', error=type(exc).__name__)
        raise
    finally:
        module.atomic(journal, json.dumps(receipt).encode())
    return receipt


def cutover(args, previous, target, module, journal):
    receipt = json.loads(journal.read_text())
    services = selected_services(receipt)
    raw = (STATE / 'config/runtime.env').read_bytes()
    roles = (STATE / 'config/roles.json').read_bytes()
    if (receipt['phase'] != 'staged' or receipt['base'] != args.base or receipt['commit'] != args.commit
            or receipt['owner_approval'] != args.approval
            or receipt.get('evaluation_edition') != args.evaluation_edition
            or receipt.get('evaluation_editions', []) != evaluation_editions(args)
            or receipt.get('max_revisions', 0) != getattr(args, 'max_revisions', 0)
            or digest(raw) != receipt['env_sha256']
            or digest(roles) != receipt['roles_sha256']):
        raise ValueError('preview_staged_configuration_changed')
    values = configuration(raw)
    channels = json.loads(values['SLACK_ALLOWED_CHANNELS'])
    settings = {'RELEASE_COMMIT': args.commit, 'QDATA_BUILD_CONTEXT': str(target / 'qdata'),
                'PINNED_CODEX_RUNTIME_IMAGE': (values['PINNED_CODEX_RUNTIME_IMAGE']
                    if receipt.get('preserve_codex_runtime') else service_images(receipt).get('codex-runtime')
                    or 'quant-company-codex:' + args.commit),
                'BRIEFING_ENABLED': 'true', 'BRIEFING_PUBLISH_ENABLED': 'false',
                'BRIEFING_SOURCE_NOTES_ENABLED': 'true', 'BRIEFING_MAX_REVISIONS': str(getattr(args, 'max_revisions', 0)),
                'BRIEFING_EVALUATION_EDITION_ID': '' if evaluation_editions(args) else args.evaluation_edition,
                'BRIEFING_EVALUATION_EDITION_IDS': json.dumps(evaluation_editions(args)),
                'BRIEFING_CHANNEL_ID': args.channel, 'BRIEFING_OWNER_USER': args.owner,
                'SLACK_ALLOWED_CHANNELS': json.dumps([*channels, *([] if args.channel in channels else [args.channel])])}
    new_roles = merged_roles(json.loads(roles), json.loads((target / 'src/quant_company/roles.json').read_text()),
                             json.loads((previous / 'src/quant_company/roles.json').read_text()))
    before = inventory()
    runtime_rows = None
    if receipt.get('source_profiles'):
        names = (*services,NEW_SERVICE)
        if any(before['/quant-company-'+name+'-1'] != receipt['service_inventory_after']['/quant-company-'+name+'-1']
               for name in names):
            raise ValueError('preview_staged_services_changed')
        runtime_rows = {r['Name']:r for r in json.loads(run(['docker','inspect',
                        *['quant-company-'+name+'-1' for name in names]]))}
        module.atomic(journal.with_suffix('.runtime-before.json'), json.dumps(runtime_rows).encode())
    drains = ('news-worker', 'dispatch', *((NEW_SERVICE,) if '/quant-company-' + NEW_SERVICE + '-1' in before else ()))
    if any(not before['/quant-company-' + name + '-1']['running'] for name in SERVICES):
        raise ValueError('preview_required_service_not_running')
    if available_memory() < 384:
        raise ValueError('preview_cutover_memory_admission')
    # Private host-only backups; no credential content is returned in receipts.
    module.atomic(journal.with_suffix('.env'), raw)
    module.atomic(journal.with_suffix('.roles'), roles)
    receipt.update(phase='draining', drain_started_at=time.time())
    module.atomic(journal, json.dumps(receipt).encode())
    locks = []
    primary_stopped = False
    switched = False
    try:
        compose(module, previous, 'stop', '-t', '1100', *drains)
        drain_locks = (('.runtime-brief.lock',) if receipt.get('preserve_codex_runtime') else
                       ('.runtime.lock', '.runtime-news.lock', '.runtime-brief.lock', '.runtime-quant.lock'))
        for name in drain_locks:
            lock = runtime_lock(STATE / 'codex/jobs' / name)
            locks.append(lock)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        effects = sending_effects(values)
        if effects['sending']:
            raise ValueError('preview_external_effect_not_drained')
        backup = journal.with_suffix('.postgres.dump')
        with backup.open('xb') as output:
            backup.chmod(0o600)
            subprocess.run(['docker', 'exec', 'quant-company-postgres-1', 'pg_dump', '-U', 'postgres',
                            '-d', values.get('DATABASE_NAME', 'quant_company'), '-Fc'],
                           check=True, stdout=output, stderr=subprocess.PIPE, timeout=300)
        receipt['database_backup'] = {'file': backup.name, 'bytes': backup.stat().st_size,
                                      'sha256': digest(backup.read_bytes())}
        env = {**os.environ, **settings}
        schema = """from importlib.resources import files
from quant_company.config import Settings
from quant_company.db import Database
with Database(Settings().database_url).transaction() as conn:
 conn.execute('SELECT pg_advisory_xact_lock(71350219)')
 conn.execute(files('quant_company.briefing').joinpath('schema.sql').read_text())
print('briefing_schema_ready')"""
        schema_overlay = None
        if schema_service_overlay(receipt) is not None:
            schema_overlay = journal.with_suffix('.schema.compose.json')
            module.atomic(schema_overlay, json.dumps(schema_service_overlay(receipt)).encode())
        compose(module, target, 'run', '--rm', '--no-deps', '-T', 'api', 'python', '-c', schema,
                env=env, overlay=schema_overlay)
        receipt.update(phase='cutover_started', cutover_started_at=time.time())
        module.atomic(journal, json.dumps(receipt).encode())
        primary = tuple(name for name in ('api', 'codex-runtime') if name in services)
        if primary:
            primary_stopped = True
            compose(module, previous, 'stop', '-t', '360', *primary)
        module.atomic(STATE / 'config/runtime.env', updated(raw, settings))
        module.atomic(STATE / 'config/roles.json', json.dumps(new_roles, ensure_ascii=False).encode())
        module.link(target)
        switched = True
        live_overlay = None
        if runtime_rows:
            live_overlay = journal.with_suffix('.runtime.compose.json')
            module.atomic(live_overlay, json.dumps(runtime_overlay(runtime_rows, service_images(receipt), settings)).encode())
        compose(module, target, 'up', '-d', '--no-deps', '--force-recreate', '--wait', '--wait-timeout', '120',
                *services, NEW_SERVICE, overlay=live_overlay)
        after = inventory()
        preserved(before, after, services)
        if runtime_rows:
            raw_after = {r['Name']:r for r in json.loads(run(['docker','inspect',
                         *['quant-company-'+name+'-1' for name in service_images(receipt)]]))}
            module.atomic(journal.with_suffix('.runtime-after.json'), json.dumps(raw_after).encode())
            verify_profile_runtime(runtime_rows, raw_after, service_images(receipt), settings)
            receipt['existing_feature_environment_and_runtime_preserved'] = True
        for name in (*services, NEW_SERVICE):
            row = after['/quant-company-' + name + '-1']
            if not row['running'] or row['oom'] or row['restarts']:
                raise ValueError('preview_service_unhealthy')
            expected = service_images(receipt).get(name) or ('quant-company-codex:' if name == 'codex-runtime' else 'quant-company:') + args.commit
            if row['image'] != expected:
                raise ValueError('preview_image_revision_mismatch')
            if name == 'codex-runtime' and row['mounts'] != before['/quant-company-codex-runtime-1']['mounts']:
                raise ValueError('preview_model_volumes_changed')
        receipt.update(phase='preview_active', activated_at=time.time(), publication_enabled=False,
                       unrelated_services_preserved=True, selected_services={name: after['/quant-company-' + name + '-1']
                                                                           for name in (*services, NEW_SERVICE)},
                       followup_delivery='not qualified on preserved generic worker/socket',
                       quality_and_slack_acceptance='separate; not certified by installation')
        module.atomic(journal, json.dumps(receipt).encode())
        return receipt
    except Exception as exc:
        if switched:
            with contextlib.suppress(Exception):
                compose(module, target, 'stop', '-t', '1100', *services, NEW_SERVICE)
        module.atomic(STATE / 'config/runtime.env', raw)
        module.atomic(STATE / 'config/roles.json', roles)
        module.link(previous)
        overlay = journal.with_suffix('.rollback.compose.json')
        rollback = (runtime_overlay(runtime_rows, {name:before['/quant-company-'+name+'-1']['image']
                    for name in (*services,NEW_SERVICE)}, {}) if runtime_rows else {'services': {
                        name:{'image':before['/quant-company-'+name+'-1']['image']}
                        for name in (*services,NEW_SERVICE) if '/quant-company-'+name+'-1' in before}})
        module.atomic(overlay, json.dumps(rollback).encode())
        restore = (*services, *((NEW_SERVICE,) if NEW_SERVICE in drains else ())) if primary_stopped else drains
        compose(module, previous, 'up', '-d', '--no-deps', '--force-recreate', '--wait', '--wait-timeout', '120',
                *restore, overlay=overlay)
        receipt.update(phase='rolled_back', error=type(exc).__name__, publication_enabled=False)
        module.atomic(journal, json.dumps(receipt).encode())
        raise
    finally:
        for lock in locks:
            lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['stage', 'cutover'])
    for name in ('base', 'commit', 'approval', 'channel', 'owner'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--manifest-sha256')
    parser.add_argument('--evaluation-edition', required=True)
    parser.add_argument('--evaluation-editions-file', type=Path)
    parser.add_argument('--max-revisions', type=int, choices=(0, 1), default=0)
    args = parser.parse_args()
    if not all(re.fullmatch('[0-9a-f]{40}', value) for value in (args.base, args.commit)):
        raise ValueError('preview_invalid_revision')
    if not re.fullmatch('[CG][A-Z0-9]+', args.channel) or not re.fullmatch('U[A-Z0-9]+', args.owner):
        raise ValueError('preview_invalid_destination')
    if not re.fullmatch('[a-zA-Z0-9_-]{8,120}', args.approval):
        raise ValueError('preview_explicit_owner_approval_required')
    if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', args.evaluation_edition):
        raise ValueError('preview_evaluation_edition_required')
    evaluation_editions(args)
    previous, target = CURRENT.resolve(), CURRENT.parent / 'releases' / args.commit
    with (STATE / '.backup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if previous.name != args.base:
            raise ValueError('preview_installed_base_changed')
        module = helper(previous)
        journal = STATE / 'releases' / ('analyst-preview-' + args.commit + '.json')
        result = globals()[args.action](args, previous, target, module, journal)
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
