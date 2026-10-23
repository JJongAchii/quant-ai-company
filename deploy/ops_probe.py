#!/usr/bin/env python3
"""Read-only host facts for the ops watch. Preview (--print) is the default.

--publish upserts one snapshot row; --fallback posts a direct Slack notice only for the
failures that silence the in-app watch (PostgreSQL, the dispatcher or the watch itself).
No container, image, unit or configuration is changed, and no secret value is recorded."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import urllib.error
import urllib.request
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

STATE = Path('/var/lib/quant-company')
PROJECT = 'quant-company'
POSTGRES = 'quant-company-postgres-1'
SNAPSHOT_FORMAT = 1
UNITS = ('quant-company-backup.service', 'quant-company-storage-cleanup.service',
         'quant-company-analyst-monitor.service', 'quant-company-release.service')
# state_backup.APP_SERVICES: the backup stops these for the database dump and starts them again before it uploads.
BACKUP_SERVICES = ('slack-socket', 'maintenance', 'worker', 'news-worker', 'quant-codex-runtime', 'quant-feed-worker',
                   'data-watch-worker', 'dispatch', 'api', 'account-gateway', 'codex-runtime', 'claude-runtime')
# Values every process must agree on; per-service values (runtime URLs, commits, PATH) differ by design.
SHARED_KEYS = ('SLACK_TEAM_ID', 'SLACK_ALLOWED_USERS', 'SLACK_ALLOWED_CHANNELS', 'BRIEFING_ENABLED',
               'BRIEFING_CHANNEL_ID', 'BRIEFING_OWNER_USER', 'NEWS_CHANNEL_ID', 'IMPROVEMENTS_CHANNEL_ID',
               'DATA_WATCH_CHANNEL_ID', 'MODEL_ACCOUNTS_CHANNEL_ID', 'OPS_ALERT_CHANNEL_ID', 'OPS_WATCH_OWNER_USER',
               'DATABASE_NAME', 'TEMPORAL_ADDRESS', 'TEMPORAL_NAMESPACE')
TRACEBACK = 'Traceback (most recent call last)'
EXCEPTION = re.compile(r'^(?:[A-Za-z_][\w]*\.)*([A-Z]\w*(?:Error|Exception|Detected|Timeout|Exit|Interrupt))\b')
WATCH_STALE = timedelta(minutes=10)
SLACK_ID = re.compile(r'[CDGUW][A-Z0-9]{2,30}')
RELEASE_STATUS = STATE / 'release-status.json'
CURRENT_RELEASE = Path('/opt/quant-company/current')  # release.py activate points it at the running release
CLAUDE_AUTH = STATE / 'claude/auth'  # the isolated subscription login of claude-runtime (docs/claude-runtime.md)
SYNTHETIC = STATE / 'ops/synthetic-incident.json'
SYNTHETIC_FOR = timedelta(minutes=3)  # longer than two watch ticks, so the minutely timer does not erase the test
COMMIT = re.compile(r'[0-9a-f]{7,40}')
WORD = re.compile(r'[a-z_][a-z0-9_-]{0,40}')
HOST_PATH = re.compile(r'[\w./-]{1,200}')
# Release reports quote exception text; anything credential-shaped is cut before it can reach Slack.
SECRET = re.compile(r'gh[opsu]_\w{16,}|github_pat_\w{20,}|xox[abeprs]-[\w-]+|sk-[\w-]{16,}|AKIA[0-9A-Z]{16}'
                    r'|-----BEGIN [A-Z ]*PRIVATE KEY-----|(?i:bearer|token|password|secret)[\s:=]+\S+|://[^\s/@]+@')


def run(command, timeout=60, stdin=None):
    return subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=timeout,
                          check=True).stdout


def utcnow():
    return datetime.now(UTC)


def configuration(path):
    values = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return values
    for raw in lines:
        key, separator, value = raw.partition('=')
        if separator and not raw.lstrip().startswith('#'):
            values[key.strip()] = value.strip().strip('"\'')
    return values


def environment(row):
    return dict(item.split('=', 1) for item in row['Config'].get('Env') or [] if '=' in item)


def inspect_containers():
    ids = run(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'label=com.docker.compose.project=' + PROJECT]).split()
    return json.loads(run(['docker', 'inspect', *ids])) if ids else []


def container(row):
    state, labels = row['State'], row['Config'].get('Labels') or {}
    health = state.get('Health') or {}
    return {'name': row['Name'].removeprefix('/'), 'service': labels.get('com.docker.compose.service', ''),
            'image': row['Config'].get('Image', '')[:160], 'status': state.get('Status'),
            'health': health.get('Status'), 'oom_killed': bool(state.get('OOMKilled')),
            'exit_code': state.get('ExitCode'), 'started_at': state.get('StartedAt'),
            'finished_at': state.get('FinishedAt'), 'restart_count': row.get('RestartCount', 0)}


def drift(rows, salt):
    """Shared keys whose values differ between containers. Only salted digests leave this function;
    a fresh salt per run means digests compare within one snapshot, never across snapshots."""
    found = {}
    for key in SHARED_KEYS:
        groups = {}
        for row in rows:
            values = environment(row)
            if key in values:
                value = hashlib.sha256((salt + '\0' + values[key]).encode()).hexdigest()[:16]
                groups.setdefault(value, []).append(container(row)['service'])
        if len(groups) > 1:
            found[key] = sorted(({'digest': value, 'services': sorted(services)} for value, services in groups.items()),
                                key=lambda group: (-len(group['services']), group['services']))
    return found


def log_errors(text):
    """Counts and exception class names only; log lines may carry user text and are never copied."""
    classes = Counter()
    tracebacks = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(TRACEBACK):
            tracebacks += 1
            continue
        match = EXCEPTION.match(stripped)
        if match:
            classes[match[1][:80]] += 1
    return {'tracebacks': tracebacks, 'exceptions': [name for name, _ in classes.most_common(3)]}


def container_logs(name, since='2m'):
    completed = subprocess.run(['docker', 'logs', '--since', since, '--tail', '4000', name],
                               capture_output=True, text=True, timeout=30, errors='replace')
    return log_errors(completed.stdout + '\n' + completed.stderr)


def host_facts():
    usage = shutil.disk_usage('/')
    memory = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, _, value = line.partition(':')
        memory[key] = int(value.split()[0]) if value.split() else 0
    load = os.getloadavg()
    return {'disk_free_gb': round(usage.free / 1e9, 1), 'disk_used_pct': round(100 * usage.used / usage.total, 1),
            'mem_available_mb': memory.get('MemAvailable', 0) // 1024,
            'swap_used_mb': (memory.get('SwapTotal', 0) - memory.get('SwapFree', 0)) // 1024,
            'load_1m': round(load[0], 2), 'cpus': os.cpu_count()}


def unit_results():
    results = {}
    for unit in UNITS:
        values = dict(line.partition('=')[::2] for line in run(
            ['systemctl', 'show', unit, '-p', 'LoadState', '-p', 'ActiveState', '-p', 'Result',
             '-p', 'ExecMainExitTimestamp', '-p', 'ExecMainStatus'], timeout=10).splitlines())
        if values.get('LoadState') == 'loaded':
            results[unit.removesuffix('.service')] = {
                'active': values.get('ActiveState'), 'result': values.get('Result'),
                'exit_status': values.get('ExecMainStatus'), 'exited_at': values.get('ExecMainExitTimestamp') or None}
    return results


def backup_window(units):
    """The services a running backup has stopped (2026-10-17~19 03:1x KST: a critical alert every night). A oneshot unit
    is `activating` while it runs, and systemd ends it after TimeoutStartSec, so a hung backup still alerts."""
    if (units.get('quant-company-backup') or {}).get('active') in ('activating', 'deactivating'):
        return {'services': list(BACKUP_SERVICES)}
    return None


def deploy_window(now, path=STATE / 'ops/deploy-window.json'):
    """A change window; containers it names are reported as being deployed. An operator writes `until`; release.py
    apply writes `expires_at` and a commit for each recreation and removes the file once the service settles."""
    try:
        value = json.loads(path.read_text())
        until = datetime.fromisoformat(value['until'] if 'until' in value else value['expires_at'])
        services = value['services']
        if (until.tzinfo is None or not isinstance(services, list)
                or not all(isinstance(item, str) and re.fullmatch(r'[a-z0-9-]{1,64}|\*', item) for item in services)):
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if until < now - timedelta(days=1):
        return None
    reason = value.get('reason') or (f"release {str(value['commit'])[:12]}" if value.get('commit') else '')
    return {'until': until.isoformat(), 'services': services, 'reason': str(reason)[:120]}


def scrub(text, limit=300):
    return SECRET.sub('[redacted]', str(text))[:limit] if text else None


def moment(text):
    if not isinstance(text, str):
        return None
    try:
        value = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    return value.isoformat() if value.tzinfo else None


def active_release(link=CURRENT_RELEASE):
    try:
        name = link.resolve(strict=True).name
    except OSError:
        return None
    return name if COMMIT.fullmatch(name) else None


def claude_login(auth=CLAUDE_AUTH):
    """Whether claude-runtime is logged in and when its refresh token expires; the CLI empties the login at the first
    refresh after that (2026-10-19 00:01 KST). No token is read into the result. None: Claude is not set up here."""
    if not auth.is_dir():
        return None
    path = auth / '.credentials.json'
    if not path.exists():
        return {'logged_in': False, 'refresh_expires_at': None}
    value = json.loads(path.read_text())
    login = value.get('claudeAiOauth') if isinstance(value, dict) else None
    if not isinstance(login, dict):
        raise ValueError('Unexpected Claude credentials shape')
    expiry = login.get('refreshTokenExpiresAt')
    expires = (datetime.fromtimestamp(expiry / 1000, UTC).replace(microsecond=0).isoformat()
               if isinstance(expiry, int) and expiry > 0 else None)
    return {'logged_in': bool(login.get('accessToken') or login.get('refreshToken')), 'refresh_expires_at': expires}


def claude_api(path=STATE / 'claude/jobs/.api/status.json'):
    """The Claude API credit lane's status file (written by claude-runtime, never holds the key); None when absent."""
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    if not isinstance(value, dict):
        raise ValueError('Claude API status is not an object')
    return value


