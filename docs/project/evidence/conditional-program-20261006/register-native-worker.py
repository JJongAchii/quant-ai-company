"""Register one actually qualified release; preserve the active supervisor and previous mappings."""

import hashlib
import importlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

SOURCE = '5c44ad07273adc780a56a47d7d35114fd0dc32c8'
base = Path(__file__).resolve().parent
qualification = base / 'qualification-01'
proof = json.loads((qualification / 'qualification-receipt.json').read_bytes())
directory = Path(proof['prepared_release'])
assert directory.is_relative_to(qualification) and proof['source_commit'] == SOURCE
assert proof['qualified_for_frozen_input_transport'] and proof['actual_bubblewrap']
assert not proof['performance_computed'] and proof['scientific_trials_added'] == 0
sys.path.insert(0, str(directory / 'code/src'))
contracts = importlib.import_module('quant_company.research.adaptive_contracts')
executor = importlib.import_module('quant_company.research.adaptive_executor')
releases = importlib.import_module('quant_company.research.releases')
worker = importlib.import_module('quant_company.research.worker')
digest_model = contracts.digest_model
AdaptiveProfile = executor.AdaptiveProfile
resolve_release_config = releases.resolve_release_config
verify_company_pin = releases.verify_company_pin
WorkerConfig, atomic_json, exclusive_lock, sha_file = (
    worker.WorkerConfig, worker.atomic_json, worker.exclusive_lock, worker.sha_file)

active = Path('/home/achii/.config/quant-company/research-worker.json')
assert sha_file(active) == '4d0b0c82e567c6a269b17610abaa282a01b1f18b261272600c076e28ee496c57'
old_bytes = active.read_bytes()
old = WorkerConfig.from_file(active)
assert old.company_commit == '231d6ba0755f658f167636a8ea2c3ef65b6af562'
new_path = directory / 'worker-config.json'
assert sha_file(new_path) == proof['prepared_config_sha256']
assert sha_file(directory / 'release.json') == proof['prepared_release_receipt_sha256']
new = WorkerConfig.from_file(new_path)
verify_company_pin(new)
for name in ('api_url', 'worker_id', 'token_file', 'state_dir', 'repo_source', 'input_source',
             'evidence_repo', 'research_python', 'release_registry_file'):
    assert getattr(new, name) == getattr(old, name), 'worker_authority_changed:' + name
assert set(new.adaptive_profiles) == set(old.adaptive_profiles)
for name, path in old.adaptive_profiles.items():
    if name != 'kr-etf-retrospective-v1':
        assert new.adaptive_profiles[name] == path
profile_path = new.adaptive_profiles['kr-etf-retrospective-v1']
assert sha_file(profile_path) == proof['worker_profile_sha256']
profile = AdaptiveProfile.model_validate_json(profile_path.read_bytes())
assert digest_model(profile.public_profile) == proof['profile_digest']
assert profile.public_profile.data_policy_digest == proof['research_scope']['data_policy_digest']
assert not profile.public_profile.fixture_only
bound = contracts.AdaptiveManifest.model_validate_json(
    (qualification / 'warmup-qualification/manifest.json').read_bytes())
assert bound.company_commit == SOURCE and bound.spec.research_scope.model_dump(mode='json') == proof['research_scope']
for name, digest in bound.spec.data.input_files.items():
    assert sha_file(profile.input_sources[name]) == digest
registry = old.release_registry_file
record_path = base / 'worker-registration.json'
assert not record_path.exists(), 'registration_already_attempted'
os.umask(0o077)
with exclusive_lock(registry.with_name(registry.name + '.lock')):
    before_bytes = registry.read_bytes()
    before = json.loads(before_bytes)
    assert set(before) == {'schema_version', 'releases'} and before['schema_version'] == 1
    assert SOURCE not in before['releases'], 'release_already_registered'
    assert resolve_release_config(old, old.company_commit) == old
    (base / 'registry-before-native.json').write_bytes(before_bytes)
    after = {**before, 'releases': {**before['releases'], SOURCE: str(new_path)}}
    atomic_json(registry, after)
    assert resolve_release_config(old, SOURCE) == new
    assert resolve_release_config(old, old.company_commit) == old
    assert active.read_bytes() == old_bytes
    actual = json.loads(registry.read_bytes())
    assert all(actual['releases'][commit] == path for commit, path in before['releases'].items())
    record = {'schema_version': 1, 'observed_at': datetime.now(UTC).isoformat(),
        'phase': 'qualified_registered_for_exact_jobs', 'source_commit': SOURCE,
        'prepared_config_sha256': sha_file(new_path), 'active_config_sha256': sha_file(active),
        'registry_before_sha256': hashlib.sha256(before_bytes).hexdigest(),
        'registry_after_sha256': sha_file(registry), 'previous_releases_preserved': True,
        'exact_release_resolvable_by_active_supervisor': True, 'active_company_commit': old.company_commit,
        'active_supervisor_restarted': False, 'token_and_transport_authority_preserved': True,
        'scientific_trials_added': 0, 'staff_decision_applied': False}
    atomic_json(record_path, record)
print(json.dumps(record))
