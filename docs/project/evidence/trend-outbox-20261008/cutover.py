import datetime
import fcntl
import importlib.util
import json
from pathlib import Path

sp = importlib.util.spec_from_file_location('ops', '/opt/quant-company/operator-releases/trend-feed-20261006/production-cutover.py')
o = importlib.util.module_from_spec(sp)
sp.loader.exec_module(o)
root = Path('/opt/quant-company/operator-releases/trend-outbox-20261008')
journal = root / 'activation.json'

def delivered():
    return o.sql("SELECT coalesce(json_agg(s),'[]') FROM (SELECT id::text,md5(text) AS hash,sent_ts FROM outbox WHERE status='delivered' ORDER BY id)s")

def save(record):
    o.atomic(journal, (json.dumps(record, indent=2) + '\n').encode())

with (o.STATE / 'releases/backup.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert not journal.exists(), 'read previous receipt; do not replay uncertain cutover'
    image = json.loads((root / 'image.json').read_text())
    assert image['all_source_hashes_match'] and image['parent_runtime_config_preserved']
    before = o.inspect()
    old = before['dispatch']
    assert old['Id'] == image['parent_container'] and old['Image'] == image['parent_image'], 'parent drift'
    assert o.sql("SELECT count(*) FROM outbox WHERE status='sending'") == 0, 'wait for in-flight sends'
    environment = dict(v.split('=', 1) for v in old['Config']['Env'])
    h = old['HostConfig']
    service = {'image': image['tag'], 'environment': environment, 'command': old['Config']['Cmd'],
               'entrypoint': old['Config']['Entrypoint'], 'user': old['Config']['User'], 'working_dir': old['Config']['WorkingDir'],
               'read_only': h['ReadonlyRootfs'], 'cap_drop': h.get('CapDrop') or [], 'security_opt': h.get('SecurityOpt') or [],
               'mem_limit': h['Memory'], 'memswap_limit': h['MemorySwap'], 'pids_limit': h['PidsLimit'],
               'init': h.get('Init') or False, 'privileged': h['Privileged'], 'cpus': h['NanoCpus'] / 1e9,
               'restart': h['RestartPolicy']['Name'],
               'volumes': [{'type': m['Type'], 'source': m['Source'], 'target': m['Destination'], 'read_only': not m['RW']}
                           for m in old['Mounts'] if not m['Destination'].startswith('/run/secrets/')]}
    path = o.STATE / 'config/trend-outbox-20261008-dispatch.compose.json'
    o.atomic(path, json.dumps({'services': {'dispatch': service}}).encode())
    command = o.compose(old, path)
    config = json.loads(o.run([*command, 'config', '--format', 'json']))
    compiled = config['services']['dispatch']
    assert compiled['environment'] == environment and compiled['image'] == image['tag']
    mounts = {v['target']: (v['source'], v.get('read_only', False)) for v in compiled.get('volumes', [])}
    for secret in compiled.get('secrets', []):
        target = secret['target']
        target = target if target.startswith('/') else '/run/secrets/' + target
        mounts[target] = (config['secrets'][secret['source']]['file'], True)
    assert mounts == {m['Destination']: (m['Source'], not m['RW']) for m in old['Mounts']}, 'mount drift'
    assert {config['networks'][n]['name'] for n in compiled['networks']} == set(old['NetworkSettings']['Networks'])
    original_delivered = delivered()
    pause_before = o.sql("SELECT row_to_json(s) FROM (SELECT paused_until,reason FROM runtime_control WHERE id=1)s")
    record = {'state': 'switching', 'started_at': datetime.datetime.now(datetime.UTC).isoformat(),
              'parent_image': old['Image'], 'candidate_image': image['image_id'], 'scope': ['dispatch'],
              'preflight_environment_mounts_networks_preserved': True}
    save(record)
    try:
        o.run([*command, 'up', '--detach', '--no-deps', '--force-recreate', 'dispatch'], timeout=400)
        after = o.inspect()
        current = after['dispatch']
        assert current['Image'] == image['image_id'] and o.signature(current) == o.signature(old)
        assert current['State']['Running']
        assert all(after[n]['Id'] == v['Id'] for n, v in before.items() if n != 'dispatch')
        seen = {v['id']: v for v in delivered()}
        assert all(seen[v['id']] == v for v in original_delivered), 'existing receipt/body changed'
        assert pause_before == o.sql("SELECT row_to_json(s) FROM (SELECT paused_until,reason FROM runtime_control WHERE id=1)s")
        record.update(state='activated', completed_at=datetime.datetime.now(datetime.UTC).isoformat(),
                      actual_image=current['Image'], actual_container=current['Id'],
                      runtime_configuration_preserved=True, other_containers_preserved=True,
                      existing_delivered_receipts_preserved=True, owner_pause_preserved=True)
        save(record)
    except Exception:
        record['state'] = 'requires_readback'
        save(record)
        raise
print(json.dumps(record))