def release_status(path=RELEASE_STATUS, link=CURRENT_RELEASE):
    """The release tool's last report (docs/adr/0045), reduced to what the ops watch alerts on; None until installed.

    `active` is the release running now. A report written before the last activate (a release by hand, between the
    timer's checks) names an older `current`, and its verdicts no longer describe production."""
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    if not isinstance(raw, dict):
        raise ValueError('release status is not an object')

    def commit(value):
        return value if isinstance(value, str) and COMMIT.fullmatch(value) else None

    def word(value):
        return value if isinstance(value, str) and WORD.fullmatch(value) else None

    green = raw.get('latest_green') if isinstance(raw.get('latest_green'), dict) else None
    restored = raw.get('restored') if isinstance(raw.get('restored'), dict) else None
    return {'checked_at': moment(raw.get('checked_at')), 'mode': word(raw.get('mode')), 'action': word(raw.get('action')),
            'detail': scrub(raw.get('detail')), 'current': commit(raw.get('current')), 'target': commit(raw.get('target')),
            'active': active_release(link),
            'failed_commit': commit(raw.get('failed_commit')), 'started_at': moment(raw.get('started_at')),
            'step': {'name': scrub(raw['step'].get('name'), 120), 'at': moment(raw['step'].get('at'))}
            if isinstance(raw.get('step'), dict) else None,
            'latest_green': {'sha': commit(green.get('sha')), 'completed_at': moment(green.get('completed_at'))}
            if green else None,
            'behind_by': raw['behind_by'] if type(raw.get('behind_by')) is int else None,
            'drift': [{'service': word(row.get('service')), 'problem': scrub(row.get('problem'), 120)}
                      for row in raw.get('drift') or [] if isinstance(row, dict) and not row.get('ok')][:20],
            'operator_files': [name for name in raw.get('operator_files') or []
                               if isinstance(name, str) and HOST_PATH.fullmatch(name)][:20],
            'render_files': len(raw.get('render_files') or []),
            'restored': {'ok': restored.get('ok') is True,
                         'services': [name for name in restored.get('services') or [] if word(name)][:20]}
            if restored else None}


