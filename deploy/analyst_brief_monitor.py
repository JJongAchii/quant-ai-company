"""Native PostgreSQL metadata monitoring; no Company instance, model or Slack call."""

import argparse
import json
import os
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

CHECKS = {'numbers', 'sources', 'timing', 'causality', 'materiality', 'counterevidence',
          'transmission', 'alternatives', 'falsifiability', 'coverage', 'depth', 'readability'}


def stamp(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def assess(definition, row, now, *, maximum_requests=6):
    due = stamp(definition['due_at'])
    deadline = due+timedelta(minutes=10)
    base = {k: definition[k] for k in ('id', 'day', 'kind', 'due_at', 'starts_at', 'cutoff')}
    if row is None:
        return {**base, 'state': 'waiting' if now < stamp(definition['starts_at']) else 'missing',
                'content_passed': False, 'problems': ['edition_not_registered'] if now >= deadline else [],
                'model_requests': 0, 'delivery': 'not_requested'}
    checks = row.get('checks') or {}
    calls = row.get('calls') or []
    completed = {c['phase'] for c in calls if c['state'] == 'completed' and c['provider'] == 'codex'}
    required = {'plan', 'inventory', 'write', 'review'}
    if any(c['phase'] in {'revise', 'final_review'} for c in calls):
        required |= {'revise', 'final_review'}
        if row.get('source_notes_repair'):
            required.discard('review')
    content_passed = bool(row.get('verdict') == 'publish' and set(checks) == CHECKS
                          and all(v is True for v in checks.values()) and row.get('substantive')
                          and not row.get('reduced') and not row.get('rejections')
                          and row.get('inventory_digest') and required <= completed)
    problems = []
    if row.get('error'):
        problems.append('execution_failed')
    failed_checks = sorted(k for k in CHECKS if checks.get(k) is not True) if checks else []
    if failed_checks:
        problems.append('content_review_failed')
    if row.get('rejections'):
        problems.append('mechanical_rejections')
    if len(calls) > maximum_requests:
        problems.append('model_request_budget_exceeded')
    if now >= deadline and not row.get('committed_at'):
        problems.append('edition_not_finalized')
    if row.get('committed_at') and stamp(row['committed_at']) > deadline:
        problems.append('late_brief')
    if now >= deadline and not content_passed:
        problems.append('full_content_not_qualified')
    delivered = row.get('delivered_messages') or 0
    expected = row.get('expected_messages') or 0
    delivery = 'preview_only' if not row['publish'] else 'confirmed' if expected and delivered == expected else 'pending'
    if row['publish'] and now >= deadline and delivery != 'confirmed':
        problems.append('slack_delivery_incomplete')
    phases = []
    for c in calls:
        start, end = stamp(c.get('requested_at')), stamp(c.get('completed_at'))
        phases.append({'phase': c['phase'], 'state': c['state'], 'error': c.get('error'),
                       'elapsed_from_request_seconds': round((end-start).total_seconds(), 3) if start and end else None})
    return {**base, 'state': row['state'], 'content_passed': content_passed,
            'checks_passed': sum(v is True for v in checks.values()), 'checks_required': 12,
            'failed_checks': failed_checks, 'problems': problems, 'model_requests': len(calls),
            'phases': phases, 'inventory_digest': row.get('inventory_digest'),
            'policy_digest': row['policy_digest'], 'committed_at': row.get('committed_at'),
            'delay_seconds': round((stamp(row['committed_at'])-due).total_seconds(), 3) if row.get('committed_at') else None,
            'delivery': delivery, 'delivered_messages': delivered, 'expected_messages': expected,
            'source_count': row.get('source_count'), 'collection_error_count': row.get('collection_error_count')}


def query(ids):
    # Canonical UUID validation is the SQL boundary. Never interpolate env values.
    literal = ','.join("'"+str(UUID(value))+"'" for value in ids)
    return """SELECT COALESCE(json_agg(json_build_object(
      'id',e.id,'state',e.state,'publish',e.publish,'policy_digest',e.policy_digest,
      'committed_at',e.committed_at,'error',e.error,
      'checks',e.review->'checks','verdict',e.review->'verdict',
      'substantive',e.quality->'substantive','reduced',e.quality->'reduced',
      'rejections',e.quality->'rejected','inventory_digest',e.bundle->>'fact_inventory_digest',
      'source_notes_repair',e.bundle->'source_notes_repair'->'before_independent_review',
      'source_count',jsonb_array_length(COALESCE(e.bundle->'documents','[]'::jsonb)),
      'collection_error_count',jsonb_array_length(COALESCE(e.bundle->'collection_errors','[]'::jsonb)),
      'expected_messages',jsonb_array_length(COALESCE(e.rendered,'[]'::jsonb)),
      'delivered_messages',(SELECT count(*) FROM brief_messages m JOIN outbox o ON o.id=m.id
          WHERE m.edition_id=e.id AND o.status='delivered' AND o.sent_ts IS NOT NULL),
      'calls',COALESCE((SELECT json_agg(json_build_object('phase',c.phase,'state',c.state,
          'provider',c.response->>'provider','requested_at',c.requested_at,'completed_at',c.completed_at,
          'error',c.error) ORDER BY c.requested_at) FROM brief_calls c WHERE c.edition_id=e.id),'[]'::json)
    )), '[]'::json) FROM brief_editions e WHERE e.id IN ("""+literal+");"


def atomic(path, value):
    target = path.with_suffix('.tmp')
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    target.chmod(0o600)
    os.replace(target, path)


def definitions(policy, now):
    if policy.get('mode') != 'continuous':
        return policy['editions']
    assert re.fullmatch(r'[CG][A-Z0-9]+', policy['channel'])
    assert re.fullmatch(r'U[A-Z0-9]+', policy['owner'])
    worker = json.loads(subprocess.check_output(
        ['docker', 'inspect', 'quant-company-briefing-data-worker-1'], timeout=15))[0]
    env = dict(value.split('=', 1) for value in worker['Config']['Env'] if '=' in value)
    assert env.get('BRIEFING_CHANNEL_ID') == policy['channel']
    assert env.get('BRIEFING_OWNER_USER') == policy['owner']
    overrides = []
    if path := env.get('BRIEFING_CALENDAR_OVERRIDES_FILE'):
        overrides = json.loads(subprocess.check_output(
            ['docker', 'exec', 'quant-company-briefing-data-worker-1', 'cat', '--', path], timeout=15))
    # Use the installed calendar without creating Company or loading credentials.
    # A separate, network-isolated process avoids pressure on the live API/worker.
    code = '''import json,sys
from datetime import datetime,timedelta
from quant_company.briefing.contracts import CalendarOverride
from quant_company.briefing.schedule import KST,editions
p=json.load(sys.stdin);now=datetime.fromisoformat(p['now']).astimezone(KST)
changes=[CalendarOverride.model_validate(v) for v in p['overrides']]
changes={(v.market,v.day):v for v in changes}
out=[d.model_dump(mode='json') for offset in range(-7,2)
 for d in editions(now.date()+timedelta(days=offset),p['channel'],p['owner'],changes)]
print(json.dumps(out))
'''
    values = json.loads(subprocess.check_output([
        'docker', 'run', '--rm', '-i', '--network=none', '--memory=384m', '--pids-limit=64',
        '--read-only', '--entrypoint=python', worker['Config']['Image'], '-c', code],
        input=json.dumps({'now': now.isoformat(), 'channel': policy['channel'],
                          'owner': policy['owner'], 'overrides': overrides}).encode(), timeout=35))
    starts = stamp(policy['starts_at'])
    return [d for d in values if stamp(d['due_at']) >= starts]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--state', type=Path, default=Path('/var/lib/quant-company'))
    args = parser.parse_args()
    policy = json.loads(args.policy.read_text())
    now = datetime.now(UTC)
    expected = definitions(policy, now)
    ids = [str(UUID(d['id'])) for d in expected]
    assert len(ids) == len(set(ids)) and len(ids) <= 24
    assert ids or policy.get('mode') == 'continuous'
    env = dict(line.split('=', 1) for line in (args.state/'config/runtime.env').read_text().splitlines()
               if '=' in line and not line.lstrip().startswith('#'))
    database = env.get('DATABASE_NAME', 'quant_company')
    assert re.fullmatch(r'[a-zA-Z0-9_]+', database)
    raw = (subprocess.check_output(['docker', 'exec', 'quant-company-postgres-1', 'psql', '-U', 'postgres',
                                   '-d', database, '-X', '-Atc', query(ids)], timeout=30) if ids else b'[]')
    rows = {r['id']: r for r in json.loads(raw)}
    report = {'recorded_at': now.isoformat(), 'policy': policy['case'],
              'publication_enabled': env.get('BRIEFING_PUBLISH_ENABLED') == 'true',
              'editions': [assess(d, rows.get(d['id']), now,
                                  maximum_requests=policy.get('maximum_distinct_calls_per_edition', 6))
                           for d in expected],
              'model_calls': 0, 'slack_calls': 0, 'database_mutations': 0,
              'automatic_publication_changed': False,
              'scope': 'Operational metadata; independent content review and human acceptance remain separate.'}
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic(args.output/'latest.json', report)
    for row in report['editions']:
        if row['state'] != 'waiting':
            atomic(args.output/(row['day']+'-'+row['kind']+'.json'), row)
    print(json.dumps({'recorded_at': report['recorded_at'], 'editions': len(expected),
                      'failures': [r['id'] for r in report['editions'] if r['problems']],
                      'publication_enabled': report['publication_enabled'], 'model_calls': 0}))


if __name__ == '__main__':
    main()
