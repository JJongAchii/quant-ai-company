"""Check portable Git bundles and typed contracts without executing candidate code."""

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from quant_company.research.adaptive_contracts import RESEARCH_RECIPE, AdaptiveManifest, digest_model
from quant_company.research.adaptive_executor import AdaptiveProfile, _sandbox_spec
from quant_company.research.domestic_profile import CANDIDATE
from quant_company.research.mission_contracts import TrialPlan
from quant_company.research.policy_contracts import record_digest, scoped_kwargs
from quant_company.research.program_contracts import ResearchProgram
from quant_company.research.worker import atomic_json
from quant_company.research.workspace import prepare_workspace

ROOT = Path(__file__).resolve().parents[4]
EVIDENCE = Path(__file__).resolve().parent
LOCAL = ROOT / ".local/conditional-activation-20261001"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest_path = EVIDENCE / "worker-package.json"
    package_manifest = json.loads(manifest_path.read_bytes())
    package = ROOT / package_manifest["local_package"]
    expected = {"company.bundle", "qualification.bundle", "warmup.json", "development.json",
                "receipt.json", "worker-profile.json", "candidate-program.json", "qualify-worker-inputs.py"}
    assert set(package_manifest["files"]) == expected
    assert (package / "worker-package.json").read_bytes() == manifest_path.read_bytes()
    assert all(sha(package / name) == digest for name, digest in package_manifest["files"].items())
    program = ResearchProgram.model_validate_json((package / "candidate-program.json").read_bytes())
    assert record_digest(program) == package_manifest["program_digest"]
    spec = program.envelopes[0].template
    profile = AdaptiveProfile.model_validate_json((package / "worker-profile.json").read_bytes())
    assert digest_model(profile.public_profile) == spec.execution_profile_digest
    assert profile.public_profile.data_policy_digest == record_digest(spec.data_policy)
    assert profile.public_profile.qualification_input_names == ["warmup.json"]
    assert all(package_manifest["files"][name] == digest for name, digest in spec.data.input_files.items())
    check_root = LOCAL / "package-validation-02"
    check_root.mkdir(mode=0o700, exist_ok=False)
    company = prepare_workspace(package / "company.bundle", package_manifest["files"]["company.bundle"],
        package_manifest["source_commit"], check_root / "company", (), (), ())
    source_manifest = json.loads((EVIDENCE / "source-manifest.json").read_bytes())
    assert {name.removeprefix("src/quant_company/"): digest for name, digest in company.manifest["files"].items()
            if name.startswith("src/quant_company/")} == source_manifest["company_files"]
    source = prepare_workspace(package / "qualification.bundle", package_manifest["files"]["qualification.bundle"],
        package_manifest["qualification_code_commit"], check_root / "qualification", (), (), ())
    files = source.manifest["files"]
    assert files == package_manifest["qualification_code_files"]
    assert all(files[name] == digest for name, digest in package_manifest["protected_files"].items())
    assert (source.worktree / "labs/company-domestic-research/candidate.py").read_text() == CANDIDATE
    config_path = "labs/company-domestic-research/config.json"
    changed = subprocess.check_output(["git", "diff", "--name-only", "-z", "--no-ext-diff", "--no-textconv",
        spec.code.base_commit, source.commit, "--"], cwd=source.worktree).split(b"\0")
    assert {name.decode() for name in changed if name} == {config_path}
    trial_id = uuid5(NAMESPACE_URL, "engineering-qualification:" + package_manifest["program_digest"])
    plan = TrialPlan(**scoped_kwargs(spec), trial_id=trial_id, proposal_id=trial_id,
        mission_digest=record_digest(spec), execution_profile=spec.execution_profile,
        implementer="operator non-performance engineering qualification", repository="quant-lab",
        code_commit=source.commit, changed_paths=[config_path], config_files={config_path: files[config_path]},
        input_files=spec.data.input_files, lake_id=spec.data.lake_id, development=spec.development,
        worker_id="worker", hostname="DESKTOP-5T00NAF", gpu="NVIDIA GeForce RTX 3070")
    bound = AdaptiveManifest(id=RESEARCH_RECIPE, mission_id=trial_id, mission_digest=record_digest(spec),
        trial_id=trial_id, plan_digest=record_digest(plan), plan=plan, spec=spec,
        code_files={name: files[name] for name in profile.public_profile.code_paths},
        bundle_sha256=source.bundle_sha256, config_path=config_path, company_commit=package_manifest["source_commit"])
    job = check_root / "contract-check"
    job.mkdir(mode=0o700)
    atomic_json(job / "manifest.json", bound.model_dump(mode="json"))
    planned = _sandbox_spec(profile, bound, source.worktree, job, "qualify")
    assert {mount.target for mount in planned.input_mounts} == {"warmup.json", "__contract__/manifest.json"}
    assert planned.fixture_only is False
    receipt = {
        "schema_version": 1, "state": "local_package_metadata_verified_runtime_pending",
        "observed_at": datetime.now(UTC).isoformat(), "source_commit": package_manifest["source_commit"],
        "worker_package_sha256": sha(manifest_path), "package_files_verified": len(expected),
        "all_bundled_company_source_files_verified": True, "protected_files_verified": True,
        "only_qualification_config_changed": True, "typed_plan_and_manifest_valid": True,
        "program_digest": record_digest(program), "profile_digest": digest_model(profile.public_profile),
        "qualification_manifest_sha256": sha(job / "manifest.json"),
        "planned_input_mounts": sorted(mount.target for mount in planned.input_mounts),
        "candidate_code_executed": False, "actual_bubblewrap_executed": False,
        "actual_3070_qualification": False, "operating_worker_config_changed": False,
        "market_performance_computed": False, "scientific_trials_added": 0,
    }
    with (EVIDENCE / "worker-package-validation.json").open("x") as stream:
        stream.write(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