def synthetic_until(now, path=None):
    try:
        until = datetime.fromisoformat(json.loads((path or SYNTHETIC).read_text())['until'])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return until if until.tzinfo and until > now else None


def start_synthetic(now, path=None):
    """Every snapshot until then (the timer's included) carries the test incident; it recovers after it lapses."""
    path = path or SYNTHETIC
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps({'until': (now + SYNTHETIC_FOR).isoformat()}) + '\n')
    os.replace(temp, path)


def snapshot(now=None, *, synthetic=False):
    now = now or utcnow()
    value = {'format': SNAPSHOT_FORMAT, 'collected_at': now.isoformat(), 'probe_errors': [],
             'host': None, 'containers': [], 'drift': {}, 'units': {}, 'deploy_window': deploy_window(now),
             'backup': None}
    if synthetic or synthetic_until(now):
        value['synthetic_incident'] = True
    try:
        value['host'] = host_facts()
    except (OSError, ValueError) as error:
        value['probe_errors'].append('host:' + type(error).__name__)
    try:
        rows = inspect_containers()
        value['containers'] = [container(row) for row in rows]
        value['drift'] = drift(rows, secrets.token_hex(16))
        for item in value['containers']:
            if item['status'] == 'running':
                try:
                    item['logs'] = container_logs(item['name'])
                except (OSError, subprocess.SubprocessError) as error:
                    item['logs'] = {'error': type(error).__name__}
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        value['probe_errors'].append('docker:' + type(error).__name__)
    try:
        value['units'] = unit_results()
        value['backup'] = backup_window(value['units'])
    except (OSError, subprocess.SubprocessError) as error:
        value['probe_errors'].append('systemd:' + type(error).__name__)
    try:
        value['release'] = release_status()
    except (OSError, ValueError) as error:
        value['probe_errors'].append('release:' + type(error).__name__)
    try:
        value['claude_login'] = claude_login()
    except (OSError, ValueError) as error:
        value['probe_errors'].append('claude:' + type(error).__name__)
    try:
        value['claude_api'] = claude_api()
    except (OSError, ValueError) as error:
        value['probe_errors'].append('claude-api:' + type(error).__name__)
    return value


