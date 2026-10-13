# Run delivery readback in dispatch, where the existing Slack credential mount belongs.
# Producer-only processes intentionally have no Slack credential file.
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime

from slack_sdk import WebClient

from quant_company.company import Company, as_json
from quant_company.config import Settings
from quant_company.runtime import connect
from quant_company.trend_feed.editor import EDITORIAL_POLICY_VERSION, publication_items
from quant_company.trend_feed.store import TrendFeedStore

c = Company(Settings())
store = TrendFeedStore(c)
key = 'operator-qualification:trend-window:a55478f:20261008'
result = {'checked_at':datetime.now(UTC).isoformat(),
          'input':'operator qualification explicitly authorized in chat; not a new Slack SDK event',
          'editorial_policy':EDITORIAL_POLICY_VERSION, 'policy_digest':store.policy(),
          'configured_model_provider':c.settings.model_provider,'fixture_mode':c.settings.fixture_mode}
assert EDITORIAL_POLICY_VERSION==4 and store.authorized()
if len(sys.argv)>1 and sys.argv[1]=='request':
    result['request'] = store.request(key,owner=c.settings.trend_feed_owner_user,
        channel=c.settings.trend_feed_channel_id,thread_ts='1791425245.175969')
with c.db.transaction() as conn:
    result['digests'] = conn.execute("""SELECT d.id,d.kind,d.state,d.requested_at,d.cutoff,d.send_at,d.expires_at,
        d.thread_ts,d.enriched,d.reused_digest_id,o.status,o.sent_ts,o.attempts,o.started_at,o.error,
        md5(o.text) AS body_hash,d.draft,d.bundle,o.text FROM trend_feed_digests d LEFT JOIN outbox o ON o.id=d.id
        WHERE d.event_key=%s""",(key,)).fetchall()
    result['calls'] = conn.execute("""SELECT x.id,x.stage,x.state,x.error,x.created_at,x.completed_at,
        x.request->>'model' AS model,x.response->>'provider' AS response_provider
        FROM trend_feed_calls x JOIN trend_feed_digests d ON d.id=x.digest_id WHERE d.event_key=%s""",(key,)).fetchall()
    result['budget_today'] = conn.execute("""SELECT d.kind,count(*) AS n FROM trend_feed_calls x
        JOIN trend_feed_digests d ON d.id=x.digest_id
        WHERE (x.created_at AT TIME ZONE 'Asia/Seoul')::date=(now() AT TIME ZONE 'Asia/Seoul')::date GROUP BY d.kind""").fetchall()
    result['naver_today'] = conn.execute("SELECT calls FROM trend_feed_api_usage WHERE day=(now() AT TIME ZONE 'Asia/Seoul')::date").fetchone()
    result['pause'] = conn.execute('SELECT paused_until,reason FROM runtime_control WHERE id=1').fetchone()
    result['source'] = conn.execute('SELECT last_success,next_at,failures,error FROM trend_feed_source WHERE id=1').fetchone()
for row in result['digests']:
    bundle,draft = row.pop('bundle'),row.pop('draft')
    selected = publication_items(draft,bundle)
    candidates = {v['id']:v for v in bundle['candidates']}
    row['item_count'] = len(selected)
    row['candidate_count'] = len(candidates)
    row['window'] = bundle.get('ranking_window')
    row['collection_gap'] = bundle['collection_gap']
    row['partial_history'] = bundle['partial_history']
    row['selected'] = [{'titles':[candidates[k]['title'] for k in item['member_ids']],
                        'category':item['category'],'kinds':[candidates[k]['kind'] for k in item['member_ids']],
                        'observation':candidates[item['member_ids'][0]].get('observation'),
                        'naver':candidates[item['member_ids'][0]].get('naver')} for item in selected]
    if row['text']:
        row['text_sha256'] = hashlib.sha256(row['text'].encode()).hexdigest()
    if row['status']=='delivered':
        cred = json.loads(c.settings.slack_credentials_file.read_text())['trend_scout']
        client = WebClient(token=cred['bot_token'],timeout=12)
        auth = client.auth_test()
        row['slack_auth'] = {k:auth.get(k) for k in ('ok','team_id','user_id')}
        link = client.chat_getPermalink(channel=c.settings.trend_feed_channel_id,message_ts=row['sent_ts'])
        row['permalink'] = link.get('permalink')
        row['permalink_ok'] = link.get('ok')


async def workflows():
    client = await connect(c.settings)
    result = {}
    for identity in ('company-trend-feed-collection-v1','company-trend-feed-editorial-v1','company-trend-feed-publication-v2'):
        description = await client.get_workflow_handle(identity).describe()
        result[identity] = {'status':description.status.name,'run_id':description.run_id}
    return result


result['workflows'] = asyncio.run(workflows())
result['next_publication'] = store.status()['next_publication']
print(json.dumps(as_json(result),ensure_ascii=False))
