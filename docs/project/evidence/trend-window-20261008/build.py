import datetime
import hashlib
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('ops', '/opt/quant-company/operator-releases/trend-feed-20261006/production-cutover.py')
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)
root = Path('/opt/quant-company/operator-releases/trend-window-20261008')
manifest = json.loads((root/'manifest.json').read_text())
rows = o.inspect()
for role,target in manifest['targets'].items():
    assert rows[role]['Id']==target['id'] and rows[role]['Image']==target['image'], 'actual parent drift: '+role
o.atomic(root/'containers.before.json', json.dumps(rows).encode())
images = {}
for n,(parent,qualified) in enumerate(manifest['parents'].items()):
    parent_tag = f'quant-company:trend-window-parent-20261008-{n}'
    tag = f'quant-company:trend-window-20261008-{n}'
    o.run(['docker','tag',parent,parent_tag])
    dockerfile = root/f'Dockerfile.{n}'
    dockerfile.write_text('FROM '+parent_tag+'\n'+''.join(
        f'COPY --chown=10001:10001 {name} {qualified["package"]}/{name}\n' for name in manifest['changed']))
    for name in manifest['changed']:
        assert hashlib.sha256((root/name).read_bytes()).hexdigest()==qualified['files'][name]
    o.run(['docker','build','--pull=false','--network=none','--tag',tag,'--file',str(dockerfile),str(root)],timeout=300)
    built = json.loads(o.run(['docker','image','inspect',tag]))[0]
    old = json.loads(o.run(['docker','image','inspect',parent]))[0]
    assert all(built['Config'].get(k)==old['Config'].get(k) for k in ['Env','Cmd','Entrypoint','User','WorkingDir'])
    probe = "import json,hashlib;from pathlib import Path;p=Path("+repr(qualified['package'])+");print(json.dumps({str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest() for f in p.rglob('*') if f.is_file() and f.suffix in {'.py','.sql','.json','.md'}}))"
    files = json.loads(o.run(['docker','run','--rm','--entrypoint','/opt/company/.venv/bin/python',tag,'-c',probe]))
    assert files==qualified['files'], 'candidate source mismatch'
    images[parent] = {'tag':tag,'image_id':built['Id'],'unchanged_count':qualified['unchanged_count'],
                      'all_source_hashes_match':True,'parent_runtime_config_preserved':True}
record = {'checked_at':datetime.datetime.now(datetime.UTC).isoformat(),'images':images,
          'targets':manifest['targets'],'changed':manifest['changed'],'deployed':False}
o.atomic(root/'images.json',(json.dumps(record,indent=2)+'\n').encode())
print(json.dumps(record))
