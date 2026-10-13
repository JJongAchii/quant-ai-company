"""Activate the actually qualified release and preserve all previous execution mappings."""

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def run(arguments):
    return subprocess.check_output(arguments, text=True, timeout=60).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualification', type=Path, required=True)
    parser.add_argument('--attempt', type=int, required=True, choices=(3,))
    args = parser.parse_args()
    base = args.qualification.resolve()
    proof = json.loads((base / 'qualification-receipt.json').read_bytes())
    directory = Path(proof['prepared_release'])
    assert directory.is_relative_to(base)
    sys.path.insert(0, str(directory / 'code/src'))
    from quant_company.research.adaptive_contracts import AdaptiveManifest, digest_model
    from quant_company.research.adaptive_executor import AdaptiveProfile
    from quant_company.research.releases import (
        PreparedRelease,
        activate_release,
        resolve_release_config,
        rollback_release,
        verify_company_pin,
    )
    from quant_company.research.worker import WorkerConfig, atomic_json, identity_alive, sha_file

    active = Path('/home/achii/.config/quant-company/research-worker.json')
    assert sha_file(active) == '67659f99fa6ce5400410c54c597fd9e2c906d54d7ec222ad064264004a9289c9'
    assert proof['source_commit'] == '231d6ba0755f658f167636a8ea2c3ef65b6af562'
    assert proof['qualified_for_frozen_input_transport'] and proof['actual_bubblewrap']
    assert not proof['performance_computed'] and proof['scientific_trials_added'] == 0
    assert sha_file(directory / 'worker-config.json') == proof['prepared_config_sha256']
    assert sha_file(directory / 'release.json') == proof['prepared_release_receipt_sha256']
    old = WorkerConfig.from_file(active)
    assert resolve_release_config(old, old.company_commit) == old
    new = WorkerConfig.from_file(directory / 'worker-config.json')
    profile_path = new.adaptive_profiles['kr-etf-retrospective-v1']
    assert sha_file(profile_path) == proof['worker_profile_sha256']
    profile = AdaptiveProfile.model_validate_json(profile_path.read_bytes())
    assert digest_model(profile.public_profile) == proof['profile_digest']
    assert not profile.public_profile.fixture_only
    for name, path in old.adaptive_profiles.items():
        assert new.adaptive_profiles[name] == path
    bound = AdaptiveManifest.model_validate_json((base / 'warmup-qualification/manifest.json').read_bytes())
    assert bound.company_commit == proof['source_commit']
    assert bound.spec.research_scope.model_dump(mode='json') == proof['research_scope']
    assert profile.public_profile.data_policy_digest == bound.spec.research_scope.data_policy_digest
    for name, digest in bound.spec.data.input_files.items():
        assert sha_file(profile.input_sources[name]) == digest
    assert run(['systemctl', '--user', 'show', 'research-worker.service', '-p', 'KillMode', '--value']) == 'process'
    previous_start = int(run(['systemctl', '--user', 'show', 'research-worker.service',
        '-p', 'ExecMainStartTimestampMonotonic', '--value']))
    command = run(['systemctl', '--user', 'show', 'research-worker.service', '-p', 'ExecStart', '--value'])
    assert 'quant_company.research.worker --config /home/achii/.config/quant-company/research-worker.json' in command
    assert '/home/achii/quant-company-research/company/.venv/bin/python' in command
    subprocess.run(['/home/achii/quant-company-research/company/.venv/bin/python', '-I', '-B', '-c',
        'import sys;sys.path.insert(0,sys.argv[1]);from quant_company.research.worker import WorkerConfig;'
        'from quant_company.research.executor import execute_child;assert callable(execute_child)',
        str(directory / 'code/src')], check=True, timeout=30)
    for job in (old.state_dir / 'jobs').iterdir():
        if not job.is_dir():
            continue
        if (job / 'process.json').is_file():
            process = json.loads((job / 'process.json').read_bytes())
            assert not identity_alive(process['identity']), 'active_detached_execution'
        if (job / 'state.json').is_file():
            state = json.loads((job / 'state.json').read_bytes())
            assert state.get('uploaded') or state.get('server_terminal'), 'unreconciled_job_receipt'
    drop = Path('/home/achii/.config/systemd/user/research-worker.service.d/99-active-release.conf')
    registry = old.release_registry_file
    old_registry = json.loads(registry.read_bytes())
    rollback = base / ('activation-' + str(args.attempt).zfill(2))
    rollback.mkdir(mode=0o700)
    (rollback / 'worker-config.before.json').write_bytes(active.read_bytes())
    (rollback / 'dropin.before.conf').write_bytes(drop.read_bytes())
    (rollback / 'registry.before.json').write_bytes(registry.read_bytes())
    (rollback / 'dropin.before.conf').chmod(0o600)
    receipt = json.loads((directory / 'release.json').read_bytes())
    prepared = PreparedRelease(proof['source_commit'], directory, directory / 'worker-config.json',
        proof['prepared_config_sha256'], receipt['code_files'])
    record = {'schema_version': 1, 'phase': 'activating', 'started_at': datetime.now(UTC).isoformat(),
        'source_commit': prepared.commit, 'prior_commit': old.company_commit,
        'prior_config_sha256': sha_file(active), 'prior_dropin_sha256': sha_file(drop),
        'prior_registry_sha256': sha_file(registry), 'scientific_trials_added': 0}
    atomic_json(rollback / 'activation.json', record)
    activated = False
    try:
        subprocess.run(['systemctl', '--user', 'stop', 'research-worker.service'], check=True, timeout=60)
        current = activate_release(prepared, active_config=active)
        activated = True
        drop.write_text('[Service]\nWorkingDirectory=' + str(current.company_repo)
            + '\nEnvironment=PYTHONPATH=' + str(current.company_repo / 'src') + '\n')
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True, timeout=60)
        subprocess.run(['systemctl', '--user', 'start', 'research-worker.service'], check=True, timeout=60)
        assert run(['systemctl', '--user', 'is-active', 'research-worker.service']) == 'active'
        pid = run(['systemctl', '--user', 'show', 'research-worker.service', '-p', 'MainPID', '--value'])
        assert int(pid) > 0
        assert int(run(['systemctl', '--user', 'show', 'research-worker.service',
            '-p', 'ExecMainStartTimestampMonotonic', '--value'])) > previous_start
        assert Path(run(['systemctl', '--user', 'show', 'research-worker.service',
            '-p', 'WorkingDirectory', '--value'])) == current.company_repo
        installed_command = run(['systemctl', '--user', 'show', 'research-worker.service', '-p', 'ExecStart', '--value'])
        assert installed_command.split('; ignore_errors=', 1)[0] == command.split('; ignore_errors=', 1)[0]
        verify_company_pin(current)
        assert resolve_release_config(current, old.company_commit) == old
        refs = json.loads(registry.read_bytes())['releases']
        assert all(refs[commit] == path for commit, path in old_registry['releases'].items())
        assert all(getattr(current, name) == getattr(old, name) for name in
            ('api_url', 'worker_id', 'token_file', 'state_dir', 'repo_source', 'input_source', 'evidence_repo', 'research_python'))
        record.update(phase='active', config_sha256=sha_file(active), dropin_sha256=sha_file(drop),
            registry_sha256=sha_file(registry), pid=int(pid), company_repo=str(current.company_repo),
            old_profile_paths_preserved=True, previous_releases_resolvable=True,
            systemd_working_directory_and_new_start_verified=True, main_command_preserved=True,
            process_cwd_directly_inspected=False,
            token_path_and_state_directory_preserved=True, completed_at=datetime.now(UTC).isoformat())
        atomic_json(rollback / 'activation.json', record)
        print(json.dumps(record), flush=True)
    except Exception as exc:
        if activated:
            rollback_release(active_config=active)
        drop.write_bytes((rollback / 'dropin.before.conf').read_bytes())
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True, timeout=60)
        subprocess.run(['systemctl', '--user', 'restart', 'research-worker.service'], check=True, timeout=60)
        record.update(phase='rolled_back', error_type=type(exc).__name__, error=str(exc)[:120])
        atomic_json(rollback / 'activation.json', record)
        raise


if __name__ == '__main__':
    os.umask(0o077)
    main()
