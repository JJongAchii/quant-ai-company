import datetime
import hashlib
import importlib.util
import json
from pathlib import Path

sp = importlib.util.spec_from_file_location('ops', '/opt/quant-company/operator-releases/trend-feed-20261006/production-cutover.py')
o = importlib.util.module_from_spec(sp)
sp.loader.exec_module(o)
root = Path('/opt/quant-company/operator-releases/trend-outbox-20261008')
manifest = json.loads((root / 'manifest.json').read_text())
rows = o.inspect()
old = rows['dispatch']
assert old['Image'] == manifest['parent_image'], 'actual parent changed after qualification'
o.atomic(root / 'containers.before.json', json.dumps(rows).encode())
parent_tag = 'quant-company:trend-outbox-parent-20261008'
tag = 'quant-company:trend-outbox-20261008'
o.run(['docker', 'tag', old['Image'], parent_tag])
(root / 'Dockerfile').write_text('FROM ' + parent_tag + '\nCOPY --chown=10001:10001 slack.py /opt/company/src/quant_company/slack.py\n')
assert hashlib.sha256((root / 'slack.py').read_bytes()).hexdigest() == manifest['files']['slack.py']
o.run(['docker', 'build', '--pull=false', '--network=none', '--tag', tag, str(root)], timeout=300)
built = json.loads(o.run(['docker', 'image', 'inspect', tag]))[0]
parent = json.loads(o.run(['docker', 'image', 'inspect', old['Image']]))[0]
assert all(built['Config'].get(k) == parent['Config'].get(k) for k in ['Env', 'Cmd', 'Entrypoint', 'User', 'WorkingDir'])
probe = "import json,hashlib;from pathlib import Path;p=Path('/opt/company/src/quant_company');print(json.dumps({str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest() for f in p.rglob('*') if f.is_file() and f.suffix in {'.py','.sql','.json','.md'}}))"
files = json.loads(o.run(['docker', 'run', '--rm', '--entrypoint', '/opt/company/.venv/bin/python', tag, '-c', probe]))
assert files == manifest['files'], 'candidate is not qualified native source'
record = {'checked_at': datetime.datetime.now(datetime.UTC).isoformat(), 'tag': tag, 'image_id': built['Id'],
          'parent_image': old['Image'], 'parent_container': old['Id'], 'changed': ['slack.py'],
          'unchanged_count': manifest['unchanged_count'], 'all_source_hashes_match': True,
          'parent_runtime_config_preserved': True, 'briefing_preserved': True, 'deployed': False}
o.atomic(root / 'image.json', (json.dumps(record, indent=2) + '\n').encode())
print(json.dumps(record))
