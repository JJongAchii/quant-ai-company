"""Prepare and qualify an inactive 3070 company release using generated data."""

import hashlib
import json
import os
import pathlib
import subprocess
import sys
from datetime import UTC, datetime

SOURCE = "defd4833adea6a0191412121a5a5c6e8265c58ba"
BUNDLE_SHA = "686069a14a9479488aeb7aee67431a91232b78bfa26be1ee5aa25247a6072943"
ACTIVE = pathlib.Path("/home/achii/.config/quant-company/research-worker.json")
BASE = pathlib.Path("/home/achii/quant-company-qualification/held-audit-20261006")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


before = sha(ACTIVE)
old_json = json.loads(ACTIVE.read_bytes())
assert old_json["company_commit"] == "86aea8cdf03b5543666813c97689acb1e8158a6d"
sys.path.insert(0, old_json["company_repo"] + "/src")
from quant_company.research.releases import prepare_release  # noqa: E402
from quant_company.research.worker import WorkerConfig, atomic_json  # noqa: E402

old = WorkerConfig.from_file(ACTIVE)
bundle = pathlib.Path("/tmp/" + SOURCE + "-company.bundle")
assert sha(bundle) == BUNDLE_SHA
BASE.mkdir(parents=True, mode=0o700, exist_ok=True)
releases = BASE / "release-candidates"
releases.mkdir(mode=0o700, exist_ok=True)
receipt_path = BASE / (SOURCE + "-qualification.json")
assert not receipt_path.exists(), "read_existing_qualification_receipt"
prepared = prepare_release(source_snapshot=bundle, snapshot_sha256=BUNDLE_SHA, commit=SOURCE,
                           release_root=releases, config=old)
profile = json.loads(old.adaptive_profiles["kr-etf-retrospective-v1"].read_bytes())
runtime_path = BASE / "runtime.json"
atomic_json(runtime_path, profile["runtime"])
env = dict(os.environ, PYTHONPATH=str(prepared.directory / "code/src"), PYTHONDONTWRITEBYTECODE="1",
           RESEARCH_DOMESTIC_RUNTIME_PROFILE=str(runtime_path))
with (BASE / (SOURCE + "-environment.private.log")).open("wb") as stream:
    installed = subprocess.run(["/home/achii/.local/bin/uv", "sync", "--frozen", "--python", str(old.research_python)],
        cwd=prepared.directory / "code", stdout=stream, stderr=subprocess.STDOUT, timeout=240)
assert installed.returncode == 0, "isolated_test_environment_setup_failed"
log = BASE / (SOURCE + "-qualification.private.log")
junit = BASE / (SOURCE + "-qualification.xml")
with log.open("wb") as stream:
    result = subprocess.run([str(prepared.directory / "code/.venv/bin/python"), "-B", "-m", "pytest", "-q",
        "tests/test_research_domestic_linux.py::test_conditional_protected_engine_in_actual_linux_sandbox",
        "--junitxml=" + str(junit)],
        cwd=prepared.directory / "code", env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=240)
assert sha(ACTIVE) == before, "operating_worker_config_changed"
if result.returncode:
    raise RuntimeError("qualification_failed_private_log_preserved")
proof_path = prepared.directory / "code/.local/worker-qualification/conditional-etf-strategy.json"
proof = json.loads(proof_path.read_bytes())
assert proof["company_commit"] == SOURCE and proof["strict_bundle_consumer_passed"]
assert proof["fixture_only"] and proof["scientific_trials_added"] == 0 and proof["real_snapshot_reads"] == 0
assert not proof["sandbox_mocked"] and not proof["host_check_mocked"]
receipt = {"schema_version": 1, "state": "inactive_3070_release_qualified", "source_commit": SOURCE,
           "bundle_sha256": BUNDLE_SHA, "candidate_directory": str(prepared.directory),
           "candidate_config_sha256": prepared.config_sha256, "release_receipt_sha256": sha(prepared.directory / "release.json"),
           "active_config_sha256": before, "active_config_unchanged": True, "active_commit": old.company_commit,
           "operating_profile_sha256": sha(old.adaptive_profiles["kr-etf-retrospective-v1"]),
           "qualification_junit_sha256": sha(junit),
           "operating_worker_restarted": False, "scientific_trials_added": 0,
           "qualification": proof, "observed_at": datetime.now(UTC).isoformat()}
atomic_json(receipt_path, receipt)
print(json.dumps(receipt, ensure_ascii=False))
