"""Complete the already-active5c cutover by aligning only the3070 poller."""

import hashlib
import json
import os
import pathlib
import subprocess
import sys
from datetime import UTC, datetime

SOURCE = "5c44ad07273adc780a56a47d7d35114fd0dc32c8"
OLD = "231d6ba0755f658f167636a8ea2c3ef65b6af562"
ACTIVE = pathlib.Path("/home/achii/.config/quant-company/research-worker.json")
BASE = pathlib.Path("/home/achii/quant-company-qualification/data-format-20261006")
DROPIN = pathlib.Path("/home/achii/.config/systemd/user/research-worker.service.d/99-active-release.conf")
PRIOR = BASE / (SOURCE + "-alignment.json")
JOURNAL = BASE / (SOURCE + "-alignment-registered-02.json")
PROOF = BASE / (SOURCE + "-qualification.json")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def systemctl(*args):
    return subprocess.check_output(["systemctl", "--user", *args], text=True, timeout=60).strip()


proof = json.loads(PROOF.read_bytes())
assert proof["state"] == "inactive_3070_release_qualified" and proof["source_commit"] == SOURCE
assert sha(ACTIVE) == proof["active_config_sha256"] == "4d0b0c82e567c6a269b17610abaa282a01b1f18b261272600c076e28ee496c57"
assert sha(DROPIN) == "30da5ea715f02678b80a379334b912f2917e049d1bd808befe30e2003a0db286"
assert not JOURNAL.exists(), "reconcile_existing_alignment_journal"
prior = json.loads(PRIOR.read_bytes())
assert prior["phase"] == "forward_reconciliation_required" and prior["error_type"] == "ReleaseError"
old_json = json.loads(ACTIVE.read_bytes())
assert old_json["company_commit"] == OLD
assert systemctl("show", "research-worker.service", "--value", "-p", "KillMode") == "process"
assert systemctl("show", "research-worker.service", "--value", "-p", "WorkingDirectory") == old_json["company_repo"]
assert systemctl("is-active", "research-worker.service") == "active"
sys.path.insert(0, old_json["company_repo"] + "/src")
from quant_company.research.releases import (  # noqa: E402
    PreparedRelease,
    activate_release,
    verify_company_pin,
)
from quant_company.research.worker import WorkerConfig, atomic_json  # noqa: E402
from quant_company.research.workspace import snapshot_files  # noqa: E402

own_directory = pathlib.Path(proof["candidate_directory"])
own_record = json.loads((own_directory / "release.json").read_bytes())
assert sha(own_directory / "release.json") == proof["release_receipt_sha256"]
registry = json.loads(pathlib.Path(old_json["release_registry_file"]).read_bytes())
registered = pathlib.Path(registry["releases"][SOURCE])
assert sha(registered) == "3b7b27cd7715826a5cfeada8ff95594e8db9b55da3b1ed24c0a38914dedaa344"
directory = registered.parent
record = json.loads((directory / "release.json").read_bytes())
assert record["commit"] == SOURCE and record["config_sha256"] == sha(registered)
assert record["code_files"] == own_record["code_files"], "registered_release_differs_from_qualified_source"
release = PreparedRelease(SOURCE, directory, registered, sha(registered), record["code_files"])
candidate = WorkerConfig.from_file(release.config_path)
verify_company_pin(candidate)
assert snapshot_files(candidate.company_repo, SOURCE) == release.code_files
assert set(k for k, v in candidate.model_dump(mode="json").items() if old_json.get(k) != v) <= {"company_repo", "company_commit", "adaptive_profiles"}
old_profiles = old_json["adaptive_profiles"]
new_profiles = candidate.model_dump(mode="json")["adaptive_profiles"]
assert set(old_profiles) == set(new_profiles)
assert [key for key in old_profiles if old_profiles[key] != new_profiles[key]] == ["kr-etf-retrospective-v1"]
old_profile = json.loads(pathlib.Path(old_profiles["kr-etf-retrospective-v1"]).read_bytes())
new_profile = json.loads(pathlib.Path(new_profiles["kr-etf-retrospective-v1"]).read_bytes())
relocated = {"input_sources", "allowed_input_roots"}
assert {k: v for k, v in old_profile.items() if k not in relocated} == {k: v for k, v in new_profile.items() if k not in relocated}
input_hashes = {"warmup.json": "d7d240e4f325d89106ba0364f43370174792a6e4e5155489118b2f0e641f6882",
                "development.json": "7ec748039094e171d17ddfa966c89ed5fb80f254516c0808346f850906ae9c2b"}
