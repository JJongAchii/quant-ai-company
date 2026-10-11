# ruff: noqa: E402
# Qualified modules must be loaded before the service imports below.
import json
import sys
import types
from datetime import UTC, datetime

import quant_company.trend_feed

# This process only reads production records; the running service is not patched.
sources = json.load(sys.stdin)
for name in ('ranking', 'editor', 'store'):
    key = 'quant_company.trend_feed.'+name
    module = types.ModuleType(key)
    module.__package__ = 'quant_company.trend_feed'
    sys.modules[key] = module
    exec(compile(sources[name], '<qualified-trend-window/'+name+'>', 'exec'), module.__dict__)
    setattr(quant_company.trend_feed, name, module)

from quant_company.company import Company, as_json
from quant_company.config import Settings
from quant_company.trend_feed.editor import publication_items, render
from quant_company.trend_feed.store import TrendFeedStore

c = Company(Settings())
store = TrendFeedStore(c)
at = datetime.now(UTC)
with c.db.transaction() as conn:
    conn.execute('SET TRANSACTION READ ONLY')
    prior = conn.execute("SELECT bundle,draft FROM trend_feed_digests WHERE draft IS NOT NULL ORDER BY created_at DESC LIMIT 20").fetchall()
    bundles = {mode: store.bundle(conn, at, on_demand=mode=='request') for mode in ('scheduled', 'request')}
categories = {}
for row in prior:
    candidates = {v['id']: v for v in row['bundle']['candidates']}
    for item in row['draft']['items']:
        for key in item['member_ids']:
            categories.setdefault((key, candidates[key]['title']), item['category'])
result = {'checked_at': at.isoformat(), 'mode': 'read-only actual observation preview; prior verified categories only',
          'new_model_calls': 0, 'slack_calls': 0, 'database_writes': 0, 'previews': {}}
for mode,bundle in bundles.items():
    all_candidates = bundle['candidates']
    unknown = [v['title'] for v in all_candidates if (v['id'],v['title']) not in categories]
    bundle['candidates'] = [v for v in all_candidates if (v['id'],v['title']) in categories]
    bundle['on_demand'] = mode=='request'
    draft = {'items': [{'member_ids':[v['id']], 'category':categories[(v['id'],v['title'])],
                        'background':'', 'evidence':[]} for v in bundle['candidates']]}
    result['previews'][mode] = {'window':bundle['ranking_window'], 'unclassified_omitted':unknown,
        'candidate_count':len(all_candidates), 'snapshot_count':len(bundle['snapshot_ids']),
        'selected_count':len(publication_items(draft,bundle)), 'text':render(bundle,draft),
        'ranking':[{'title':v['title'], 'traffic':v.get('traffic'), 'last_seen':v.get('last_seen'),
                    'observation':v.get('observation')} for v in all_candidates if v['kind']=='rising_search']}
print(json.dumps(as_json(result), ensure_ascii=False))
