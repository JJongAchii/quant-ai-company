"""One explicitly requested filtered briefing, from the already validated real source, via durable outbox."""

import hashlib
import json
import re
import sys

from quant_company.company import Company, as_json, stable
from quant_company.config import Settings
from quant_company.contracts import ProviderResponse
from quant_company.trend_feed import editor, schedule
from quant_company.trend_feed.store import LOCK, TrendFeedStore

SOURCE = '012716d1-68d5-52fe-839e-047a2c9b99ef'
APPROVAL = 'chat-user-send-sports-now-20261006'
ID = stable('trend-feed-sports-immediate:20261006:C0C6WTA9ECV:user-send-now-1')


if __name__ == '__main__':
    action = sys.argv[1]
    if action not in {'queue', 'inspect'}:
        raise RuntimeError('unsupported_action')
    company = Company(Settings())
    store = TrendFeedStore(company)
    settings = company.settings
    if (not store.authorized() or not settings.trend_feed_publish_enabled or editor.EDITORIAL_POLICY_VERSION != 2
            or settings.slack_team_id != 'T0C1YRDRPNF' or settings.trend_feed_channel_id != 'C0C6WTA9ECV'
            or settings.trend_feed_owner_user != 'U0C250E23NW'):
        raise RuntimeError('immediate_delivery_scope_changed')
    with company.db.transaction() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK,))
        source = conn.execute('SELECT bundle,draft,content,cutoff FROM trend_feed_digests WHERE id=%s', (SOURCE,)).fetchone()
        calls = conn.execute("SELECT response FROM trend_feed_calls WHERE digest_id=%s AND state='completed'", (SOURCE,)).fetchall()
        if not source or not source['draft']:
            raise RuntimeError('verified_source_missing')
        bundle, draft = source['bundle'], source['draft']
        if draft not in [editor.validate_draft(ProviderResponse.model_validate(c['response']), bundle).model_dump() for c in calls]:
            raise RuntimeError('source_classification_not_verified')
        selected = editor.publication_items(draft)
        if not selected or any(item['category']=='스포츠' for item in selected):
            raise RuntimeError('non_sports_topics_required')
        cutoff = source['cutoff'].astimezone(schedule.KST)
        lines = editor.render(bundle, draft).splitlines()
        lines[0] = '*한국 검색 트렌드 · 스포츠 제외 브리핑*'
        lines[1] = f'{cutoff:%m/%d %H:%M KST} 마감 자료 · 앞서 검증한 자료의 스포츠 제외 적용 결과'
        text = '\n'.join(lines)
        if len(re.findall(r'^\*\d+\.', text, re.MULTILINE)) != len(selected):
            raise RuntimeError('rendered_topic_count_mismatch')
        old = conn.execute('SELECT text,author FROM messages WHERE id=%s', (ID,)).fetchone()
        if old and (old['text'] != text or old['author'] != 'trend_scout'):
            raise RuntimeError('committed_message_changed')
        created = False
        if action == 'queue' and not old:
            project_id = stable('trend-feed-project:C0C6WTA9ECV:U0C250E23NW')
            project = conn.execute('SELECT * FROM projects WHERE id=%s FOR UPDATE', (project_id,)).fetchone()
            if not project or project['channel'] != settings.trend_feed_channel_id or project['owner_user'] != settings.trend_feed_owner_user:
                raise RuntimeError('project_scope_changed')
            company._message(conn, project, None, 'trend_scout', 'status', text, message_id=ID)
            company._event(conn, 'trend_sports_immediate_delivery_authorized',
                {'approval': APPROVAL, 'source_request': '지금 보내봐', 'message_id': ID, 'source_digest_id': SOURCE,
                 'source_cutoff': cutoff.isoformat(), 'source_policy': store.policy(), 'sports_selected': 0,
                 'topics': len(selected), 'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
                 'scope': 'one explicit immediate delivery; preserve existing messages and scheduled daily slot'}, project_id)
            created = True
        receipt = conn.execute('SELECT id,status,channel,sent_ts,attempts,error FROM outbox WHERE id=%s', (ID,)).fetchone()
        original = conn.execute('SELECT text FROM messages WHERE id=%s', (SOURCE,)).fetchone()
        original_unchanged = bool(original and original['text'] == source['content'])
        if not original_unchanged:
            raise RuntimeError('original_delivered_body_changed')
    print(json.dumps(as_json({'approval': APPROVAL, 'id': ID, 'created_now': created, 'checked_at': schedule.utcnow(),
        'source_digest_id': SOURCE, 'source_cutoff': cutoff, 'selected_topics': len(selected), 'sports_selected': 0,
        'text': text, 'text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'receipt': receipt,
        'original_delivered_body_preserved': original_unchanged, 'regular_publication_enabled': True,
        'next_publication': schedule.next_publication(schedule.utcnow()), 'model_calls_made': 0,
        'naver_calls_made': 0, 'automatic_uncertain_replay': False}), ensure_ascii=False))