for profile in (old_profile, new_profile):
    assert {name: sha(pathlib.Path(path)) for name, path in profile["input_sources"].items()} == input_hashes
registry = json.loads(candidate.release_registry_file.read_bytes())
assert OLD in registry["releases"], "retain_current_research_release"
backup = BASE / "alignment-registered-prior-02"
backup.mkdir(mode=0o700, exist_ok=False)
for name, path in (("worker-config.json", ACTIVE), ("99-active-release.conf", DROPIN),
                   ("registry.json", candidate.release_registry_file)):
    target = backup / name
    target.write_bytes(path.read_bytes())
    target.chmod(0o600)
receipt = {"schema_version": 1, "phase": "prepared", "source_commit": SOURCE, "previous_commit": OLD,
           "prior_alignment_sha256": sha(PRIOR), "registered_config": str(registered),
           "profile_relocation": {"old_sha256": sha(pathlib.Path(old_profiles["kr-etf-retrospective-v1"])),
                                  "new_sha256": sha(pathlib.Path(new_profiles["kr-etf-retrospective-v1"])),
                                  "public_runtime_bundle_and_all_other_fields_equal": True,
                                  "input_sha256": input_hashes},
           "qualification_sha256": sha(PROOF), "previous_config_sha256": sha(ACTIVE),
           "previous_dropin_sha256": sha(DROPIN), "prior_registry_sha256": sha(candidate.release_registry_file),
           "authority": "Owner requested continuation; complete the existing source5c cutover with matching3070 only",
           "detached_execution_killed": False, "scientific_trials_added": 0, "started_at": datetime.now(UTC).isoformat()}
atomic_json(JOURNAL, receipt)
try:
    systemctl("stop", "research-worker.service")
    receipt["phase"] = "poller_stopped"
    atomic_json(JOURNAL, receipt)
    assert sha(ACTIVE) == receipt["previous_config_sha256"] and sha(DROPIN) == receipt["previous_dropin_sha256"]
    selected = activate_release(release, active_config=ACTIVE)
    receipt["phase"] = "release_selected"
    atomic_json(JOURNAL, receipt)
    content = ("[Service]\nWorkingDirectory=" + str(selected.company_repo)
               + "\nEnvironment=PYTHONPATH=" + str(selected.company_repo / "src") + "\n")
    temporary = DROPIN.with_name(DROPIN.name + ".data-format-prepared")
    temporary.write_text(content)
    temporary.chmod(0o600)
    os.replace(temporary, DROPIN)
    systemctl("daemon-reload")
    assert systemctl("show", "research-worker.service", "--value", "-p", "WorkingDirectory") == str(selected.company_repo)
    systemctl("start", "research-worker.service")
    assert systemctl("is-active", "research-worker.service") == "active"
    restored = WorkerConfig.from_file(ACTIVE)
    verify_company_pin(restored)
    assert restored.company_commit == SOURCE and restored.company_repo == selected.company_repo
    receipt.update(phase="active_worker_aligned", active_config_sha256=sha(ACTIVE), active_dropin_sha256=sha(DROPIN),
                   registry_sha256=sha(candidate.release_registry_file), previous_release_retained=True,
                   company_repo=str(selected.company_repo), completed_at=datetime.now(UTC).isoformat())
    atomic_json(JOURNAL, receipt)
except Exception as error:
    receipt.update(last_successful_phase=receipt["phase"], phase="forward_reconciliation_required", error_type=type(error).__name__)
    atomic_json(JOURNAL, receipt)
    raise RuntimeError("worker_alignment_requires_reconciliation") from None
print(json.dumps(receipt, ensure_ascii=False))
