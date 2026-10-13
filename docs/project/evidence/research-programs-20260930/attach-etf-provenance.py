"""Add one hash-bound provenance report to the existing approved ETF packets.

Run on the company host. Preserves every input, engine, report and blocking gap;
qualifies with the installed service before atomically changing the registry.
The durable journal prevents automatic replay after an uncertain activation.
"""

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path

STATE = Path("/var/lib/quant-company")
COMMIT = "73fcfc34a6876e0a80c14fa72edae2cdb8413b0c"
OLD_SHA = "3ff9e81e3f4c3f16b2a9a3e6f6ce24946cfbc05ee0d1de9604a09b44092a7b22"
PROGRAM_ID = "e06537d3-fac3-5c8c-bf25-ddabb3c7e282"
PROGRAM_DIGEST = "53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b"
APPROVAL = ("slack:T0C1YRDRPNF:C0C2B9EUEGM:1790641272.310813:director:action:"
            "21f480c4-d59f-45e7-ac2e-2cf91aa109e8:approve")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save_new(path, data):
    if path.exists():
        if path.is_symlink() or path.read_bytes() != data:
            raise RuntimeError("immutable_evidence_file_drift")
        return
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o444)


def program():
    query = ("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; "
             "SELECT json_build_object('state',state,'digest',manifest_digest,"
             "'approval',approval_event_id,'spec',spec) FROM research_programs WHERE id='"
             + PROGRAM_ID + "'; ROLLBACK;")
    result = subprocess.check_output([
        "docker", "exec", "-i", "-u", "postgres", "quant-company-postgres-1", "psql",
        "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-d", "quant_company", "-c", query,
    ], text=True)
    return next(json.loads(line) for line in result.splitlines() if line.startswith("{"))


