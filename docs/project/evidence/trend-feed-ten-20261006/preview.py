"""One persisted subscription preview of verified inputs; no Slack writes or source/API calls."""

import asyncio
import hashlib
import json
import re
import sys
from datetime import timedelta
from pathlib import Path

from psycopg.types.json import Jsonb

from quant_company.company import Company, as_json, stable
from quant_company.config import Settings
from quant_company.contracts import ProviderResponse
from quant_company.trend_feed import editor, schedule
from quant_company.trend_feed.sources import article_source
from quant_company.trend_feed.store import LOCK, TrendFeedStore
from quant_company.trend_feed.supplement import major_issues

SOURCE_ID = '012716d1-68d5-52fe-839e-047a2c9b99ef'
ID = stable('trend-feed-ten-preview:20261006:C0C6WTA9ECV')
APPROVAL = 'chat-user-ten-topics-20261006'
FILES = ('contracts.py', 'editor.py', 'runner.py', 'store.py', 'supplement.py')


def check(store, conn, row):
    if not row:
        raise RuntimeError('ten_topic_preview_missing')
    calls = conn.execute('SELECT * FROM trend_feed_calls WHERE digest_id=%s ORDER BY stage', (ID,)).fetchall()
    if not row['draft']:
        return {'valid': False, 'state': 'editorial_pending', 'digest_id': ID,
                'calls': [{k: c[k] for k in ('id', 'state', 'error', 'next_at')} for c in calls], 'automatic_new_request': False}
    drafts = [editor.validate_draft(ProviderResponse.model_validate(c['response']), row['bundle']).model_dump()
              for c in calls if c['state'] == 'completed' and c['response']]
    if row['draft'] not in drafts or row['policy_digest'] != store.policy():
        raise RuntimeError('ten_topic_editorial_or_policy_not_verified')
    candidates = {c['id']: c for c in row['bundle']['candidates']}
    selected = editor.publication_items(row['draft'], row['bundle'])
    text = editor.render(row['bundle'], row['draft'])
    text = '*한국 검색 트렌드 · 10개 구성 검증 미리보기*\n' + text.split('\n', 1)[1]
    cards = len(re.findall(r'^\*\d+\.', text, re.MULTILINE))
    if cards != 10 or len(selected) != 10 or any(i['category'] == '스포츠' for i in selected):
        raise RuntimeError('ten_non_sports_topics_not_verified')
    current_major = {c['id']: c for c in major_issues(conn, store.company.settings, row['cutoff'])}
    for item in selected:
        for key in item['member_ids']:
            candidate = candidates[key]
            if candidate.get('kind') == 'major_issue' and current_major.get(key) != candidate:
                raise RuntimeError('accepted_news_source_changed')
            if any(not article_source(a['url'], store.company.settings) for a in candidate['articles']):
                raise RuntimeError('unapproved_original')
    day = row['cutoff'].astimezone(schedule.KST).replace(hour=0, minute=0, second=0, microsecond=0)
    count = conn.execute('SELECT count(*) AS n FROM trend_feed_calls WHERE created_at>=%s AND created_at<%s',
                         (day, day+timedelta(days=1))).fetchone()['n']
    usage = conn.execute('SELECT calls FROM trend_feed_api_usage WHERE day=%s', (day.date(),)).fetchone()
    original = conn.execute('SELECT d.content,m.text,o.status FROM trend_feed_digests d '
                            'JOIN messages m ON m.id=d.id JOIN outbox o ON o.id=d.id WHERE d.id=%s', (SOURCE_ID,)).fetchone()
    if count > 2 or (usage and usage['calls'] > 100) or not original or original['content'] != original['text']:
        raise RuntimeError('budget_or_delivered_body_changed')
    if conn.execute('SELECT 1 FROM outbox WHERE id=%s', (ID,)).fetchone():
        raise RuntimeError('preview_must_not_be_sent')
    package = Path(editor.__file__).parent
    return {'valid': True, 'checked_at': schedule.utcnow(), 'digest_id': ID, 'source_digest_id': SOURCE_ID,
            'source_cutoff': row['cutoff'], 'editorial_policy_version': editor.EDITORIAL_POLICY_VERSION,
            'classified_candidates': len(row['draft']['items']), 'cards': cards, 'sports_selected': 0,
            'selected': [{'titles': [candidates[k]['title'] for k in item['member_ids']], 'category': item['category'],
                          'kind': 'rising_search' if any(candidates[k].get('kind') != 'major_issue' for k in item['member_ids'])
                          else 'major_issue'} for item in selected],
            'text': text, 'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'source_sha256': {'trend_feed/'+name: hashlib.sha256((package/name).read_bytes()).hexdigest() for name in FILES},
            'model_requests_today': count, 'naver_requests_today': usage['calls'] if usage else 0,
            'calls': [{'id': c['id'], 'state': c['state'], 'model': c['request']['model'],
                       'reasoning_effort': c['request']['reasoning_effort'],
                       'provider': c['response'].get('provider') if c['response'] else None} for c in calls],
            'original_body_unchanged': True, 'additional_slack_calls': 0, 'additional_naver_calls': 0,
            'automatic_uncertain_replay': False}


async def main(action):
    company = Company(Settings())
    store = TrendFeedStore(company)
    if not store.authorized() or company.settings.model_provider == 'fixture':
        raise RuntimeError('real_authorized_company_required')
    if action == 'prepare':
        with store.db.transaction() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK,))
            row = conn.execute('SELECT * FROM trend_feed_digests WHERE id=%s', (ID,)).fetchone()
            if row:
                raise RuntimeError('preview_already_prepared_use_inspect')
            original = conn.execute('SELECT * FROM trend_feed_digests WHERE id=%s', (SOURCE_ID,)).fetchone()
            if not original or not original['draft']:
                raise RuntimeError('verified_google_input_missing')
            bundle = original['bundle']
            bundle['candidates'].extend(major_issues(conn, company.settings, original['cutoff']))
            if not any(c.get('kind') == 'major_issue' for c in bundle['candidates']):
                raise RuntimeError('accepted_major_issues_missing')
            at = schedule.utcnow()
            conn.execute('INSERT INTO trend_feed_digests '
                         '(id,day,geo,channel,owner_user,cutoff,send_at,expires_at,policy_digest,bundle,enriched) '
                         "VALUES(%s,%s,'KR-ten-validation',%s,%s,%s,%s,%s,%s,%s,true)",
                         (ID, at.astimezone(schedule.KST).date(), original['channel'], original['owner_user'],
                          original['cutoff'], at+timedelta(minutes=30), at+timedelta(hours=1), store.policy(), Jsonb(bundle)))
            company._event(conn, 'trend_ten_topic_preview_authorized', {'approval': APPROVAL, 'digest_id': ID,
                           'source_digest_id': SOURCE_ID, 'scope': 'one subscription validation; no Slack publication'})
        from quant_company.trend_feed.runner import TrendFeedEditor

        await TrendFeedEditor(company).tick()
    with store.db.transaction() as conn:
        if action == 'inspect':
            conn.execute('SET TRANSACTION READ ONLY')
        row = conn.execute('SELECT * FROM trend_feed_digests WHERE id=%s', (ID,)).fetchone()
        result = check(store, conn, row)
        if result['valid'] and action == 'prepare':
            conn.execute("UPDATE trend_feed_digests SET state='preview',content=%s WHERE id=%s",
                         (result['text'], ID))
    print(json.dumps(as_json(result), ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else 'inspect'))
