#!/usr/bin/env python3
"""Root-owned release executor. No daemon on the Mac, Docker socket in a bot, or model shell."""

import ast
import fcntl
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import time
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from uuid import UUID

STATE = Path('/var/lib/quant-company')
CURRENT = Path('/opt/quant-company/current')
SERVICES = ['codex-runtime', 'api', 'worker', 'news-worker', 'dispatch', 'slack-socket', 'maintenance']


def run(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, timeout=900, **kwargs).stdout


def protocol(action, identity=None, data=None):
    args = ['docker', 'exec', '-i', 'quant-company-maintenance-1', 'python', '/app/entrypoint.py',
            'quant-company', 'maintenance-release', action]
    if identity:
        args += ['--id', str(UUID(identity))]
    output = run(args, input=None if data is None else json.dumps(data).encode())
    return output if action == 'archive' else json.loads(output or b'null')


def atomic(path, data):
    original = path.stat() if path.exists() else None
    temp = path.with_name(path.name + '.release-tmp')
    temp.write_bytes(data)
    # Existing bind-mounted configuration must remain readable by UID 10001.
    # New journals and saved configuration copies remain root-private.
    temp.chmod((original.st_mode & 0o777) if original else 0o600)
    if original:
        os.chown(temp, original.st_uid, original.st_gid)
    os.replace(temp, path)


def link(target):
    temp = CURRENT.with_name('current.release-tmp')
    temp.unlink(missing_ok=True)
    temp.symlink_to(target)
    os.replace(temp, CURRENT)


def services(root):
    envfile = STATE/'config/runtime.env'
    enabled = any(line.strip() == 'COMPANY_STAFF_REVIEW_ENABLED=true'
                  for line in envfile.read_text().splitlines()) if envfile.exists() else False
    supported = (root/'src/quant_company/providers/claude_runtime.py').is_file()
    quant = (envfile.exists() and 'QUANT_FEED_ENABLED=true' in envfile.read_text().splitlines()
             and (root/'src/quant_company/quant_feed').is_dir())
    return SERVICES + (['claude-runtime'] if enabled and supported else []) + (['quant-feed-worker'] if quant else [])


def compose_command(root):
    envfile = STATE/'config/runtime.env'
    command = ['docker', 'compose', '--profile', 'maintenance', '--profile', 'claude', '--profile', 'quant-feed',
               '--env-file', str(envfile), '-f', str(root/'deploy/compose.yaml')]
    research = any(line.strip() == 'COMPANY_RESEARCH_ENABLED=true'
                   for line in envfile.read_text().splitlines()) if envfile.exists() else False
    if research:
        overlay = root/'deploy/research.compose.yaml'
        if not overlay.is_file():
            raise ValueError('release_research_overlay_missing')
        command += ['-f', str(overlay)]
    autonomous = any(line.strip() == 'COMPANY_AUTONOMOUS_RESEARCH_ENABLED=true'
                     for line in envfile.read_text().splitlines()) if envfile.exists() else False
    if autonomous:
        if not research:
            raise ValueError('release_autonomous_requires_research')
        overlay = root/'deploy/autonomous-research.compose.yaml'
        if not overlay.is_file():
            raise ValueError('release_autonomous_overlay_missing')
        command += ['-f', str(overlay)]
    return command


def compose(root, *args, env=None):
    return run([*compose_command(root), *args], env=env)


def unpack(data, target):
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        members = archive.getmembers()
        if sum(m.size for m in members) > 32 * 1024 * 1024:
            raise ValueError('release_expanded_size')
        roots = {PurePosixPath(m.name).parts[0] for m in members}
        if len(roots) != 1:
            raise ValueError('release_archive_roots')
        for item in members:
            parts = PurePosixPath(item.name).parts
            if item.name.startswith('/') or '..' in parts or not (item.isdir() or item.isfile()):
                raise ValueError('release_archive_path')
            if len(parts) == 1:
                continue
            path = target.joinpath(*parts[1:])
            if item.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(item) as source, path.open('xb') as output:
                    shutil.copyfileobj(source, output)
                # Docker COPY preserves source modes. The root operator's private umask
                # must not make repository code unreadable to the image's UID 10001.
                path.chmod(0o755 if item.mode & 0o111 else 0o644)
        target.chmod(0o755)
        for path in target.rglob('*'):
            if path.is_dir():
                path.chmod(0o755)