def worker():
    return json.loads(subprocess.check_output(["docker", "inspect", "quant-company-worker-1"]))[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha", required=True)
    parser.add_argument("--registry-sha", required=True)
    parser.add_argument("--report-sha", required=True)
    parser.add_argument("--expected-release", required=True)
    args = parser.parse_args()
    for digest in (args.archive_sha, args.registry_sha, args.report_sha):
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid_digest")
    if len(args.expected_release) != 40 or any(c not in "0123456789abcdef" for c in args.expected_release):
        raise ValueError("invalid_release_commit")
    directory = STATE / "research/provisioned/data-evidence"
    registry = directory / "registry.json"
    report_name = "provenance-recovery-" + args.report_sha[:12] + ".json"
    journal = STATE / "release-prep" / ("etf-provenance-" + args.report_sha[:12] + ".json")
    with (STATE / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        initial = worker()
        authority = program()
        old_bytes = registry.read_bytes()
        if (journal.exists() or registry.is_symlink() or sha(old_bytes) != OLD_SHA
                or args.archive.is_symlink() or sha(args.archive.read_bytes()) != args.archive_sha
                or Path("/opt/quant-company/current").resolve().name != args.expected_release
                or initial["Config"]["Image"] != "quant-company-autonomous:" + COMMIT
                or initial["State"]["Status"] != "running"
                or "MODEL_RUNTIME_URL=http://codex-runtime:8080" not in initial["Config"]["Env"]
                or "MODEL_ACCOUNTS_ENABLED=true" not in initial["Config"]["Env"]
                or "RESEARCH_DATA_EVIDENCE_FILE=/state/research/provisioned/data-evidence/registry.json"
                    not in initial["Config"]["Env"]
                or {k: authority[k] for k in ("state", "digest", "approval")}
                    != {"state": "active", "digest": PROGRAM_DIGEST, "approval": APPROVAL}):
            raise RuntimeError("attachment_precondition_drift")
        paths = ["company.py", "config.py", "research/data_evidence.py", "research/program_controller.py",
                 "research/programs.py", "research/program_contracts.py", "research/builds.py",
                 "research/adaptive_contracts.py", "research/contracts.py", "research/mission_contracts.py"]
        source_code = ("import hashlib,json,quant_company; from pathlib import Path; "
                       "root=Path(quant_company.__file__).parent; paths=" + repr(paths) + "; "
                       "print(json.dumps({p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths}))")
        installed = json.loads(subprocess.check_output([
            "docker", "exec", "quant-company-worker-1", "python", "-c", source_code,
        ], text=True))
        pinned = Path("/opt/quant-company/releases") / COMMIT / "src/quant_company"
        if installed != {path: sha((pinned / path).read_bytes()) for path in paths}:
            raise RuntimeError("installed_worker_source_drift")
        with tarfile.open(args.archive, "r") as archive:
            members = archive.getmembers()
            if (len(members) != 2 or {m.name for m in members} != {"registry.json", report_name}
                    or any(not m.isfile() or m.size > 1_000_000 for m in members)):
                raise RuntimeError("archive_scope_drift")
            files = {m.name: archive.extractfile(m).read() for m in members}
        if sha(files["registry.json"]) != args.registry_sha or sha(files[report_name]) != args.report_sha:
            raise RuntimeError("attachment_hash_drift")
        before, after = json.loads(old_bytes), json.loads(files["registry.json"])
        expected = json.loads(old_bytes)
        for packet in expected["packets"]:
            packet["reports"][report_name] = {
                "path": "/state/research/provisioned/data-evidence/" + report_name,
                "sha256": args.report_sha,
            }
        if after != expected or len(before["packets"]) != 2:
            raise RuntimeError("attachment_changed_existing_contract_or_gaps")
        report = json.loads(files[report_name])
        if (report["program_digest"] != PROGRAM_DIGEST or report["data_readiness_decision"] != "not_made"
                or report["sealed_price_rows_read"] or report["strategy_performance_computed"]
                or report["scientific_trials_added"] != 0):
            raise RuntimeError("report_scope_drift")
        candidate = directory / ("registry-" + args.registry_sha[:12] + ".json")
        save_new(directory / report_name, files[report_name])
        save_new(candidate, files["registry.json"])
        code = """import json,sys
from pathlib import Path
from types import SimpleNamespace
from quant_company.company import fingerprint
from quant_company.research.data_evidence import load_packets
from quant_company.research.program_contracts import ResearchProgram
data=json.load(sys.stdin)
spec=ResearchProgram.model_validate(data['authority']['spec'])
assert fingerprint(spec.model_dump(mode='json'))==data['authority']['digest']
company=SimpleNamespace(settings=SimpleNamespace(research_artifact_dir=Path('/state/research'),
 research_data_evidence_file=Path(data['registry']),
 research_profiles_file=Path('/etc/quant-company/research-profiles.json'),fixture_mode=False))
packets=load_packets(company,{'manifest_digest':data['authority']['digest']},{e.name:e for e in spec.envelopes})
assert sorted(packets)==['etf_claim','etf_strategy']
assert all(len(packet.blocking_gaps)==5 and len(packet.reports)==4 for packet,_ in packets.values())
print(json.dumps({'verified_envelopes':sorted(packets),
 'packet_digests':{name:fingerprint(p.model_dump(mode='json')) for name,(p,_) in packets.items()},
 'report_count_per_packet':4,'blocking_gaps_per_packet':5},sort_keys=True))
"""
        validation = subprocess.run(
            ["docker", "exec", "-i", "quant-company-worker-1", "python", "-c", code],
            input=json.dumps({"authority": authority, "registry": "/state/research/provisioned/data-evidence/"
                              + candidate.name}), text=True, capture_output=True, check=True,
        )
        proof = json.loads(validation.stdout)
        if (program() != authority or registry.read_bytes() != old_bytes or worker()["Id"] != initial["Id"]
                or Path("/opt/quant-company/current").resolve().name != args.expected_release):
            raise RuntimeError("attachment_state_changed_before_activation")
        save_new(directory / ("registry-" + OLD_SHA[:12] + ".json"), old_bytes)
        receipt = {"schema_version": 1, "state": "qualified_before_activation",
                   "observed_at": datetime.now(UTC).isoformat(), "program_id": PROGRAM_ID,
                   "program_digest": PROGRAM_DIGEST, "approval_event_id": APPROVAL,
                   "archive_sha256": args.archive_sha, "old_registry_sha256": OLD_SHA,
                   "new_registry_sha256": args.registry_sha, "report_sha256": args.report_sha,
                   "report_name": report_name, "packet_proof": proof, "worker_id": initial["Id"],
                   "worker_image": initial["Config"]["Image"], "services_recreated": False,
                   "active_release_commit": args.expected_release, "installed_worker_sha256": installed,
                   "blocking_gaps_unchanged": True, "input_and_execution_contract_unchanged": True,
                   "data_readiness_granted": False, "scientific_trials_added_by_attachment": 0}
        save_new(journal, (json.dumps(receipt, sort_keys=True) + "\n").encode())
        os.replace(candidate, registry)
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if sha(registry.read_bytes()) != args.registry_sha or worker()["Id"] != initial["Id"]:
            raise RuntimeError("activation_readback_requires_operator_inspection")
        receipt.update(state="provenance_report_attached", completed_at=datetime.now(UTC).isoformat())
        completed = journal.with_suffix(".completed.json")
        save_new(completed, (json.dumps(receipt, sort_keys=True) + "\n").encode())
        print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
