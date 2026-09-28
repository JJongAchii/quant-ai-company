#!/usr/bin/env python3
"""Scoped operator upgrade of the housing feed, dispatcher, and Slack socket receiver."""

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import time
from pathlib import Path

STATE = Path('/var/lib/quant-company')
CURRENT = Path('/opt/quant-company/current')


def installed_helper(previous):
    path = previous / 'deploy/housing_feed_release.py'
    spec = importlib.util.spec_from_file_location('installed_housing_release', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERVICES = ('housing-feed-worker', 'dispatch', 'slack-socket')


def preserved(before, after):
    selected = {'/quant-company-' + name + '-1' for name in SERVICES}
    if any(after.get(name) != value for name, value in before.items() if name not in selected):
        raise ValueError('unrelated_service_changed')


def settings_match(values, args):
    return (values.get('HOUSING_FEED_ENABLED') == 'true'
            and values.get('HOUSING_FEED_PUBLISH_ENABLED') == 'true'
            and values.get('HOUSING_MAP_PANEL_ENABLED', 'false') == 'false'
            and values.get('HOUSING_FEED_CHANNEL_ID') == args.channel
            and values.get('HOUSING_FEED_OWNER_USER') == args.owner)


def stage(args, previous, target, base, helper, journal):
    raw = (STATE / 'config/runtime.env').read_bytes()
    if not settings_match(base.configuration(raw), args):
        raise ValueError('housing_active_configuration_mismatch')
    if target.exists() or journal.exists():
        raise ValueError('housing_panel_release_already_staged')
    archive = args.archive.read_bytes()
    if hashlib.sha256(archive).hexdigest() != args.sha256:
        raise ValueError('housing_panel_archive_digest_mismatch')
    access = base.slack_access(args.channel, base.configuration(raw)['SLACK_TEAM_ID'])
    before = base.inventory()
    selected_before = {'/quant-company-' + name + '-1': before.get('/quant-company-' + name + '-1')
                       for name in SERVICES}
    if any(not row or not row['running'] or row['oom'] or row['restarts']
           for row in selected_before.values()):
        raise ValueError('housing_panel_base_service_unhealthy')
    if any(selected_before['/quant-company-' + name + '-1']['image'] != 'quant-company:' + args.base
           for name in ('dispatch', 'housing-feed-worker')):
        raise ValueError('housing_panel_base_release_mismatch')
    target.mkdir()
    base.unpack(archive, target)
    shutil.copytree(previous / 'qdata', target / 'qdata')
    env = {**os.environ, 'RELEASE_COMMIT': args.commit, 'QDATA_BUILD_CONTEXT': str(target / 'qdata')}
    args.reuse_base_image = True
    built = base.build(args, previous, target, helper, env)
    code = """import json
import httpx
from importlib.resources import files
from quant_company.config import Settings
from quant_company.db import Database
from quant_company.housing_feed.maps import geocode, map_url
with Database(Settings().database_url).transaction() as conn:
    conn.execute('SELECT pg_advisory_xact_lock(71350219)')
    conn.execute(files('quant_company.housing_feed').joinpath('schema.sql').read_text())
location = geocode('경기도 광명시 소하동 A6BL', '경기')
if location is None:
    raise ValueError('housing_panel_geocode_unavailable')
response = httpx.get(map_url(location, embed=True), timeout=30)
if response.status_code != 200 or 'text/html' not in response.headers.get('content-type', ''):
    raise ValueError('housing_panel_embed_unavailable')
print(json.dumps({'geocode': True, 'embed_status': response.status_code,
                  'embed_bytes': len(response.content), 'schema': True}))
"""
    preview = json.loads(base.oneoff(helper, target, env, code))
    preserved(before, base.inventory())
    helper.atomic(journal.with_suffix('.env'), raw)
    helper.atomic(journal, json.dumps({
        'phase': 'staged', 'base': args.base, 'commit': args.commit,
        'archive_sha256': args.sha256, 'env_sha256': hashlib.sha256(raw).hexdigest(),
        'access': access, 'build': built, 'panel_preview': preview,
        'selected_before': selected_before,
    }).encode())
    return {'phase': 'staged', 'access': access, 'build': built,
            'panel_preview': preview, 'unrelated_services_preserved': True}


def activate(args, previous, target, base, helper, journal):
    receipt = json.loads(journal.read_text())
    raw = (STATE / 'config/runtime.env').read_bytes()
    values = base.configuration(raw)
    if (receipt['phase'] != 'staged' or receipt['base'] != args.base or receipt['commit'] != args.commit
            or hashlib.sha256(raw).hexdigest() != receipt['env_sha256']
            or not settings_match(values, args)):
        raise ValueError('housing_panel_staged_configuration_changed')
    access = base.slack_access(args.channel, values['SLACK_TEAM_ID'])
    before = base.inventory()
    if any(before.get(name) != row for name, row in receipt['selected_before'].items()):
        raise ValueError('housing_panel_base_service_changed')
    receipt['phase'] = 'cutover_started'
    helper.atomic(journal, json.dumps(receipt).encode())
    updates = {'RELEASE_COMMIT': args.commit, 'QDATA_BUILD_CONTEXT': str(target / 'qdata'),
               'HOUSING_MAP_PANEL_ENABLED': 'true'}
    env = {**os.environ, **updates}
    try:
        helper.compose(previous, 'stop', '-t', '30', *SERVICES)
        helper.atomic(STATE / 'config/runtime.env', base.updated(raw, updates))
        helper.link(target)
        helper.compose(target, 'up', '-d', '--no-deps', *SERVICES)
        time.sleep(5)
        after = base.inventory()
        preserved(before, after)
        selected = {name: after['/quant-company-' + name + '-1'] for name in SERVICES}
        if any(not row['running'] or row['oom'] or row['restarts'] or row['image'] != 'quant-company:' + args.commit
               for row in selected.values()):
            raise ValueError('housing_panel_service_unhealthy')
        base.oneoff(helper, target, env, """from quant_company.config import Settings
from quant_company.db import Database
with Database(Settings().database_url).transaction() as conn:
    conn.execute('UPDATE housing_feed_sources SET next_at=now() WHERE lease_token IS NULL')
print('housing_panel_collection_due')
""")
        receipt.update(phase='activated', access=access, services=selected,
                       unrelated_services_preserved=True)
        helper.atomic(journal, json.dumps(receipt).encode())
        return receipt
    except Exception:
        helper.compose(target, 'stop', '-t', '30', *SERVICES, env=env)
        base.oneoff(helper, target, env, """from quant_company.config import Settings
from quant_company.db import Database
with Database(Settings().database_url).transaction() as conn:
    conn.execute(\"\"\"UPDATE outbox SET status='uncertain',error='housing_panel_release_rollback'
        WHERE id IN (SELECT id FROM housing_feed_publications) AND status='sending'\"\"\")
    conn.execute(\"\"\"UPDATE housing_map_details SET status='uncertain',error='housing_panel_release_rollback'
        WHERE status='sending'\"\"\")
    conn.execute(\"\"\"UPDATE housing_map_details SET status='stale',error='housing_panel_release_rollback'
        WHERE status='pending'\"\"\")
print('housing_panel_ambiguous_sends_preserved')
""")
        helper.atomic(STATE / 'config/runtime.env', raw)
        helper.link(previous)
        helper.compose(previous, 'up', '-d', '--no-deps', *SERVICES)
        receipt['phase'] = 'rolled_back'
        helper.atomic(journal, json.dumps(receipt).encode())
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['stage', 'activate'])
    for name in ('base', 'commit', 'channel', 'owner'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--sha256')
    args = parser.parse_args()
    if not all(re.fullmatch(r'[0-9a-f]{40}', value) for value in (args.base, args.commit)):
        raise ValueError('invalid_release_revision')
    if not re.fullmatch(r'[CG][A-Z0-9]+', args.channel) or not re.fullmatch(r'U[A-Z0-9]+', args.owner):
        raise ValueError('invalid_slack_destination')
    if args.action == 'stage' and (not args.archive or not re.fullmatch(r'[0-9a-f]{64}', args.sha256 or '')):
        raise ValueError('housing_panel_archive_required')
    previous = CURRENT.resolve()
    if previous.name != args.base:
        raise ValueError('installed_base_changed')
    target = CURRENT.parent / 'releases' / args.commit
    base = installed_helper(previous)
    helper = base.helper(previous)
    journal = STATE / 'releases' / ('housing-panel-' + args.commit + '.json')
    with (STATE / '.backup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = globals()[args.action](args, previous, target, base, helper, journal)
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