def validate_tree(previous, target):
    # Load the policy from the installed release, never the candidate. No third-party dependencies.
    protected = {'api.py', 'cli.py', 'config.py', 'db.py', 'schema.sql', 'socket_mode.py',
                 'owner_controls.py', 'state_schema.sql', 'web_fetch.py', 'finance_sources.py', 'maintenance/policy.py',
                 'maintenance/github.py', 'maintenance/applications.py', 'maintenance/releases.py', 'maintenance/schema.sql',
                 'staff/cases.py', 'staff/store.py', 'staff/runner.py', 'staff/workflow.py', 'staff/schema.sql', 'staff/packs.py',
                 'staff/progress.py', 'staff/comparisons.py', 'maintenance/evaluation.py',
                 'staff/independent_review.py', 'staff/review_contract.py'}

    def inventory(root):
        output = {}
        for path in root.rglob('*'):
            relative = path.relative_to(root).as_posix()
            if relative.startswith(('qdata/', '.service-', '.qdata-', 'deploy/__pycache__/')):
                continue
            if path.is_symlink():
                raise ValueError('release_symlink')
            if path.is_file():
                output[relative] = path.read_bytes()
        return output

    before, after = inventory(previous), inventory(target)
    for name in before.keys() | after.keys():
        if before.get(name) == after.get(name):
            continue
        if name not in after:
            raise ValueError('release_file_deleted')
        is_doc = (name.startswith('docs/') and name.endswith('.md')
                  and name not in {'docs/deployment.md', 'docs/codex-runtime.md'})
        is_test = re.fullmatch(r'tests/test_maintenance_regression_[a-z0-9_]+\.py', name)
        module = name.removeprefix('src/quant_company/')
        is_procedure = (name.startswith('src/quant_company/staff/playbooks/') and name.endswith('.md')
                        and len(PurePosixPath(module).parts) == 3)
        is_code = (name.startswith('src/quant_company/') and module not in protected and not module.startswith('providers/')
                   and (module.endswith('.py') or module == 'roles.json'))
        if not (is_doc or is_test or is_code or is_procedure):
            raise ValueError('release_protected_file_changed')
        if module == 'roles.json':
            def fixed(data):
                return [{k: v for k, v in r.items() if k not in {'mission', 'instructions', 'tools'}} for r in json.loads(data)]
            if fixed(before[name]) != fixed(after[name]):
                raise ValueError('release_role_permissions_changed')
            old, new = json.loads(before[name]), json.loads(after[name])
            if [r.get('tools', []) for r in old] != [r.get('tools', []) for r in new]:
                if not any(p.startswith('src/') and p.endswith('.py') and before.get(p) != content for p, content in after.items()):
                    raise ValueError('release_tool_registration_requires_code')
                tree = ast.parse(after['src/quant_company/contracts.py'])
                tool = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ToolRequest')
                field = next(node for node in tool.body if isinstance(node, ast.AnnAssign)
                             and isinstance(node.target, ast.Name) and node.target.id == 'name')
                values = field.annotation.slice
                literals = values.elts if isinstance(values, ast.Tuple) else [values]
                if not all(isinstance(v, ast.Constant) and isinstance(v.value, str) for v in literals):
                    raise ValueError('release_explicit_tool_contract_required')
                if any(not set(r.get('tools', [])) <= {v.value for v in literals} for r in new):
                    raise ValueError('release_unknown_tool')


def health(commit, postgres_id):
    rows = json.loads(run(['docker', 'inspect', *['quant-company-'+s+'-1' for s in ['postgres', *services(CURRENT.resolve())]]]))
    for row in rows:
        state = row['State']
        if not state['Running'] or state['OOMKilled'] or state.get('Health', {}).get('Status', 'healthy') != 'healthy':
            raise ValueError('release_service_unhealthy')
        if row['Name'] == '/quant-company-postgres-1':
            if row['Id'] != postgres_id:
                raise ValueError('release_database_recreated')
        elif row['Config']['Labels'].get('org.opencontainers.image.revision') != commit:
            raise ValueError('release_image_revision_mismatch')
    return {'healthy_services': len(rows), 'postgres_recreated': False}


def stable_health(commit, postgres_id):
    result = health(commit, postgres_id)
    for _ in range(2):
        time.sleep(5)
        result = health(commit, postgres_id)
    return result


def take_backup(previous, envfile):
    # Same lock as scheduled backup. Direct call avoids recursively acquiring our lock.
    spec = importlib.util.spec_from_file_location('release_backup', previous/'deploy/state_backup.py')
    backup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backup)
    for key, value in backup.config_values(STATE/'config/backup.env').items():
        os.environ[key] = value
    command = compose_command(previous)
    backup.backup(SimpleNamespace(env_file=envfile, s3_uri=None), backup.config_values(envfile), command, STATE)