def psql(statement, timeout=20):
    return run(['docker', 'exec', '-i', POSTGRES, 'psql', '-U', 'postgres', '-d', 'quant_company',
                '-At', '-q', '-v', 'ON_ERROR_STOP=1'], timeout=timeout, stdin=statement)


def quoted(text):
    while True:
        tag = 'ops_' + secrets.token_hex(6)
        if '$' + tag + '$' not in text:
            return '$' + tag + '$' + text + '$' + tag + '$'


def publish(value):
    payload = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    if len(payload) > 200_000:
        raise ValueError('snapshot_too_large')
    psql("INSERT INTO ops_host_snapshots(id,collected_at,payload) VALUES (1," + quoted(value['collected_at'])
         + "::timestamptz," + quoted(payload) + "::jsonb) ON CONFLICT (id) DO UPDATE SET "
         "collected_at=EXCLUDED.collected_at,payload=EXCLUDED.payload,received_at=now();\n")


def in_window(value, service, now):
    # The release tool swaps services one at a time; its own checks and restore cover them meanwhile.
    if (value.get('release') or {}).get('action') == 'releasing' and service != 'postgres':
        return True
    if service in ((value.get('backup') or {}).get('services') or ()):
        return True
    window = value.get('deploy_window')
    return bool(window and datetime.fromisoformat(window['until']) > now
                and ('*' in window['services'] or service in window['services']))


