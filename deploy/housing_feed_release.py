#!/usr/bin/env python3
"""Reviewed operator rollout for the housing collector and existing dispatcher only.

Stage performs real public collection with publication disabled. Activate requires that
receipt and the unchanged base/configuration. Journals contain no secret values.
"""

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

STATE = Path('/var/lib/quant-company')
CURRENT = Path('/opt/quant-company/current')


def run(command, **kwargs):
    return subprocess.run(command, check=True, capture_output=True, timeout=900, **kwargs).stdout


def helper(root):
    spec = importlib.util.spec_from_file_location('housing_release_helper', root / 'deploy/maintenance_release.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configuration(data):
    return dict(line.split('=', 1) for line in data.decode().splitlines()
                if '=' in line and not line.lstrip().startswith('#'))


def updated(data, values):
    lines = [line for line in data.decode().splitlines() if line.split('=', 1)[0] not in values]
    return ('\n'.join(lines + [key + '=' + value for key, value in values.items()]) + '\n').encode()


def inventory():
    names = run(['docker', 'ps', '-a', '--filter', 'name=quant-company-', '--format', '{{.Names}}']).decode().splitlines()
    return {r['Name']: {'id': r['Id'], 'running': r['State']['Running'], 'restarts': r['RestartCount'],
                        'oom': r['State']['OOMKilled'], 'image': r['Config']['Image']}
            for r in json.loads(run(['docker', 'inspect', *names]))}


def preserved(before, after):
    selected = {'/quant-company-dispatch-1', '/quant-company-housing-feed-worker-1'}
    if any(after.get(name) != value for name, value in before.items() if name not in selected):
        raise ValueError('unrelated_service_changed')
    return True


def oneoff(module, root, env, code):
    return module.compose(root, 'run', '--rm', '--no-deps', '-T', 'housing-feed-worker', 'python', '-c', code, env=env)


def updates(args, values):
    channels = json.loads(values['SLACK_ALLOWED_CHANNELS'])
    if args.owner not in json.loads(values['SLACK_ALLOWED_USERS']):
        raise ValueError('owner_not_authorized')
    return {'RELEASE_COMMIT': args.commit, 'HOUSING_FEED_ENABLED': 'true',
            'HOUSING_FEED_PUBLISH_ENABLED': 'false', 'HOUSING_FEED_CHANNEL_ID': args.channel,
            'HOUSING_FEED_OWNER_USER': args.owner,
            'SLACK_ALLOWED_CHANNELS': json.dumps(list(dict.fromkeys([*channels, args.channel])), separators=(',', ':'))}


def slack_access(channel, team):
    token = json.loads((STATE / 'secrets/slack-credentials.json').read_text())['reporter']['bot_token']

    def request(method, params=None):
        url = 'https://slack.com/api/' + method + ('?' + urllib.parse.urlencode(params) if params else '')
        req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(req, timeout=20) as response:
            result = json.load(response)
        if not result.get('ok'):
            raise ValueError('reporter_access_failed')
        return result

    identity = request('auth.test')
    if identity.get('team_id') != team:
        raise ValueError('reporter_workspace_mismatch')
    request('conversations.history', {'channel': channel, 'limit': 1})
    return {'team': team, 'bot_user': identity['user_id'], 'channel': channel, 'history_access': True}


def stage(args, previous, target, module, journal):
    raw = (STATE / 'config/runtime.env').read_bytes()
    values = configuration(raw)
    settings = updates(args, values)
    access = slack_access(args.channel, values['SLACK_TEAM_ID'])
    data = args.archive.read_bytes()
    if hashlib.sha256(data).hexdigest() != args.sha256:
        raise ValueError('archive_digest_mismatch')
    if target.exists() or journal.exists():
        raise ValueError('release_already_staged_inspect_receipt')
    before = inventory()
    if '/quant-company-housing-feed-worker-1' in before:
        raise ValueError('housing_already_installed')
    target.mkdir()
    module.unpack(data, target)
    qdata = Path(values['QDATA_BUILD_CONTEXT'])
    shutil.copytree(qdata, target / 'qdata')
    settings['QDATA_BUILD_CONTEXT'] = str(target / 'qdata')
    env = {**os.environ, **settings}
    module.compose(target, 'build', 'housing-feed-worker', env=env)
    schema = """from importlib.resources import files
from quant_company.config import Settings
from quant_company.db import Database
with Database(Settings().database_url).transaction() as conn:
    conn.execute('SELECT pg_advisory_xact_lock(71350219)')
    conn.execute(files('quant_company.housing_feed').joinpath('schema.sql').read_text())
print('housing_schema_ready')
"""
    oneoff(module, target, env, schema)
    preview = json.loads(module.compose(target, 'run', '--rm', '--no-deps', '-T', 'housing-feed-worker',
                                        'quant-company', 'housing-feed', 'collect', env=env))
    if (len(preview.get('sources', [])) != 4 or any(r['state'] != 'collected' or r['queued'] != 0
                                                 for r in preview['sources'])):
        module.atomic(journal.with_suffix('.preview-failed.json'), json.dumps(preview).encode())
        raise ValueError('housing_preview_failed')
    preserved(before, inventory())
    module.atomic(journal.with_suffix('.env'), raw)
    module.atomic(journal, json.dumps({'phase': 'staged', 'base': args.base, 'commit': args.commit,
                                      'archive_sha256': args.sha256, 'env_sha256': hashlib.sha256(raw).hexdigest(),
                                      'updates': settings, 'access': access, 'preview': preview}).encode())
    return {'phase': 'staged', 'preview': preview, 'access': access, 'unrelated_services_preserved': True}


def activate(args, previous, target, module, journal):
    receipt = json.loads(journal.read_text())
    raw = (STATE / 'config/runtime.env').read_bytes()
    if (receipt['phase'] != 'staged' or receipt['base'] != args.base or receipt['commit'] != args.commit
            or hashlib.sha256(raw).hexdigest() != receipt['env_sha256']
            or receipt['updates']['HOUSING_FEED_CHANNEL_ID'] != args.channel
            or receipt['updates']['HOUSING_FEED_OWNER_USER'] != args.owner):
        raise ValueError('staged_configuration_changed')
    before = inventory()
    values = configuration(raw)
    access = slack_access(args.channel, values['SLACK_TEAM_ID'])
    settings = {**receipt['updates'], 'HOUSING_FEED_PUBLISH_ENABLED': 'true'}
    env = {**os.environ, **settings}
    receipt['phase'] = 'cutover_started'
    module.atomic(journal, json.dumps(receipt).encode())
    try:
        module.compose(previous, 'stop', '-t', '30', 'dispatch')
        module.atomic(STATE / 'config/runtime.env', updated(raw, settings))
        module.link(target)
        module.compose(target, 'up', '-d', '--no-deps', 'dispatch')
        oneoff(module, target, env, """from quant_company.config import Settings
from quant_company.db import Database
with Database(Settings().database_url).transaction() as conn:
    conn.execute('UPDATE housing_feed_sources SET next_at=now() WHERE lease_token IS NULL')
print('housing_collection_due')
""")
        module.compose(target, 'up', '-d', '--no-deps', 'housing-feed-worker')
        time.sleep(5)
        after = inventory()
        preserved(before, after)
        selected = {name: after['/quant-company-' + name + '-1'] for name in ('dispatch', 'housing-feed-worker')}
        if any(not row['running'] or row['oom'] or row['restarts'] or row['image'] != 'quant-company:' + args.commit
               for row in selected.values()):
            raise ValueError('housing_service_unhealthy')
        receipt.update(phase='activated', access=access, services=selected, unrelated_services_preserved=True)
        module.atomic(journal, json.dumps(receipt).encode())
        return receipt
    except Exception:
        # Stop effects first. Keep successful sends and ambiguous sends as receipts.
        module.compose(target, 'stop', '-t', '30', 'housing-feed-worker', 'dispatch', env=env)
        oneoff(module, target, env, """from quant_company.config import Settings
from quant_company.db import Database
with Database(Settings().database_url).transaction() as conn:
    conn.execute(\"\"\"UPDATE outbox SET status=CASE WHEN status='sending' THEN 'uncertain' ELSE 'stale' END,
        error='housing_release_rollback' WHERE id IN (SELECT id FROM housing_feed_publications)
        AND status IN ('pending','sending')\"\"\")
print('housing_pending_effects_closed')
""")
        module.atomic(STATE / 'config/runtime.env', raw)
        module.link(previous)
        module.compose(previous, 'up', '-d', '--no-deps', 'dispatch')
        receipt['phase'] = 'rolled_back'
        module.atomic(journal, json.dumps(receipt).encode())
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['stage', 'activate'])
    for name in ('base', 'commit', 'channel', 'owner'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--sha256')
    args = parser.parse_args()
    if not all(re.fullmatch(r'[0-9a-f]{40}', s) for s in (args.base, args.commit)):
        raise ValueError('invalid_release_revision')
    if not re.fullmatch(r'[CG][A-Z0-9]+', args.channel) or not re.fullmatch(r'U[A-Z0-9]+', args.owner):
        raise ValueError('invalid_slack_destination')
    previous, target = CURRENT.resolve(), CURRENT.parent / 'releases' / args.commit
    with (STATE / '.backup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if CURRENT.resolve().name != args.base:
            raise ValueError('installed_base_changed')
        module = helper(previous)
        journal = STATE / 'releases' / ('housing-feed-' + args.commit + '.json')
        result = globals()[args.action](args, previous, target, module, journal)
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
