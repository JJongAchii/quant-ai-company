"""Build and inspect immutable code overlays on exact running bases; never replace a service."""

import hashlib
import json
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

base = Path(__file__).resolve().parent / 'native-output-5c44ad0-02'
manifest = json.loads((base / 'native-source-manifest.json').read_text())
source = manifest['source_commit']
assert source == '5c44ad07273adc780a56a47d7d35114fd0dc32c8'
archive = base / (source + '.tar')
assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest['archive_sha256']
destination = Path('/opt/quant-company/releases') / source
destination.mkdir(mode=0o755, exist_ok=False)
with tarfile.open(archive) as bundle:
    for member in bundle.getmembers():
        path = PurePosixPath(member.name)
        assert not path.is_absolute() and '..' not in path.parts
        assert path.parts[0] in {'src', 'deploy', 'pyproject.toml', 'uv.lock', 'README.md'}
        target = destination.joinpath(*path.parts)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            assert member.isfile() and not member.issym() and not member.islnk()
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(member) as stream, target.open('xb') as output:
                output.write(stream.read())
            target.chmod(member.mode & 0o777)
for name, digest in manifest['company_files'].items():
    assert hashlib.sha256((destination / 'src/quant_company' / name).read_bytes()).hexdigest() == digest
bases = {
    'app': ('quant-company-api-1', 'sha256:7f305b5f8b235d065aa2b4d45106436f31355ec4b66fd78445453148472767ff'),
    'worker': ('quant-company-worker-1', 'sha256:c55cde4b44b83df0b0e9c2b59682404fbb2544bea87dcd211f478296c44eb7d0'),
    'gateway': ('quant-company-account-gateway-1', 'sha256:942a896a92f6a2c6e3d5b4743ad670808da62be448136d77f2e75455f7668b24'),
    'codex': ('quant-company-codex-runtime-1', 'sha256:c1d123ee1c525a7d02375fb1172e1d955fec8c555e0285255c5da4b80b834ea4'),
}
probe = r'''
import hashlib,json,os,pathlib,sys
import quant_company
from quant_company.contracts import ProviderRequest,ResearchStageOutput
from quant_company.providers.codex_runner import output_schema
expected=json.loads(sys.argv[1])
root=pathlib.Path(quant_company.__file__).resolve().parent
actual={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
    for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
assert actual==expected['company_files']
assert os.environ['COMPANY_CODE_COMMIT']==expected['source_commit']
request=ProviderRequest(request_id='11111111-1111-4111-8111-111111111111',model='gpt-5.6-terra',
    prompt='Non-performance import qualification',output_contract='research_stage_v1')
schema=output_schema(request)
assert set(schema['required'])=={'action','read_path','read_offset','artifact_json'}
ResearchStageOutput(action='read',read_path='fixture.txt',read_offset=0)
print(json.dumps({'actual_import_root':str(root),'company_files':len(actual),
    'native_schema_verified':True,'effective_uid':os.getuid(),'model_called':False,
    'slack_or_database_credentials_loaded':False}))
'''
images = {}
for role, (name, expected_id) in bases.items():
    container = json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]
    assert container['Image'] == expected_id and container['State']['Running']
    base_tag = container['Config']['Image']
    assert json.loads(subprocess.check_output(['docker', 'image', 'inspect', base_tag]))[0]['Id'] == expected_id
    tag = f'quant-company-native-{role}:{source}'
    log = base / f'build-{role}.log'
    with log.open('xb') as stream:
        result = subprocess.run(['docker', 'build', '--network', 'none', '-f',
            str(destination / 'deploy/Dockerfile.quant-code-update'), '--build-arg', f'BASE_IMAGE={base_tag}',
            '--build-arg', f'RELEASE_COMMIT={source}', '-t', tag, str(destination)],
            stdout=stream, stderr=subprocess.STDOUT, timeout=300)
    assert result.returncode == 0, f'{role}_image_build_failed_private_log_preserved'
    image_id = json.loads(subprocess.check_output(['docker', 'image', 'inspect', tag]))[0]['Id']
    result = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--memory', '256m', '--cpus', '1',
        '-e', 'PYTHONDONTWRITEBYTECODE=1', '--entrypoint', 'python', tag, '-B', '-c', probe,
        json.dumps(manifest)], capture_output=True, timeout=90)
    assert result.returncode == 0, f'{role}_isolated_import_qualification_failed'
    receipt = json.loads(result.stdout)
    images[role] = {'base_container_id': container['Id'], 'base_image_id': expected_id,
                    'tag': tag, 'image_id': image_id, 'import_qualification': receipt}
receipt = {'schema_version': 1, 'observed_at': datetime.now(UTC).isoformat(), 'source_commit': source,
    'archive_sha256': manifest['archive_sha256'], 'images': images, 'services_replaced': False,
    'model_calls': 0, 'credential_files_read': False, 'scientific_trials_added': 0}
(base / 'image-preparation.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt))