def silencers(value, now, watch_age):
    """Failures that stop the in-app watch from posting. Each maps a stable key to Korean text."""
    found = {}
    by_service = {item['service']: item for item in value['containers']}
    database = by_service.get('postgres')
    if (not database or database['status'] != 'running' or database['health'] == 'unhealthy') \
            and not in_window(value, 'postgres', now):
        found['postgres'] = 'PostgreSQL이 꺼져 있거나 응답하지 않습니다. 앱 안의 감시·발송이 모두 멈춘 상태입니다.'
    dispatch = by_service.get('dispatch')
    if (not dispatch or dispatch['status'] != 'running') and not in_window(value, 'dispatch', now):
        found['dispatch'] = 'dispatch(Slack 발송)가 꺼져 있습니다. 봇 답변과 알림이 나가지 않습니다.'
    if 'postgres' not in found and 'dispatch' not in found and watch_age is not None and watch_age > WATCH_STALE:
        minutes = int(watch_age.total_seconds() // 60)
        found['watch'] = f'운영 감시가 {minutes}분째 기록되지 않았습니다. dispatch 루프가 멈췄을 수 있습니다.'
    return found


def watch_age():
    """None when the in-app watch has never run (not enabled yet): no fallback alert for it."""
    text = psql("SELECT extract(epoch FROM now()-ticked_at) FROM ops_watch_state WHERE id=1;\n").strip()
    return timedelta(seconds=float(text)) if text else None


def post(token, channel, text, timeout=10):
    body = json.dumps({'channel': channel, 'text': text, 'unfurl_links': False}).encode()
    request = urllib.request.Request('https://slack.com/api/chat.postMessage', data=body, method='POST',
                                     headers={'Authorization': 'Bearer ' + token,
                                              'Content-Type': 'application/json; charset=utf-8'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def fallback(value, now, *, config, credentials_path, state_path, sender=post):
    """Announce each silencing failure once and its recovery once. An ambiguous send is recorded and
    never repeated automatically."""
    channel, owner = config.get('OPS_ALERT_CHANNEL_ID', ''), config.get('OPS_WATCH_OWNER_USER', '')
    # A second Slack sender besides dispatch: it needs its own owner decision, not just the watch being on.
    if (config.get('OPS_WATCH_ENABLED') != 'true' or config.get('OPS_FALLBACK_ENABLED') != 'true'
            or not SLACK_ID.fullmatch(channel) or not channel.startswith('C')):
        return {'state': 'disabled'}
    age = None
    database_up = any(item['service'] == 'postgres' and item['status'] == 'running' for item in value['containers'])
    if database_up:
        try:
            age = watch_age()
        except (OSError, ValueError, subprocess.SubprocessError):
            age = None
    found = silencers(value, now, age)
    state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(state_path.with_suffix('.lock'), 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = json.loads(state_path.read_text())
        except (OSError, ValueError):
            state = {'active': {}}
        active = state.setdefault('active', {})
        notices = [(key, '🔴 *운영 비상 알림*\n' + text, True) for key, text in found.items() if key not in active]
        notices += [(key, '✅ *운영 비상 알림 해소*\n' + {'postgres': 'PostgreSQL', 'dispatch': 'dispatch',
                                                       'watch': '운영 감시'}[key] + '가 다시 동작합니다.', False)
                    for key, record in active.items() if key not in found and record.get('announced')]
        for key in [key for key, record in active.items() if key not in found and not record.get('announced')]:
            del active[key]
        token = None
        if notices:
            role = json.loads(credentials_path.read_text()).get('director') or {}
            token = role.get('bot_token') or None
        sent = []
        for key, text, opening in notices:
            if opening:
                active[key] = {'since': now.isoformat(), 'announced': False}
                if SLACK_ID.fullmatch(owner) and owner.startswith(('U', 'W')):
                    text = f'<@{owner}>\n' + text
            outcome = 'not_configured'
            if token:
                try:
                    result = sender(token, channel, text)
                    outcome = 'delivered' if result.get('ok') else 'rejected:' + str(result.get('error', ''))[:60]
                except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                    outcome = 'uncertain'
            if opening:
                # A rejected opening notice is retried next minute; a delivered or ambiguous one never is.
                active[key]['announced'] = outcome in {'delivered', 'uncertain'}
                active[key]['outcome'] = outcome
                if not active[key]['announced']:
                    del active[key]
            elif outcome in {'delivered', 'uncertain'}:
                del active[key]
            sent.append({'key': key, 'opening': opening, 'outcome': outcome})
        target = state_path.with_suffix('.tmp')
        target.write_text(json.dumps(state, indent=2) + '\n')
        target.chmod(0o600)
        os.replace(target, state_path)
    return {'state': 'checked', 'silencers': sorted(found), 'notices': sent}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--print', action='store_true', help='preview only (the default): print the snapshot, write nothing')
    parser.add_argument('--publish', action='store_true', help='upsert the snapshot into PostgreSQL')
    parser.add_argument('--fallback', action='store_true', help='direct Slack notice when the in-app watch is silenced')
    parser.add_argument('--synthetic-incident', action='store_true',
                        help='end-to-end alert test: with --publish, snapshots carry a test incident for 3 minutes')
    parser.add_argument('--env-file', type=Path, default=STATE / 'config/runtime.env')
    parser.add_argument('--credentials', type=Path, default=STATE / 'secrets/slack-credentials.json')
    parser.add_argument('--state', type=Path, default=STATE / 'ops/fallback-state.json')
    args = parser.parse_args(argv)
    if args.print and (args.publish or args.fallback):
        parser.error('--print writes nothing; drop --publish/--fallback')
    now = utcnow()
    if args.synthetic_incident and args.publish:
        start_synthetic(now)
    value = snapshot(now, synthetic=args.synthetic_incident)
    receipt = {'collected_at': value['collected_at'], 'containers': len(value['containers']),
               'probe_errors': value['probe_errors'], 'published': False}
    if args.publish:
        try:
            publish(value)
            receipt['published'] = True
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            receipt['publish_error'] = type(error).__name__
    if args.fallback:
        receipt['fallback'] = fallback(value, now, config=configuration(args.env_file),
                                       credentials_path=args.credentials, state_path=args.state)
    print(json.dumps(receipt if args.publish or args.fallback else value, ensure_ascii=False, indent=None))


if __name__ == '__main__':
    main()
