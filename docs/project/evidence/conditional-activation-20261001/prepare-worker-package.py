"""Build a portable, hash-bound warmup qualification package without running it."""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from quant_company.research.domestic_profile import CANDIDATE
from quant_company.research.program_contracts import ResearchProgram
from quant_company.research.workspace import TextPatch, prepare_workspace

ROOT = Path(__file__).resolve().parents[4]
EVIDENCE = Path(__file__).resolve().parent
LOCAL = ROOT / ".local/conditional-activation-20261001"
SOURCE = "19807db8d1817a3a495ac10eee34b1bac2d287fe"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", type=int, default=2)
    args = parser.parse_args()
    assert args.attempt > 0
    assert not (EVIDENCE / "worker-package.json").exists(), "prepared_receipt_already_exists"
    program = ResearchProgram.model_validate_json((EVIDENCE / "candidate-program.json").read_bytes())
    spec = program.envelopes[0].template
    profile = json.loads((LOCAL / "profile/server-profile.json").read_bytes())
    code = LOCAL / "profile/source/code/labs/company-domestic-research"
    assert (code / "candidate.py").read_text() == CANDIDATE, "qualification_requires_engineering_baseline"
    config = "labs/company-domestic-research/config.json"
    qualification = prepare_workspace(Path(profile["source_bundle"]), profile["source_bundle_sha256"],
        profile["base_commit"], LOCAL / f"qualification-source-{args.attempt:02d}",
        (config,), tuple(profile["public_profile"]["protected_paths"]),
        (TextPatch(path=config, expected_text=(code / "config.json").read_text(),
                   replacement_text=json.dumps({"schema_version": 1,
                       "description": "Warmup transport qualification only; no evaluation or model training"})),))
    assert qualification.commit != spec.code.base_commit
    package = LOCAL / f"worker-package-{args.attempt:02d}"
    package.mkdir(mode=0o700, exist_ok=False)
    # Git bundles need a named reference; the pinned commit must be reachable.
    subprocess.run(["git", "merge-base", "--is-ancestor", SOURCE, "HEAD"], cwd=ROOT, check=True)
    subprocess.run(["git", "bundle", "create", str(package / "company.bundle"), "HEAD"], cwd=ROOT, check=True)
    shutil.copyfile(qualification.bundle_path, package / "qualification.bundle")
    for name in ("warmup.json", "development.json", "receipt.json"):
        shutil.copyfile(LOCAL / "inputs" / name, package / name)
    for name in ("worker-profile.json",):
        shutil.copyfile(LOCAL / "profile" / name, package / name)
    shutil.copyfile(EVIDENCE / "candidate-program.json", package / "candidate-program.json")
    shutil.copyfile(EVIDENCE / "qualify-worker-inputs.py", package / "qualify-worker-inputs.py")
    from quant_company.company import fingerprint

    receipt = {
        "schema_version": 1, "state": "portable_package_prepared_not_executed",
        "attempt": args.attempt, "local_package": str(package.relative_to(ROOT)),
        "source_commit": SOURCE, "program_digest": fingerprint(program.model_dump(mode="json")),
        "qualification_code_commit": qualification.commit,
        "qualification_code_files": qualification.manifest["files"],
        "protected_files": {name: qualification.manifest["files"][name]
                            for name in profile["public_profile"]["protected_paths"]},
        "files": {path.name: sha(path) for path in package.iterdir() if path.is_file()},
        "qualification_market_mounts": ["warmup.json"], "evaluation_called": False,
        "scientific_trials_added": 0, "operating_worker_config_changed": False,
        "worker_profile_registration": "not_done", "actual_3070_qualification": "not_done",
    }
    content = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    (package / "worker-package.json").write_text(content)
    with (EVIDENCE / "worker-package.json").open("x") as stream:
        stream.write(content)
    print(json.dumps({"state": receipt["state"], "files": len(receipt["files"]),
                      "qualification_code_commit": qualification.commit, "actual_3070_qualification": False}))


if __name__ == "__main__":
    main()
