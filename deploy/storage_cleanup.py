#!/usr/bin/env python3
"""Preview bounded host cleanup; --apply preserves live images and verifies S3 backups."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

STATE = Path('/var/lib/quant-company')
REPOSITORIES = {'quant-company', 'quant-company-codex', 'quant-company-claude',
                'quant-company-autonomous', 'quant-company-maintenance'}
KEEP_IMAGES = 3
IMAGE_DAYS = 7
BACKUP_DAYS = 7
KEEP_BACKUPS = 3
CACHE_LIMIT = '2GB'
ARCHIVE_NAME = re.compile(r'company-(\d{8}T\d{6}Z)-[0-9a-f]{8}\.tar\.gz')


def run(command, timeout=120):
    return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL, timeout=timeout).strip()


def digest(path):
    with path.open('rb') as stream:
        return stream_digest(stream)


def stream_digest(stream):
    value = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        value.update(block)
    return value.hexdigest()


def configuration(path):
    values = {}
    for raw in path.read_text().splitlines():
        key, separator, value = raw.partition('=')
        if separator and not raw.lstrip().startswith('#'):
            values[key.strip()] = value.strip().strip('"\'')
    return values


def containers():
    ids = run(['docker', 'ps', '-aq', '--no-trunc']).splitlines()
    if not ids:
        raise ValueError('no_containers_found')
    template = ('{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},'
                '"status":{{json .State.Status}},"started":{{json .State.StartedAt}},'
                '"restart_count":{{.RestartCount}},'
                '"health":{{with index .State "Health"}}{{json .Status}}{{else}}null{{end}}}')
    return [json.loads(line) for line in run(['docker', 'inspect', '--format', template, *ids]).splitlines()]


def images():
    ids = sorted(set(run(['docker', 'image', 'ls', '-q', '--no-trunc']).splitlines()))
    template = '{"id":{{json .Id}},"created":{{json .Created}},"tags":{{json .RepoTags}}}'
    return [json.loads(line) for line in run(['docker', 'image', 'inspect', '--format', template, *ids]).splitlines()]


def image_candidates(rows, referenced, cfg, now, protected_commits=()):
    protected = set(referenced)
    pins = {value for key, value in cfg.items() if re.fullmatch(r'PINNED_[A-Z_]*IMAGE', key) and value}
    available_tags = {tag for row in rows for tag in row['tags'] or []}
    if pins - available_tags:
        raise ValueError('configured_image_pin_missing')
    commits = set(protected_commits) | {cfg.get('RELEASE_COMMIT', '')}
    by_repository = {}
    for row in sorted(rows, key=lambda item: (item['created'], item['id']), reverse=True):
        for tag in row['tags'] or []:
            repository, _, revision = tag.rpartition(':')
            if tag in pins or revision[:40] in commits:
                protected.add(row['id'])
            if repository in REPOSITORIES:
                by_repository.setdefault(repository, []).append(row['id'])
    for ids in by_repository.values():
        protected.update(list(dict.fromkeys(ids))[:KEEP_IMAGES])
    cutoff = now - timedelta(days=IMAGE_DAYS)
    return [row for row in sorted(rows, key=lambda item: (item['created'], item['id']), reverse=True)
            if row['id'] not in protected and row['tags']
            and all(tag.rpartition(':')[0] in REPOSITORIES for tag in row['tags'])
            and datetime.fromisoformat(row['created'].replace('Z', '+00:00')) < cutoff]


def fingerprint(path):
    stat = path.stat(follow_symlinks=False)
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]


def backup_candidates(root, now):
    if root.is_symlink() or not root.is_dir():
        raise ValueError('unsafe_backup_directory')
    rows = []
    for path in root.iterdir():
        match = ARCHIVE_NAME.fullmatch(path.name)
        if match and path.is_file() and not path.is_symlink():
            stamp = datetime.strptime(match[1], '%Y%m%dT%H%M%SZ').replace(tzinfo=UTC)
            rows.append({'name': path.name, 'created': stamp.isoformat(), 'fingerprint': fingerprint(path),
                         'bytes': path.stat().st_size})
    rows.sort(key=lambda row: row['name'], reverse=True)
    cutoff = now - timedelta(days=BACKUP_DAYS)
    return [row for row in rows[KEEP_BACKUPS:]
            if datetime.fromisoformat(row['created']) < cutoff
            and datetime.fromtimestamp(row['fingerprint'][3] / 1e9, UTC) < cutoff]


def remote_digest(uri, cfg):
    env = dict(os.environ)
    for key in ('AWS_PROFILE', 'AWS_DEFAULT_REGION'):
        if cfg.get(key):
            env[key] = cfg[key]
    command = ['aws', 's3', 'cp', uri, '-', '--only-show-errors',
               '--cli-connect-timeout', '10', '--cli-read-timeout', '60']
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env) as process:
        value = stream_digest(process.stdout)
        if process.wait(timeout=120):
            raise ValueError('remote_backup_unavailable')
    return value


def verified_backup(path, expected_fingerprint, cfg):
    checksum = path.with_name(path.name + '.sha256')
    if (path.is_symlink() or checksum.is_symlink() or not checksum.is_file()
            or fingerprint(path) != expected_fingerprint):
        raise ValueError('backup_changed_or_checksum_missing')
    content = checksum.read_text()
    match = re.fullmatch(r'([0-9a-f]{64})  ' + re.escape(path.name) + r'\n?', content)
    if not match or digest(path) != match[1]:
        raise ValueError('local_backup_checksum_mismatch')
    prefix = cfg.get('BACKUP_S3_URI', '')
    if not re.fullmatch(r's3://[a-z0-9.-]+/company/?', prefix):
        raise ValueError('backup_s3_prefix_missing')
    expected = match[1]
    uri = prefix.rstrip('/') + '/' + path.name
    if remote_digest(uri, cfg) != expected:
        raise ValueError('remote_backup_checksum_mismatch')
    if path.is_symlink() or fingerprint(path) != expected_fingerprint or digest(path) != expected:
        raise ValueError('backup_changed_during_verification')
    return {'name': path.name, 'bytes': path.stat().st_size, 'sha256': expected, 's3_uri': uri}


def disk():
    usage = shutil.disk_usage('/')
    return {'total_bytes': usage.total, 'used_bytes': usage.used, 'available_bytes': usage.free}


def save(path, report):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, indent=2) + '\n')
    temporary.replace(path)


def guard_config(report):
    if digest(STATE / 'config/runtime.env') != report['runtime_sha256']:
        raise ValueError('runtime_configuration_changed')


def remove_image(row, report):
    guard_config(report)
    if row['id'] in {item['image'] for item in containers()}:
        return False
    # No --force: Docker also rejects a concurrent new reference or a dependent image.
    run(['docker', 'image', 'rm', '--no-prune', row['id']])
    return True


def make_plan(protected_commits):
    now = datetime.now(UTC)
    runtime_sha256 = digest(STATE / 'config/runtime.env')
    cfg = configuration(STATE / 'config/runtime.env')
    current_containers = containers()
    return {
        'observed_at_utc': now.isoformat(), 'mode': 'preview',
        'policy': {'image_minimum_age_days': IMAGE_DAYS, 'images_per_repository': KEEP_IMAGES,
                   'local_backup_days': BACKUP_DAYS, 'minimum_local_backups': KEEP_BACKUPS,
                   'build_cache_limit': CACHE_LIMIT, 'cache_minimum_age_hours': 24,
                   'protected_commits': protected_commits},
        'runtime_sha256': runtime_sha256,
        'roles_sha256': digest(STATE / 'config/roles.json'),
        'containers_before': current_containers, 'disk_before': disk(),
        'images': image_candidates(images(), {row['image'] for row in current_containers},
                                   cfg, now, protected_commits),
        'backups': backup_candidates(STATE / 'backups', now),
    }


def apply_plan(report, receipt):
    report.update(mode='apply', status='in_progress', deleted_images=[], deleted_backups=[], skipped=[])
    save(receipt, report)
    try:
        for row in report['images']:
            try:
                if remove_image(row, report):
                    report['deleted_images'].append(row)
                else:
                    report['skipped'].append({'image': row['id'], 'reason': 'container_now_references_image'})
            except subprocess.CalledProcessError:
                report['skipped'].append({'image': row['id'], 'reason': 'docker_refused_image_removal'})
            save(receipt, report)
        guard_config(report)
        report['cache_command'] = ['docker', 'buildx', 'prune', '--builder', 'default', '--all', '--force',
                                   '--filter', 'until=24h', '--max-used-space', CACHE_LIMIT]
        report['cache_result'] = run(report['cache_command'], timeout=900).splitlines()[-1:]
        save(receipt, report)
        cfg = configuration(STATE / 'config/backup.env')
        for index, row in enumerate(report['backups'], 1):
            guard_config(report)
            path = STATE / 'backups' / row['name']
            try:
                verified = verified_backup(path, row['fingerprint'], cfg)
            except (ValueError, OSError) as error:
                reason = str(error) if isinstance(error, ValueError) else type(error).__name__
                report['skipped'].append({'backup': row['name'], 'reason': reason})
            else:
                guard_config(report)
                path.unlink()
                report['deleted_backups'].append(verified)
                save(receipt, report)
                path.with_name(path.name + '.sha256').unlink()
            save(receipt, report)
            print(json.dumps({'backup_verification': index, 'total': len(report['backups']),
                              'deleted': len(report['deleted_backups'])}), file=sys.stderr, flush=True)
        report['status'] = 'complete'
    except Exception as error:
        report.update(status='failed', error=type(error).__name__)
        raise
    finally:
        report['disk_after'] = disk()
        report['containers_after'] = containers()
        report['runtime_preserved'] = digest(STATE / 'config/runtime.env') == report['runtime_sha256']
        report['roles_preserved'] = digest(STATE / 'config/roles.json') == report['roles_sha256']
        report['containers_preserved'] = (
            sorted(report['containers_before'], key=lambda row: row['id'])
            == sorted(report['containers_after'], key=lambda row: row['id']))
        report['completed_at_utc'] = datetime.now(UTC).isoformat()
        save(receipt, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--protect-commit', action='append', default=[])
    args = parser.parse_args()
    if any(not re.fullmatch(r'[0-9a-f]{40}', commit) for commit in args.protect_commit):
        parser.error('--protect-commit must be an exact 40-character commit')
    if not args.apply:
        print(json.dumps(make_plan(args.protect_commit), indent=2))
        return
    os.umask(0o077)
    with (STATE / '.backup.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'status': 'deferred', 'reason': 'backup_or_release_lock_held'}))
            return
        report = make_plan(args.protect_commit)
        root = STATE / 'operations/storage-cleanup'
        root.mkdir(parents=True, exist_ok=True)
        receipt = root / (datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ-') + uuid4().hex[:8] + '.json')
        apply_plan(report, receipt)
        print(json.dumps({'status': report['status'], 'receipt': str(receipt),
                          'disk_before': report['disk_before'], 'disk_after': report['disk_after'],
                          'deleted_images': len(report['deleted_images']),
                          'deleted_backups': len(report['deleted_backups']), 'skipped': len(report['skipped']),
                          'containers_preserved': report['containers_preserved'],
                          'runtime_preserved': report['runtime_preserved'], 'roles_preserved': report['roles_preserved']}))


if __name__ == '__main__':
    main()
