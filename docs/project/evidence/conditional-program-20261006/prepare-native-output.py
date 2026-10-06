"""Freeze the tested candidate and unchanged numeric qualification package without activating it."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from quant_company.company import fingerprint
from quant_company.research.program_contracts import ResearchProgram

evidence = Path(__file__).resolve().parent
root = evidence.parents[3]
source = '5c44ad07273adc780a56a47d7d35114fd0dc32c8'
operating = '231d6ba0755f658f167636a8ea2c3ef65b6af562'


def git(*args):
    return subprocess.check_output(['git', *args], cwd=root)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    with path.open('x') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


assert git('rev-parse', 'HEAD').decode().strip() == source
for name in ('pyproject.toml', 'uv.lock', 'deploy/entrypoint.py', 'deploy/qdata-source.json'):
    assert git('show', source + ':' + name) == git('show', operating + ':' + name)
changed = git('diff', '--name-only', operating, source, '--', 'src').decode().splitlines()
assert set(changed) == {'src/quant_company/company.py', 'src/quant_company/contracts.py',
    'src/quant_company/providers/codex_runner.py', 'src/quant_company/research/controller.py',
    'src/quant_company/research/program_controller.py'}
previous = evidence.parent / 'conditional-runtime-20261002'
old_manifest = json.loads((previous / 'worker-package.json').read_text())
old_package = root / old_manifest['local_package']
assert all(sha(old_package / name) == digest for name, digest in old_manifest['files'].items())
local = root / '.local/conditional-program-20261006/native-output-5c44ad0-02'
package = local / 'worker-package'
package.mkdir(parents=True, mode=0o700, exist_ok=False)
inherited = ('warmup.json', 'development.json', 'receipt.json', 'worker-profile.json', 'qualification.bundle')
for name in inherited:
    shutil.copyfile(old_package / name, package / name)
shutil.copyfile(evidence / 'candidate-program.json', package / 'candidate-program.json')
spec = ResearchProgram.model_validate_json((package / 'candidate-program.json').read_text())
assert fingerprint(spec.model_dump(mode='json')) == 'c3ba5268d71c87bd2c6226160bc720498137bd291396a054970a1f1e7ebcf2db'
helper = (previous / 'qualify-worker-inputs.py').read_text()
assert helper.count(operating) == 1
helper = helper.replace(operating, source).replace("'owner_program_approved':False",
    "'owner_program_approval_granted_by_qualification':False")
(evidence / 'qualify-native-worker.py').write_text(helper)
shutil.copyfile(evidence / 'qualify-native-worker.py', package / 'qualify-worker-inputs.py')
subprocess.run(['git', 'bundle', 'create', str(package / 'company.bundle'), 'HEAD'], cwd=root, check=True,
               capture_output=True)
assert (package / 'company.bundle').stat().st_size <= 256 * 1024 * 1024
manifest = {'schema_version': 1, 'source_commit': source, 'program_digest': fingerprint(spec.model_dump(mode='json')),
    'qualification_code_commit': old_manifest['qualification_code_commit'], 'attempt': 1,
    'files': {p.name: sha(p) for p in package.iterdir() if p.is_file()},
    'protected_files': old_manifest['protected_files'], 'inherited_numeric_files': list(inherited),
    'original_worker_package_sha256': sha(previous / 'worker-package.json'),
    'qualification_market_mounts': ['warmup.json'], 'performance_computed': False,
    'scientific_trials_added': 0, 'poller_activated': False}
write(package / 'worker-package.json', manifest)
write(evidence / 'native-worker-package.json', manifest)
archive = local / (source + '.tar')
subprocess.run(['git', 'archive', '--format=tar', '-o', str(archive), source,
                'src', 'deploy', 'pyproject.toml', 'uv.lock', 'README.md'], cwd=root, check=True)
names = git('ls-tree', '-r', '--name-only', source, 'src/quant_company').decode().splitlines()
files = {name.removeprefix('src/quant_company/'):
         hashlib.sha256(git('show', source + ':' + name)).hexdigest() for name in names}
receipt = {'schema_version': 1, 'source_commit': source, 'previous_operating_commit': operating,
    'company_files': files, 'company_tree_sha256': fingerprint(files),
    'archive_sha256': sha(archive), 'archive_bytes': archive.stat().st_size,
    'local_archive': str(archive.relative_to(root)), 'local_worker_package': str(package.relative_to(root)),
    'program_digest': manifest['program_digest'], 'changed_runtime_files': changed,
    'dependencies_entrypoint_and_qdata_pin_preserved': True, 'numeric_inputs_engine_and_profile_preserved': True,
    'operating_source_activated': False, 'worker_qualified': False, 'scientific_trials_added': 0}
write(evidence / 'native-source-manifest.json', receipt)
print(json.dumps({key: value for key, value in receipt.items() if key != 'company_files'}, ensure_ascii=False))
