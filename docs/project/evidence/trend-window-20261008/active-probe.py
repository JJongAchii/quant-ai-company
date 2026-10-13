import datetime
import importlib.util
import json
import subprocess
from pathlib import Path

sp = importlib.util.spec_from_file_location('ops','/opt/quant-company/operator-releases/trend-feed-20261006/production-cutover.py')
o = importlib.util.module_from_spec(sp)
sp.loader.exec_module(o)
root = Path('/opt/quant-company/operator-releases/trend-window-20261008')
manifest = json.loads((root/'manifest.json').read_text())
images = json.loads((root/'images.json').read_text())
before = json.loads((root/'containers.before.json').read_text())
after = o.inspect()
result = {'checked_at':datetime.datetime.now(datetime.UTC).isoformat(),'roles':{}}
for role,target in manifest['targets'].items():
    row = after[role]
    probe = """import hashlib,json,quant_company
from pathlib import Path
from quant_company.company import Company
from quant_company.config import Settings
from quant_company.trend_feed.editor import EDITORIAL_POLICY_VERSION
from quant_company.trend_feed.store import TrendFeedStore
p=Path(quant_company.__file__).parent;c=Company(Settings());s=TrendFeedStore(c)
print(json.dumps({'source':{name:hashlib.sha256((p/name).read_bytes()).hexdigest() for name in ['trend_feed/ranking.py','trend_feed/store.py','trend_feed/editor.py']},'policy_version':EDITORIAL_POLICY_VERSION,'policy_digest':s.policy(),'authorized':s.authorized(),'publication_hours':c.settings.trend_feed_publication_hours,'on_demand':c.settings.trend_feed_on_demand_enabled,'publish':c.settings.trend_feed_publish_enabled}))
"""
    actual = json.loads(o.run(['docker','exec',row['Id'],'python','/app/entrypoint.py','/opt/company/.venv/bin/python','-c',probe]))
    assert actual['source']=={name:manifest['parents'][target['image']]['files'][name] for name in manifest['changed']}
    assert row['Image']==images['images'][target['image']]['image_id'] and o.signature(row)==o.signature(before[role])
    assert row['State']['Running'] and actual['policy_version']==4 and actual['authorized'] and actual['publish']
    result['roles'][role] = {**actual,'image':row['Image'],'running':True,'restart_count':row['RestartCount'],
                            'runtime_configuration_preserved':True,'source_hashes_verified':True}
assert len({r['policy_digest'] for r in result['roles'].values()})==1
logs = subprocess.run(['docker','logs','--tail','1000',after['slack-socket']['Id']],capture_output=True,text=True)
logtext = logs.stdout+logs.stderr
result['socket_connected_info_log_present'] = 'Slack Socket Mode connected for' in logtext
result['socket_startup_error'] = 'Slack Socket Mode could not stay connected' in logtext
result['other_containers_preserved'] = all(after[n]['Id']==r['Id'] for n,r in before.items() if n not in manifest['targets'])
result['pause'] = o.sql("SELECT row_to_json(s) FROM (SELECT paused_until,reason FROM runtime_control WHERE id=1)s")
result['socket_authentication'] = 'separately verified by short-lived SDK probe; INFO logs may be disabled'
assert not result['socket_startup_error'] and result['other_containers_preserved']
print(json.dumps(result))