def execute(item):
    identity, commit = str(UUID(item['id'])), item['commit']
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('release_commit_invalid')
    records = STATE/'releases'
    records.mkdir(mode=0o700, exist_ok=True)
    journal = records/(identity+'.json')
    envfile, rolesfile = STATE/'config/runtime.env', STATE/'config/roles.json'
    target = CURRENT.parent/'releases'/commit
    if journal.exists():
        record = json.loads(journal.read_text())
        if record['commit'] != commit:
            raise ValueError('release_journal_conflict')
        if record['state'] in {'complete', 'blocked', 'rolled_back'}:
            return protocol('finish', identity, record)
        # Interrupted cutover: restore the saved runtime before any further write.
        atomic(envfile, (records/(identity+'.env')).read_bytes())
        atomic(rolesfile, (records/(identity+'.roles')).read_bytes())
        previous = Path(record['previous'])
        link(previous)
        compose(previous, 'up', '-d', '--no-deps', '--wait', '--wait-timeout', '120', *services(previous))
        stable_health(previous.name, record['postgres_id'])
        record.update(state='rolled_back', error='interrupted_cutover_restored')
        atomic(journal, json.dumps(record).encode())
        return protocol('finish', identity, record)
    previous = CURRENT.resolve()
    try:
        if target.exists():
            raise ValueError('release_target_exists_requires_review')
        target.mkdir(parents=True)
        unpack(protocol('archive', identity), target)
        validate_tree(previous, target)
        shutil.copytree(previous/'qdata', target/'qdata')
        qcommit = json.loads((target/'deploy/qdata-source.json').read_text())['commit']
        buildenv = {**os.environ, 'RELEASE_COMMIT': commit, 'QDATA_COMMIT': qcommit,
                    'QDATA_BUILD_CONTEXT': str(target/'qdata')}
        for service in ['api', 'codex-runtime', 'maintenance'] + (
                ['claude-runtime'] if 'claude-runtime' in services(target) else []):
            compose(target, 'build', service, env=buildenv)
        oldenv, oldroles = envfile.read_bytes(), rolesfile.read_bytes()
        postgres_id = json.loads(run(['docker', 'inspect', 'quant-company-postgres-1']))[0]['Id']
        record = {'commit': commit, 'previous': str(previous), 'postgres_id': postgres_id, 'state': 'cutover'}
        atomic(records/(identity+'.env'), oldenv)
        atomic(records/(identity+'.roles'), oldroles)
        atomic(journal, json.dumps(record).encode())
        compose(previous, 'stop', 'slack-socket')
        for _ in range(60):
            if not any(protocol('activity', identity).values()):
                break
            time.sleep(5)
        else:
            raise ValueError('release_company_work_not_drained')
        compose(previous, 'stop', 'worker', 'dispatch', 'maintenance')
        take_backup(previous, envfile)
        values = {'RELEASE_COMMIT': commit, 'QDATA_COMMIT': qcommit, 'QDATA_BUILD_CONTEXT': str(target/'qdata')}
        lines = [s for s in oldenv.decode().splitlines() if s.split('=', 1)[0] not in values]
        atomic(envfile, ('\n'.join(lines+[k+'='+v for k, v in values.items()])+'\n').encode())
        # Preserve deployed model/activation overrides; apply reviewed prompts and code-backed tool registrations.
        roles = json.loads(oldroles)
        original = {r['id']: r for r in json.loads((previous/'src/quant_company/roles.json').read_text())}
        candidate = {r['id']: r for r in json.loads((target/'src/quant_company/roles.json').read_text())}
        for role in roles:
            for key in ['mission', 'instructions', 'tools']:
                if candidate[role['id']].get(key) != original[role['id']].get(key):
                    if key == 'tools' and role.get(key) != original[role['id']].get(key):
                        raise ValueError('release_tool_override_requires_review')
                    role[key] = candidate[role['id']][key]
        atomic(rolesfile, json.dumps(roles, ensure_ascii=False).encode())
        link(target)
        compose(target, 'up', '-d', '--no-deps', '--wait', '--wait-timeout', '120', *services(target))
        record.update(state='complete', **stable_health(commit, postgres_id))
        atomic(journal, json.dumps(record).encode())
    except Exception as exc:
        code = str(exc) if isinstance(exc, ValueError) else 'release_host_command_failed'
        if not re.fullmatch(r'[a-z_0-9]{1,100}', code):
            code = 'release_host_validation_failed'
        if journal.exists():
            record = json.loads(journal.read_text())
            atomic(envfile, (records/(identity+'.env')).read_bytes())
            atomic(rolesfile, (records/(identity+'.roles')).read_bytes())
            link(previous)
            compose(previous, 'up', '-d', '--no-deps', '--wait', '--wait-timeout', '120', *services(previous))
            stable_health(previous.name, record['postgres_id'])
            record.update(state='rolled_back', error=code)
        else:
            record = {'commit': commit, 'state': 'blocked', 'error': code}
        atomic(journal, json.dumps(record).encode())
    return protocol('finish', identity, record)


def main():
    os.umask(0o077)
    with (STATE/'.backup.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if item := protocol('next'):
            execute(item)


if __name__ == '__main__':
    main()
