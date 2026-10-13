"""Operator-only 3070 warmup qualification; never evaluate or activate a poller."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    package, destination = args.package.resolve(), args.destination.absolute()
    active_path = Path("/home/achii/.config/quant-company/research-worker.json")
    active_raw = active_path.read_bytes()
    active = json.loads(active_raw)
    sys.path.insert(0, active["company_repo"] + "/src")
    from quant_company.research.releases import prepare_release
    from quant_company.research.worker import WorkerConfig, atomic_json, sha_file

    manifest = json.loads((package / "worker-package.json").read_bytes())
    assert manifest["source_commit"] == "19807db8d1817a3a495ac10eee34b1bac2d287fe"
    assert destination.is_absolute() and not destination.exists()
    assert all(sha_file(package / name) == digest for name, digest in manifest["files"].items())
    destination.mkdir(mode=0o700)
    profile_root, inputs = destination / "profile", destination / "inputs"
    profile_root.mkdir(mode=0o700)
    inputs.mkdir(mode=0o700)
    for name in ("warmup.json", "development.json", "receipt.json"):
        shutil.copyfile(package / name, inputs / name)
        (inputs / name).chmod(0o444)
    worker = json.loads((package / "worker-profile.json").read_bytes())
    worker["input_sources"] = {name: str(inputs / name) for name in worker["input_sources"]}
    worker["allowed_input_roots"] = [str(inputs)]
    profile_path = profile_root / "worker-profile.json"
    atomic_json(profile_path, worker)
    profile_path.chmod(0o444)
    release_root = destination / "release-candidates"
    release_root.mkdir(mode=0o700)
    config = WorkerConfig.model_validate({**active, "adaptive_profiles": {
        **active["adaptive_profiles"], "kr-etf-retrospective-v1": str(profile_path),
    }})
    prepared = prepare_release(source_snapshot=package / "company.bundle",
        snapshot_sha256=manifest["files"]["company.bundle"], commit=manifest["source_commit"],
        release_root=release_root, config=config)
    # Only pinned trusted company code is imported in this subprocess. Candidate
    # code is callable only inside the networkless sandbox with warmup mounts.
    code = r'''
import json,pathlib,sys
from uuid import uuid4
from quant_company.research.adaptive_contracts import AdaptiveManifest,RESEARCH_RECIPE,digest_model
from quant_company.research.adaptive_executor import AdaptiveProfile,_sandbox_spec,_public_sandbox_receipt,bind_producer_record,qualification_from_file,verify_host
from quant_company.research.mission_contracts import TrialPlan
from quant_company.research.policy_contracts import record_digest,scoped_kwargs
from quant_company.research.program_contracts import ResearchProgram
from quant_company.research.sandbox import run_sandbox
from quant_company.research.worker import atomic_json,sha_file
from quant_company.research.workspace import prepare_workspace,snapshot_files
P=pathlib.Path
package,base,profile_path=map(P,sys.argv[1:])
package_manifest=json.loads((package/'worker-package.json').read_bytes())
program=ResearchProgram.model_validate_json((package/'candidate-program.json').read_bytes())
assert record_digest(program)==package_manifest['program_digest']
spec=program.envelopes[0].template
profile=AdaptiveProfile.model_validate_json(profile_path.read_bytes())
source=prepare_workspace(package/'qualification.bundle',package_manifest['files']['qualification.bundle'],package_manifest['qualification_code_commit'],base/'qualification-source',(),(),())
job=base/'warmup-qualification';job.mkdir(mode=0o700)
trial=uuid4();mission_digest=record_digest(spec)
config_path='labs/company-domestic-research/config.json'
files=snapshot_files(source.worktree,source.commit)
plan=TrialPlan(**scoped_kwargs(spec),trial_id=trial,proposal_id=uuid4(),mission_digest=mission_digest,
 execution_profile=spec.execution_profile,implementer='operator non-performance engineering qualification',
 repository='quant-lab',code_commit=source.commit,changed_paths=[config_path],
 config_files={config_path:files[config_path]},input_files=spec.data.input_files,lake_id=spec.data.lake_id,
 development=spec.development,worker_id='worker',hostname='DESKTOP-5T00NAF',gpu='NVIDIA GeForce RTX 3070')
bound=AdaptiveManifest(id=RESEARCH_RECIPE,mission_id=uuid4(),mission_digest=mission_digest,trial_id=trial,
 plan_digest=record_digest(plan),plan=plan,spec=spec,
 code_files={name:files[name] for name in profile.public_profile.code_paths},
 bundle_sha256=source.bundle_sha256,config_path=config_path,company_commit=package_manifest['source_commit'])
verify_host(bound)
atomic_json(job/'manifest.json',bound.model_dump(mode='json'))
sandbox_spec=_sandbox_spec(profile,bound,source.worktree,job,'qualify')
assert {mount.target for mount in sandbox_spec.input_mounts}=={'warmup.json','__contract__/manifest.json'}
sandbox=run_sandbox(sandbox_spec)
assert sandbox.exit_code==0 and not sandbox.timed_out
producer_path=job/'qualify/qualification.json'
producer=qualification_from_file(producer_path,bound,profile.public_profile,producer=True)
original=job/'producer-qualification.json';original.write_bytes(producer_path.read_bytes())
wrapped=bind_producer_record(producer,original,bound)
atomic_json(job/'qualification.json',wrapped.model_dump(mode='json'))
consumer=qualification_from_file(job/'qualification.json',bound,profile.public_profile)
assert consumer.schema_version==2 and consumer.research_scope==spec.research_scope and consumer.sample_count==104
assert sha_file(original)==consumer.producer_sha256
assert not list(job.rglob('result.json')) and not list(job.rglob('*.csv'))
atomic_json(job/'sandbox.json',_public_sandbox_receipt(sandbox,'qualify',sandbox_spec,bound))
print(json.dumps({'schema_version':1,'source_commit':package_manifest['source_commit'],'program_digest':package_manifest['program_digest'],
 'host':'DESKTOP-5T00NAF','gpu':'NVIDIA GeForce RTX 3070','warmup_rows':1040,'warmup_sessions':consumer.sample_count,
 'profile_digest':digest_model(profile.public_profile),'research_scope':consumer.research_scope.model_dump(mode='json'),
 'producer_sha256':sha_file(original),'consumer_sha256':sha_file(job/'qualification.json'),
 'sandbox_receipt_sha256':sha_file(job/'sandbox.json'),'actual_bubblewrap':True,
 'mounted_market_inputs':['warmup.json'],'development_price_rows_mounted':False,'sealed_price_rows_read':False,
 'baseline_called_twice_for_determinism':True,'model_training':False,'performance_computed':False,'scientific_trials_added':0,
 'qualified_for_frozen_input_transport':True,'staff_data_admission_granted':False,'owner_program_approved':False}))
'''
    private_log = destination / "qualification.log"
    env = dict(os.environ, PYTHONPATH=str(prepared.directory / "code/src"), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-B", "-c", code, str(package), str(destination), str(profile_path)],
                            capture_output=True, text=True, timeout=300, env=env)
    assert active_path.read_bytes() == active_raw, "operating_worker_config_changed"
    if result.returncode:
        private_log.write_text(result.stderr)
        raise RuntimeError("real_warmup_qualification_failed; private log preserved; do not automatically replay")
    proof = json.loads(result.stdout)
    proof.update(observed_at=datetime.now(UTC).isoformat(),
        prepared_release=str(prepared.directory), prepared_config_sha256=prepared.config_sha256,
        prepared_release_receipt_sha256=sha_file(prepared.directory / "release.json"),
        active_config_unchanged=True, poller_activated=False,
        active_company_commit=config.company_commit, worker_profile_sha256=sha_file(profile_path))
    atomic_json(destination / "qualification-receipt.json", proof)
    print(json.dumps(proof, ensure_ascii=False))


if __name__ == "__main__":
    main()
