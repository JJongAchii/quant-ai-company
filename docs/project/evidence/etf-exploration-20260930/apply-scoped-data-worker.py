"""Select the already-qualified 86 release and align only the existing 3070 poller."""

import hashlib
import json
import pathlib
import subprocess
import sys
from datetime import UTC, datetime

SOURCE = "86aea8cdf03b5543666813c97689acb1e8158a6d"
PREVIOUS = "5c44ad07273adc780a56a47d7d35114fd0dc32c8"
BASE = pathlib.Path("/home/achii/quant-company-qualification/scoped-data-20261006")
ACTIVE = pathlib.Path("/home/achii/.config/quant-company/research-worker.json")
DROPIN = pathlib.Path("/home/achii/.config/systemd/user/research-worker.service.d/99-active-release.conf")
PROOF = BASE / (SOURCE + "-qualification.json")
JOURNAL = BASE / (SOURCE + "-activation.json")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def systemctl(*args):
    return subprocess.check_output(["systemctl", "--user", *args], text=True, timeout=60).strip()


proof = json.loads(PROOF.read_bytes())
assert proof["state"] == "inactive_3070_release_qualified" and proof["source_commit"] == SOURCE
assert sha(ACTIVE) == proof["active_config_sha256"] == "3b7b27cd7715826a5cfeada8ff95594e8db9b55da3b1ed24c0a38914dedaa344"
assert sha(DROPIN) == "bd44674d7ccd9f0449bdedf62eb851fdb440b472cc7ae327433292c5393f44f3"
assert not JOURNAL.exists(), "read_existing_activation_before_retry"
old_json = json.loads(ACTIVE.read_bytes())
assert old_json["company_commit"] == PREVIOUS
assert systemctl("is-active", "research-worker.service") == "active"
assert systemctl("show", "research-worker.service", "--value", "-p", "KillMode") == "process"
assert systemctl("show", "research-worker.service", "--value", "-p", "WorkingDirectory") == old_json["company_repo"]
sys.path.insert(0, old_json["company_repo"] + "/src")
from quant_company.research.releases import (  # noqa: E402
    PreparedRelease,
    activate_release,
    verify_company_pin,
)
from quant_company.research.worker import WorkerConfig, atomic_json  # noqa: E402

directory = pathlib.Path(proof["candidate_directory"])
record = json.loads((directory / "release.json").read_bytes())
assert sha(directory / "release.json") == proof["release_receipt_sha256"]
release = PreparedRelease(SOURCE, directory, directory / "worker-config.json",
                          proof["candidate_config_sha256"], record["code_files"])
candidate = WorkerConfig.from_file(release.config_path)
verify_company_pin(candidate)
assert {key for key, value in candidate.model_dump(mode="json").items() if old_json.get(key) != value} == {
    "company_repo", "company_commit"}
profile = json.loads(candidate.adaptive_profiles["kr-etf-retrospective-v1"].read_bytes())
assert sha(candidate.adaptive_profiles["kr-etf-retrospective-v1"]) == "e17bd51ef171b82d520cd27bdb14fe4033f99cdc6771c37d82b6b509da235110"
inputs = {"warmup.json": "d7d240e4f325d89106ba0364f43370174792a6e4e5155489118b2f0e641f6882",
          "development.json": "7ec748039094e171d17ddfa966c89ed5fb80f254516c0808346f850906ae9c2b"}
assert {name: sha(pathlib.Path(path)) for name, path in profile["input_sources"].items()} == inputs
registry = json.loads(candidate.release_registry_file.read_bytes())
assert PREVIOUS in registry["releases"] and SOURCE not in registry["releases"]
backup = BASE / "activation-before"
backup.mkdir(mode=0o700, exist_ok=False)
for name, path in (("worker-config.json", ACTIVE), ("99-active-release.conf", DROPIN),
                   ("registry.json", candidate.release_registry_file)):
    target = backup / name
    target.write_bytes(path.read_bytes())
    target.chmod(0o600)
receipt = {"schema_version": 1, "phase": "prepared", "source_commit": SOURCE, "previous_commit": PREVIOUS,
           "qualification_sha256": sha(PROOF), "previous_config_sha256": sha(ACTIVE),
           "previous_dropin_sha256": sha(DROPIN), "candidate_config_sha256": release.config_sha256,
           "company_repo": str(candidate.company_repo), "input_sha256": inputs,
           "authority": "Existing owner deployment and continuation request; reviewed PR105 scoped-format operation",
           "detached_execution_killed": False, "scientific_trials_added_by_operator": 0,
           "started_at": datetime.now(UTC).isoformat()}
atomic_json(JOURNAL, receipt)
try:
    systemctl("stop", "research-worker.service")
    receipt["phase"] = "poller_stopped"
    atomic_json(JOURNAL, receipt)
    activate_release(release, active_config=ACTIVE)
    text = ("[Service]\nWorkingDirectory=" + str(candidate.company_repo) + "\nEnvironment=\"PYTHONPATH="
            + str(candidate.company_repo / "src") + "\"\n")
    temporary = DROPIN.with_name(DROPIN.name + ".scoped-tmp")
    assert not temporary.exists()
    temporary.write_text(text)
    temporary.chmod(0o600)
    temporary.replace(DROPIN)
    systemctl("daemon-reload")
    systemctl("start", "research-worker.service")
    assert systemctl("is-active", "research-worker.service") == "active"
    assert systemctl("show", "research-worker.service", "--value", "-p", "WorkingDirectory") == str(candidate.company_repo)
    assert systemctl("show", "research-worker.service", "--value", "-p", "KillMode") == "process"
    assert sha(ACTIVE) == release.config_sha256
    retained = json.loads(candidate.release_registry_file.read_bytes())["releases"]
    assert all(retained.get(key) == value for key, value in registry["releases"].items())
    receipt.update(phase="active_worker_aligned", active_config_sha256=sha(ACTIVE),
                   active_dropin_sha256=sha(DROPIN), previous_releases_retained=True,
                   completed_at=datetime.now(UTC).isoformat())
    atomic_json(JOURNAL, receipt)
except Exception as error:
    receipt.update(failed_phase=receipt["phase"], phase="forward_reconciliation_required", error_type=type(error).__name__)
    atomic_json(JOURNAL, receipt)
    raise
print(json.dumps(receipt))
