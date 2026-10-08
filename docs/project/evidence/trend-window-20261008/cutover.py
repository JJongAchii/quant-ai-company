import datetime
import fcntl
import importlib.util
import json
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location('ops', '/opt/quant-company/operator-releases/trend-feed-20261006/production-cutover.py')
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)
root = Path('/opt/quant-company/operator-releases/trend-window-20261008')
journal = root/'activation.json'
targets = ('news-worker', 'api', 'slack-socket', 'dispatch')
pause_reason = 'trend_window_cutover_20261008'


def delivered():
    return o.sql("SELECT coalesce(json_agg(s),'[]') FROM (SELECT id::text,md5(text) AS hash,sent_ts FROM outbox WHERE status='delivered' ORDER BY id)s")


def save(record):
    o.atomic(journal, (json.dumps(record, indent=2)+'\n').encode())


def busy():
    return {table: o.sql("SELECT count(*) FROM "+table+" WHERE state='running'")
            for table in ('news_reviews', 'news_triages', 'news_searches', 'brief_calls', 'trend_feed_calls')}


with (o.STATE/'releases/backup.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert not journal.exists(), 'read previous activation receipt; do not replay'
    images = json.loads((root/'images.json').read_text())
    before = o.inspect()
    commands = {}
    for name in targets:
        old, qualified = before[name], images['targets'][name]
        assert old['Id']==qualified['id'] and old['Image']==qualified['image'], 'parent drift: '+name
        image = images['images'][old['Image']]
        assert image['all_source_hashes_match'] and image['parent_runtime_config_preserved']
        env = dict(v.split('=',1) for v in old['Config']['Env'])
        host = old['HostConfig']
        service = {'image':image['tag'], 'environment':env, 'command':old['Config']['Cmd'],
            'entrypoint':old['Config']['Entrypoint'], 'user':old['Config']['User'], 'working_dir':old['Config']['WorkingDir'],
            'read_only':host['ReadonlyRootfs'], 'cap_drop':host.get('CapDrop') or [], 'security_opt':host.get('SecurityOpt') or [],
            'mem_limit':host['Memory'], 'memswap_limit':host['MemorySwap'], 'pids_limit':host['PidsLimit'],
            'init':host.get('Init') or False, 'privileged':host['Privileged'], 'cpus':host['NanoCpus']/1e9,
            'restart':host['RestartPolicy']['Name'],
            'volumes':[{'type':m['Type'],'source':m['Source'],'target':m['Destination'],'read_only':not m['RW']}
                       for m in old['Mounts'] if not m['Destination'].startswith('/run/secrets/')]}
        path = o.STATE/'config'/f'trend-window-20261008-{name}.compose.json'
        o.atomic(path,json.dumps({'services':{name:service}}).encode())
        command = o.compose(old,path)
        config = json.loads(o.run([*command,'config','--format','json']))
        compiled = config['services'][name]
        assert compiled['environment']==env and compiled['image']==image['tag']
        mounts = {v['target']:(v['source'],v.get('read_only',False)) for v in compiled.get('volumes',[])}
        for secret in compiled.get('secrets',[]):
            target = secret['target']
            target = target if target.startswith('/') else '/run/secrets/'+target
            mounts[target] = (config['secrets'][secret['source']]['file'],True)
        assert mounts=={m['Destination']:(m['Source'],not m['RW']) for m in old['Mounts']}, 'mount drift: '+name
        assert {config['networks'][n]['name'] for n in compiled['networks']}==set(old['NetworkSettings']['Networks'])
        commands[name] = command
    assert not o.sql("SELECT count(*) FROM runtime_control WHERE paused_until>now()"), 'existing owner pause'
    original_delivered, original_requests = delivered(), o.frozen_requests()
    pause_before = o.sql("SELECT row_to_json(s) FROM (SELECT paused_until,reason FROM runtime_control WHERE id=1)s")
    record = {'state':'quiescing','started_at':datetime.datetime.now(datetime.UTC).isoformat(),
              'source_commit':'a55478f','scope':list(targets),'replaced':[],
              'preflight_environment_mounts_networks_preserved':True,'pause_before':pause_before}
    save(record)
    try:
        granted = o.sql("WITH s AS (UPDATE runtime_control SET paused_until=now()+interval '10 minutes',reason='"+pause_reason+"' WHERE id=1 AND (paused_until IS NULL OR paused_until<=now()) RETURNING id) SELECT coalesce(json_agg(id),'[]') FROM s")
        assert granted, 'pause acquired by another operator'
        # Let already-started model calls finish. No model, charge or send is replayed.
        deadline = time.monotonic()+420
        time.sleep(2)
        while any((counts:=busy()).values()) or o.sql("SELECT count(*) FROM outbox WHERE status='sending'"):
            if time.monotonic()>=deadline:
                raise RuntimeError('active_work_did_not_quiesce')
            print(json.dumps({'state':'waiting_for_in_flight_completion','counts':counts}),flush=True)
            time.sleep(10)
        record.update(state='switching',in_flight_model_and_slack_writes=0)
        save(record)
        for name in targets:
            assert o.inspect()[name]['Id']==before[name]['Id'], 'parent changed while waiting'
            assert o.sql("SELECT count(*) FROM runtime_control WHERE reason='"+pause_reason+"' AND paused_until>now()"), 'operator pause changed'
            o.run([*commands[name],'up','--detach','--no-deps','--force-recreate',name],timeout=400)
            current = o.inspect()[name]
            assert current['Image']==images['images'][before[name]['Image']]['image_id']
            assert o.signature(current)==o.signature(before[name]) and current['State']['Running']
            record['replaced'].append(name)
            save(record)
        after = o.inspect()
        assert all(after[name]['Id']==old['Id'] for name,old in before.items() if name not in targets)
        receipts = {r['id']:r for r in delivered()}
        assert all(receipts[r['id']]==r for r in original_delivered), 'existing delivered receipt changed'
        requests = o.frozen_requests()
        assert all(requests[table].get(key)==value for table,rows in original_requests.items() for key,value in rows.items())
        record.update(state='activated',completed_at=datetime.datetime.now(datetime.UTC).isoformat(),
            runtime_configuration_preserved=True,other_containers_preserved=True,
            existing_delivered_receipts_preserved=True,existing_frozen_requests_preserved=True,
            containers={name:{'id':after[name]['Id'],'image':after[name]['Image']} for name in targets})
    except Exception:
        record['state'] = 'requires_readback'
        raise
    finally:
        # Restore only our pause, without overriding a later owner intervention.
        until = 'NULL' if not pause_before['paused_until'] else "'"+pause_before['paused_until']+"'::timestamptz"
        reason = 'NULL' if pause_before['reason'] is None else "'"+pause_before['reason'].replace("'","''")+"'"
        o.sql("WITH s AS (UPDATE runtime_control SET paused_until="+until+",reason="+reason+" WHERE id=1 AND reason='"+pause_reason+"' RETURNING id) SELECT coalesce(json_agg(id),'[]') FROM s")
        record['operator_pause_removed'] = not o.sql("SELECT count(*) FROM runtime_control WHERE reason='"+pause_reason+"'")
        save(record)
print(json.dumps(record),flush=True)
