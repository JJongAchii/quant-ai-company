import hashlib
import json
import math
from collections import Counter
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

root = Path.cwd()
inputs = root / '.local/science-inputs/kr_etf-prepared-v2'
program = json.loads((root / 'docs/project/evidence/research-programs-20260928/first-program-draft.json').read_text())['program']
prepared = json.loads((root / 'docs/project/evidence/research-programs-20260928/real-inputs.json').read_text())['markets']['kr_etf']
receipt_path = inputs / 'receipt.json'
receipt = json.loads(receipt_path.read_text())
spec = next(e['template'] for e in program['envelopes'] if e['name'] == 'etf_strategy')
assert spec['data']['input_files'] == prepared['input_files'] == receipt['input_files']
assert spec['data']['lake_id'] == 'krx-s3-kr_etf-30784209bf06e53313b8'
assert spec['execution_profile'] == prepared['profile']['execution_profile'] == 'kr-etf-research-v2'
assert spec['execution_profile_digest'] == prepared['profile']['execution_profile_digest']
assert receipt['state'] == prepared['state'] == 'ready'
assert hashlib.sha256(receipt_path.read_bytes()).hexdigest() == prepared['data_receipt_sha256']
cohort = set(prepared['cohort'])
assert len(cohort) == 10
required = {'date','ticker','market','open','close','adj_close','value','available_at','tradable'}
local_zone = ZoneInfo('Asia/Seoul')
summary = {}
all_dates = {}
for name in ('warmup.json','development.json'):
    path = inputs / name
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == spec['data']['input_files'][name]
    rows = json.loads(path.read_text())
    assert isinstance(rows,list) and rows
    by_date = Counter()
    seen = set()
    for row in rows:
        assert isinstance(row,dict) and set(row)==required
        d = date.fromisoformat(row['date'])
        ticker = row['ticker']
        assert isinstance(ticker,str) and len(ticker)==6 and ticker.isdigit() and ticker in cohort
        assert row['market']=='kr_etf' and type(row['tradable']) is bool
        assert (d,ticker) not in seen
        seen.add((d,ticker))
        by_date[d] += 1
        for field in ('open','close','adj_close','value'):
            value=row[field]
            assert type(value) in (int,float) and math.isfinite(value) and value>=0
        if row['tradable']:
            assert row['open']>0 and row['close']>0 and row['adj_close']>0
        available=datetime.fromisoformat(row['available_at'])
        assert available.tzinfo is not None and available.utcoffset() is not None
        assert available.astimezone(local_zone).date()==d
        assert available.astimezone(local_zone).time()>=time(18,0)
    dates=sorted(by_date)
    assert all(n==len(cohort) for n in by_date.values())
    all_dates[name]=dates
    late=0
    for row in rows:
        d=date.fromisoformat(row['date'])
        idx=dates.index(d)
        if idx+1==len(dates):
            continue
        next_signal=datetime.combine(dates[idx+1],time(8,30),tzinfo=local_zone)
        if datetime.fromisoformat(row['available_at'])>next_signal:
            late+=1
    assert late==0
    summary[name]={'sha256':digest,'rows':len(rows),'sessions':len(dates),
                   'tickers':len({r['ticker'] for r in rows}),
                   'first_date':dates[0].isoformat(),'last_date':dates[-1].isoformat(),
                   'duplicate_date_ticker_rows':0,'missing_cohort_rows':0,
                   'field_available_after_next_session_0830_rows':late,
                   'last_session_next_signal_unverified_rows':len(cohort)}
assert all_dates['warmup.json'][-1] < all_dates['development.json'][0]
next_signal=datetime.combine(all_dates['development.json'][0],time(8,30),tzinfo=local_zone)
for row in json.loads((inputs/'warmup.json').read_text()):
    if row['date']==all_dates['warmup.json'][-1].isoformat():
        assert datetime.fromisoformat(row['available_at'])<=next_signal
summary['warmup.json']['last_session_next_signal_unverified_rows']=0
assert summary['development.json']['first_date']==spec['development']['start']
assert summary['development.json']['last_date']==spec['development']['end']
print(json.dumps({'schema_version':1,'scope':'read-only exact prepared ETF JSON input contract',
 'verification_command':'python3 '+str(Path(__file__).resolve().relative_to(root)),
 'verification_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
 'program_manifest_digest':'53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b',
 'lake_id':spec['data']['lake_id'],'qdata_commit':prepared['qdata_commit'],
 'profile_digest':spec['execution_profile_digest'],'receipt_sha256':prepared['data_receipt_sha256'],
 'cohort_selection_date':prepared['selection_date'],'cohort_size':len(cohort),
 'files':summary,'checked_fields':sorted(required),'sealed_input_read':False,
 'performance_read':False,'source_publication_time_independently_verified':False,
 'pit_universe_independently_replayed':False,'execution_prices_independently_verified':False,
 'data_readiness_decision':'not_made'},sort_keys=True,indent=2))
