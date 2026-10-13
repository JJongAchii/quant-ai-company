"""Record prepared artifacts and external blockers; applies no operating release."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from quant_company.company import fingerprint
from quant_company.research.data_evidence import DataEvidencePacket
from quant_company.research.program_contracts import ResearchProgram

ROOT = Path(__file__).resolve().parents[4]
EVIDENCE = Path(__file__).resolve().parent
SOURCE = "19807db8d1817a3a495ac10eee34b1bac2d287fe"


def read(name):
    return json.loads((EVIDENCE / name).read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name, value):
    with (EVIDENCE / name).open("x") as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n")


def main():
    now = datetime.now(UTC).isoformat()
    before, final = read("server-baseline-20261002.json"), read("server-baseline-final-20261002.json")
    for key in ("app_release", "containers", "config_hashes", "registry_sha256", "registry_packets"):
        assert before[key] == final[key], "operating_baseline_drift:" + key
    database = final["database"]
    assert database == before["database"], "research_authority_or_execution_drift"
    assert not database["missions"] and not database["usage"]
    assert database["activity"]["running_turns"] == database["activity"]["active_jobs"] == 0
    assert database["activity"]["sending_outbox"] == 0
    assert database["lineage_tables_present"] is False
    program = ResearchProgram.model_validate_json((EVIDENCE / "candidate-program.json").read_bytes())
    packet = DataEvidencePacket.model_validate_json((EVIDENCE / "candidate-evidence-packet.json").read_bytes())
    program_digest = fingerprint(program.model_dump(mode="json"))
    review = read("review-package.json")
    assert program_digest == review["program_digest"] == packet.program_digest
    assert packet.research_scope == program.envelopes[0].template.research_scope
    assert packet.execution_profile_digest == review["profile_digest"]
    assert database["project"]["id"] == review["project_id"]
    assert database["project"]["revision"] == review["revision"]
    assert database["programs"][0]["id"] == review["old_program_id"]
    assert database["programs"][0]["manifest_digest"] == review["old_program_digest"]
    assert all((EVIDENCE / "source-reports" / name).is_file()
        and sha(EVIDENCE / "source-reports" / name) == digest for name, digest in review["reports"].items())
    origins = [{"program_id": task["program_id"], "task_id": task["id"], "task_digest": task["digest"]}
               for task in sorted(database["tasks"], key=lambda task: task["id"])]
    assert origins == read("lineage-preimage-preview.json")["history"]["origins"]
    assert all(fingerprint(task["proposal"]) == task["digest"] and task["mission_id"] is None
               and task["state"] == "waiting" for task in database["tasks"])
    images, ci, package = read("server-image-stage.json"), read("source-ci.json"), read("worker-package.json")
    assert images["source_commit"] == ci["source_commit"] == package["source_commit"] == SOURCE
    assert ci["conclusion"] == "success" and images["active_changed"] is False
    assert len(images["images"]) == 2
    assert all(image["isolated_parser_verification"]["program_digest"] == program_digest
               and image["package_tree_sha256"] == read("source-manifest.json")["company_tree_sha256"]
               for image in images["images"])
    assert package["actual_3070_qualification"] == "not_done"
    assert read("worker-package-validation.json")["actual_3070_qualification"] is False
    assert sha(EVIDENCE / "initial-image-stage.json") == images["failed_attempt"]["receipt_sha256"]
    write("worker-connectivity-final-20261002.json", {
        "schema_version": 1, "recorded_at": now, "worker": "worker", "ssh_exit_code": 255,
        "ssh_error": "ssh: connect to host quant-worker port 22: Operation timed out",
        "previous_network_observation": "worker-connectivity-20261002.json",
        "power_state": "unknown", "state": "ssh_unreachable",
        "actual_new_input_qualification": False, "alternative_worker_selected": False,
        "resume_condition": "designated_3070_WSL_Tailscale_and_SSH_available",
    })
    plan = {
        "schema_version": 1, "state": "prepared_awaiting_designated_worker",
        "recorded_at": now, "source_commit": SOURCE,
        "ci_receipt_sha256": sha(EVIDENCE / "source-ci.json"),
        "company_tree_sha256": read("source-manifest.json")["company_tree_sha256"],
        "archive_sha256": read("source-manifest.json")["archive_sha256"],
        "staged_images": images["images"], "operating_baseline_sha256": sha(EVIDENCE / "server-baseline-final-20261002.json"),
        "operating_config_hashes": final["config_hashes"], "operating_registry_sha256": final["registry_sha256"],
        "old_program_id": review["old_program_id"], "old_program_digest": review["old_program_digest"],
        "program_digest": program_digest, "data_policy_digest": review["data_policy_digest"],
        "execution_profile_digest": review["profile_digest"], "packet_digest": review["packet_digest"],
        "input_files": review["input_files"], "lineage_history_digest": review["lineage_history_digest"],
        "origin_task_count": len(origins), "prior_scientific_trials": 0, "prior_compute_seconds": 0,
        "source_base_commit": program.envelopes[0].template.code.base_commit,
        "worker_package_sha256": sha(EVIDENCE / "worker-package.json"),
        "pre_activation_gates": [
            "Designated DESKTOP-5T00NAF RTX3070 connects; recheck actual active config, release, runtime and job receipts",
            "Verify every portable package hash and qualify the real warmup input through actual bubblewrap and strict producer/consumer checks",
            "Reread source CI, all actual container/config identities, revision, jobs, reservations, stages and ambiguous model/outbox effects; abort on drift",
            "Hold new research submissions and reconcile execution; take a consistent existing DB/config/research backup under the shared deployment lock",
            "Use only prepared compatible images and source pins; preserve official ChatGPT authentication and all tokens/model routing",
        ],
        "intended_restarted_services": ["api", "dispatch", "slack-socket", "worker"],
        "preserve_other_service_container_ids": {name: item["id"] for name, item in final["containers"].items()
            if name not in {"quant-company-api-1", "quant-company-dispatch-1", "quant-company-slack-socket-1", "quant-company-worker-1"}},
        "activation_order": [
            "After the gates pass, migrate only additive company tables using the pinned compatible code",
            "Select the staged images for the four intended services; select the prepared 3070 consumer release and preserve all legacy release mappings",
            "Append the new profile and program-bound packet; retain every existing profile, input, report, packet and receipt",
            "Verify exact installed source/profile/input/engine/report hashes, API health, Temporal connection and worker heartbeat",
            "Call deployed scientific_lineages.history against actual PostgreSQL and match the exact ten origins, zero prior trials and canonical preimage before draft submission",
            "Publish the full canonical program digest through the validated company approval path; owner cancels the prior program and signs the fresh canonical target",
            "Observe actual independent data assessment and director task selection before any experiment can receive authority",
        ],
        "scientific_authority_granted": False, "actual_staff_review_completed": False,
        "owner_program_approval": None, "owner_Slack_request_sent": False,
        "actual_3070_new_input_qualification": False, "backup_taken_for_this_cutover": False,
        "operating_services_restarted": False, "production_database_mutated": False,
        "rollback": {
            "before_new_conditional_records": "Use captured compatible images/config only after reconciliation; retain all durable receipts and uncertain effects",
            "after_new_conditional_records": "Hold research, retain compatible parsers and apply a qualified forward repair; never restore a legacy parser over schema3 JSON",
        },
        "sealed_prices_read": False, "market_performance_computed": False, "scientific_trials_added": 0,
    }
    write("release-plan.json", plan)
    references = ["source-ci.json", "integration-validation.json", "source-manifest.json", "server-image-stage.json",
                  "initial-image-stage.json", "image-import-diagnosis.json", "numeric-quality.json", "data-policy.json",
                  "candidate-program.json", "candidate-evidence-packet.json", "review-package.json", "public-profile.json",
                  "profile-identity.json", "lineage-preimage-preview.json", "worker-package.json", "worker-package-validation.json",
                  "package-preparation-repair.json", "server-baseline-final-20261002.json", "worker-connectivity-final-20261002.json",
                  "release-plan.json"]
    write("intake-preparation-acceptance.json", {
        "schema_version": 1, "recorded_at": now, "state": "independent_preparation_complete_operational_intake_blocked",
        "source_commit": SOURCE, "files": {name: sha(EVIDENCE / name) for name in references},
        "all_operating_container_ids_and_config_hashes_preserved": True,
        "old_signed_program_and_ten_task_origins_preserved": True,
        "real_numeric_data_packet_prepared": True, "staged_image_parser_and_full_source_hashes_verified": True,
        "source_CI_passed": 1520, "integration_tests_passed": 320,
        "actual_staff_data_decision": "not_made", "actual_director_decision": "not_made",
        "actual_3070_new_input_qualification": False, "owner_program_approval": None,
        "owner_Slack_request_sent": False, "production_cutover_completed": False,
        "blocking_dependency": "designated_3070_worker_unreachable",
        "next_action": "restore_3070_connectivity_then_run_frozen_warmup_qualification",
        "market_performance_computed": False, "scientific_trials_added": 0,
    })
    status_path = ROOT / "docs/work/lamprey/STATUS.json"
    status = json.loads(status_path.read_bytes())
    assert status["currentIntent"] == "INTENT-v8.json"
    status["updatedAt"] = now
    progress = {
        "REAL_DATA_PACKET": ("verified", ["numeric-quality.json", "review-package.json", "worker-package-validation.json"],
            "Immutable real numeric inputs, canonical policy/report bytes and compatible profile prepared; this does not grant staff admission or actual worker qualification."),
        "RELEASE_INTAKE": ("blocked", ["source-ci.json", "server-image-stage.json", "release-plan.json", "worker-connectivity-final-20261002.json"],
            "Integrated source19807 passed CI and two server images are staged. Designated 3070 remains unreachable; no cutover, migration, profile registration or fresh backup performed."),
        "PROGRAM_REVIEW": ("in_progress", ["review-package.json", "lineage-preimage-preview.json", "intake-preparation-acceptance.json"],
            "Canonical program and actual ten prior origins prepared. Deployed history check, fresh signed Slack owner approval and actual data/director decisions remain pending worker recovery and release intake."),
    }
    for key, (state, names, note) in progress.items():
        status["deliverables"][key] = {"status": state, "note": note, "updatedAt": now,
            "evidence": [{"path": str((EVIDENCE / name).relative_to(ROOT)), "sha256": sha(EVIDENCE / name)[:12]} for name in names]}
    status_path.write_text(json.dumps(status, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"state": "prepared_worker_unreachable", "operating_baseline_unchanged": True,
                      "real_numeric_packet_verified": True, "source_commit": SOURCE,
                      "production_cutover_completed": False, "owner_Slack_request_sent": False}))


if __name__ == "__main__":
    main()
