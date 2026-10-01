import fcntl, hashlib, json, os, pathlib, shutil, subprocess, tarfile, time
from datetime import UTC, datetime

P = pathlib.Path
COMMIT = '3c848af95dc33b7da444a55018fd41d374c10025'
OLD_APP = '5defb8cb9dd7c655214663b05670cd2896006b30'
STATE = P('/var/lib/quant-company')
ROOT = P('/opt/quant-company/releases') / COMMIT
ARCHIVE = P('/tmp/company.tar')
RECORD = STATE / 'releases' / ('exploration-stage-' + COMMIT + '.json')

def run(args, **kw):
    return subprocess.check_output(args, timeout=1800, **kw)

def sha(p):
    with p.open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()

def inventory(p):
    return {str(f.relative_to(p)): sha(f) for f in p.rglob('*') if f.is_file() and '__pycache__' not in f.parts}

with (STATE / '.backup.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    old = P('/opt/quant-company/current').resolve()
    assert old.name == OLD_APP, 'operating_app_changed'
    assert sha(ARCHIVE) == 'e3c458d2e9e704739b27e60795b1786ef13a2525eb889e04c0b33212e767684d'
    assert not ROOT.exists() and not RECORD.exists(), 'candidate_already_exists'
    assert shutil.disk_usage(ROOT.parent).free > 4 * 1024**3, 'insufficient_disk'
    ROOT.mkdir(mode=0o755)
    with tarfile.open(ARCHIVE) as tf:
        for m in tf.getmembers():
            path = pathlib.PurePosixPath(m.name)
            assert not path.is_absolute() and '..' not in path.parts
            assert m.isfile() or m.isdir()
            target = ROOT.joinpath(*path.parts)
            if m.isdir(): target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tf.extractfile(m).read())
                target.chmod(0o755 if m.mode & 0o111 else 0o644)
    assert json.loads((ROOT / 'deploy/qdata-source.json').read_text()) == json.loads((old / 'deploy/qdata-source.json').read_text())
    qdata = json.loads((ROOT / 'deploy/qdata-source.json').read_text())['commit']
    assert qdata == 'd6d7d0ed066ec49541e9acdd657c9ec5692ffc52'
    shutil.copytree(old / 'qdata', ROOT / 'qdata')
    expected = inventory(ROOT / 'src/quant_company')
    record = {'phase':'staging', 'commit':COMMIT, 'archive_sha256':sha(ARCHIVE),
              'previous':str(old), 'qdata_commit':qdata, 'qdata_tree_sha256':hashlib.sha256(json.dumps(inventory(ROOT/'qdata'), sort_keys=True, separators=(',',':')).encode()).hexdigest(),
              'started_at':datetime.now(UTC).isoformat(), 'images':[], 'active_changed':False}
    def save(): RECORD.write_text(json.dumps(record, indent=2)+'\n')
    save()
    for target, name in [('app','quant-company'),('autonomous-research','quant-company-autonomous')]:
        print(json.dumps({'phase':'building_exact_target', 'target':target}), flush=True)
        image = name + ':' + COMMIT
        log = STATE / 'releases' / ('exploration-build-' + COMMIT + '-' + target + '.log')
        args = ['docker','buildx','build','--builder','default','--load','--progress','plain',
                '--build-context','qdata='+str(ROOT/'qdata'),'--build-arg','RELEASE_COMMIT='+COMMIT,
                '--build-arg','QDATA_COMMIT='+qdata,'--target',target,'-f',str(ROOT/'deploy/Dockerfile'),'-t',image,str(ROOT)]
        with log.open('wb') as f:
            subprocess.run(args, check=True, stdout=f, stderr=subprocess.STDOUT, timeout=1800)
        probe = "import hashlib,importlib.util,json,pathlib,os;root=pathlib.Path(importlib.util.find_spec('quant_company').origin).parent;from quant_company.research.program_contracts import ResearchProgram;from quant_company.research.program_controller import ProgramController; print(json.dumps({'files':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts},'company_commit':os.environ['COMPANY_CODE_COMMIT'],'qdata_commit':os.environ['QDATA_CODE_COMMIT']}))"
        proof = json.loads(run(['docker','run','--rm','--network=none','--read-only','--memory=192m','--cpus=0.5','--cap-drop=ALL','--security-opt=no-new-privileges','--entrypoint','python',image,'-c',probe]))
        assert proof['files'] == expected and proof['company_commit'] == COMMIT and proof['qdata_commit'] == qdata, 'installed_source_or_pin_mismatch'
        if target == 'autonomous-research':
            run(['docker','run','--rm','--network=none','--entrypoint','python',image,'-c',"import yaml,subprocess;assert yaml.__version__=='6.0.3';subprocess.run(['git','--version'],check=True,capture_output=True)"])
        row = json.loads(run(['docker','image','inspect',image]))[0]
        record['images'].append({'target':target,'image':image,'id':row['Id'],'source_files_sha256':hashlib.sha256(json.dumps(expected,sort_keys=True).encode()).hexdigest(),'source_matches_archive':True,'uid':row['Config']['User'],'build_log_sha256':sha(log)})
        save()
    record.update(phase='exact_images_qualified_not_active', completed_at=datetime.now(UTC).isoformat())
    save()
    print(json.dumps(record), flush=True)
