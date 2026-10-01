"""Check draft bindings and exercise the current runtime's actual rejection gates."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError

from quant_company.company import PolicyError, fingerprint
from quant_company.research.adaptive_contracts import AdaptiveExecutionProfile, digest_model
from quant_company.research.builds import profile_for
from quant_company.research.domestic_profile import prepare_domestic_profile
from quant_company.research.mission_contracts import MissionSpec
from quant_company.research.program_contracts import DataAssessment, ResearchProgram

ROOT = Path(__file__).resolve().parents[4]
EVIDENCE = Path(__file__).resolve().parent
STAGED = ROOT / ".local/etf-execution-contract-20260930/inputs"


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    checks = []

    def check(name, condition):
        if not condition:
            raise ValueError(name)
        checks.append({"name": name, "passed": True})

    def rejected(name, exception, operation):
        try:
            operation()
        except exception as error:
            checks.append({"name": name, "passed": True, "rejection": str(error)[:400]})
        else:
            raise ValueError("Expected rejection: " + name)

    review = read(EVIDENCE / "review-package.json")
    package = review["package"]
    policy = read(EVIDENCE / "data-policy-draft.json")
    program = ResearchProgram.model_validate(read(EVIDENCE / "program-schema-preview.json"))
    public = AdaptiveExecutionProfile.model_validate(read(EVIDENCE / "public-profile-draft.json"))
    check("review_package_digest", fingerprint(package) == review["review_digest"])
    check("all_package_file_hashes", all(sha(EVIDENCE / name) == expected
                                        for name, expected in package["files"].items()))
    check("policy_canonical_digest", fingerprint(policy) == package["policy_digest"])
    check("program_schema_preview_digest", fingerprint(program.model_dump(mode="json"))
          == package["program_schema_preview_digest"])
    check("public_profile_digest", digest_model(public) == package["profile_digest"])
    for name in ("raw_manifest", "lineage_receipt", "availability_survey", "official_source_review"):
        ref = policy[name]
        check(name + "_hash", sha(ROOT / ref["path"]) == ref["sha256"])
    check("staged_input_hashes", {name: sha(STAGED / name) for name in package["input_files"]}
          == package["input_files"] == policy["input_files"])
    old_inputs = ROOT / ".local/science-inputs/kr_etf-prepared-v2"
    check("approved_inputs_preserved", {name: sha(old_inputs / name)
                                        for name in package["active_input_files_unchanged"]}
          == package["active_input_files_unchanged"])
    check("protected_engine_preserved", sha(ROOT / "src/quant_company/research/reference/domestic_engine.py")
          == policy["protected_engine_sha256"] == public.entrypoint_sha256)
    check("draft_has_no_authority", package["activation"] is False and package["owner_approval"] is None
          and package["current_runtime_admissible"] is False
          and package["production_profiles_registered"] is False)
    check("PIT_unknown_remains_visible", policy["historical_point_in_time_verified"] is False
          and policy["source_publication_time"] is None and policy["historical_revision_history"] is None)
    check("legacy_numeric_receipt_cannot_admit_draft", read(STAGED / "receipt.json")["state"] != "ready")
    spec = MissionSpec.model_validate(program.envelopes[0].template.model_dump(mode="json"))
    check("exact_new_input_and_profile_bindings", spec.data.input_files == package["input_files"]
          and spec.execution_profile == public.id and spec.execution_profile_digest == digest_model(public))
    check("bounded_development_only", program.max_missions == 1 and program.max_total_trials == 4
          and program.max_parallel_missions == 1 and program.max_compute_seconds == 7200
          and spec.search.continuous is False and spec.development.end.isoformat() == "2025-12-30"
          and "development.json" not in public.qualification_input_names)

    assessment = {"decision": "ready", "rationale": "Current schema rejection probe, no production assessment.",
                  "source_ids": program.source_ids, "point_in_time": False, "coverage": True,
                  "executable_prices": True, "original_conditions": False}
    rejected("current_ready_rejects_unverified_PIT", ValidationError,
             lambda: DataAssessment.model_validate(assessment))
    rejected("current_schema_rejects_conditional_ready", ValidationError,
             lambda: DataAssessment.model_validate(dict(assessment, decision="conditional_ready")))
    rejected("program_policy_extension_requires_new_schema", ValidationError,
             lambda: ResearchProgram.model_validate(dict(program.model_dump(mode="json"), data_policy=policy)))
    rejected("mission_policy_extension_requires_new_schema", ValidationError,
             lambda: MissionSpec.model_validate(dict(spec.model_dump(mode="json"), data_policy=policy)))
    # original_conditions is specifically required by ProgramStore for exact replication;
    # the common model validator alone does not require it for novel hypotheses.
    check("common_model_does_not_require_original_conditions", DataAssessment.model_validate(
        dict(assessment, point_in_time=True)).original_conditions is False)
    registry = ROOT / "docs/project/evidence/research-programs-20260928/server-profile-registry-candidate.json"
    company = SimpleNamespace(settings=SimpleNamespace(research_profiles_file=registry, fixture_mode=False))
    rejected("old_registry_does_not_authorize_new_profile", PolicyError, lambda: profile_for(company, spec))
    worker = read(ROOT / ".local/real-profiles/kr_etf/worker-profile.json")
    server = read(ROOT / ".local/real-profiles/kr_etf/server-profile.json")
    destination = ROOT / ".local/etf-execution-contract-20260930/rejection-probe-not-created"
    args = {
        "source_bundle": Path(server["source_bundle"]), "source_sha256": server["source_bundle_sha256"],
        "base_commit": server["base_commit"], "destination": destination, "runtime": worker["runtime"],
        "input_sources": {name: STAGED / name for name in package["input_files"]}, "market": "kr_etf",
        "qualification_seconds": 120, "evaluation_seconds": 900, "fixture_only": False,
    }
    rejected("current_factory_rejects_nonready_quality_receipt", ValueError,
             lambda: prepare_domestic_profile(**args))
    rejected("current_factory_requires_versioned_profile_change", ValueError,
             lambda: prepare_domestic_profile(**dict(args, runtime=dict(worker["runtime"], profile_id=public.id))))
    check("factory_rejection_created_no_workspace", not destination.exists())
    modules = ("program_contracts.py", "programs.py", "mission_contracts.py", "domestic_profile.py", "builds.py")
    result = {
        "schema_version": 1, "observed_at": datetime.now(UTC).isoformat(), "review_digest": review["review_digest"],
        "script_sha256": sha(Path(__file__)), "checks": checks, "all_passed": True,
        "current_gate_source_hashes": {name: sha(ROOT / "src/quant_company/research" / name) for name in modules},
        "scope": "local_draft_bindings_and_actual_current_schema_factory_rejections",
        "production_database_or_Temporal_test": False, "signed_Slack_test": False,
        "future_policy_implementation_test": False, "runtime_qualification": False,
        "price_performance_evaluation": False, "sealed_price_rows_read": False,
        "production_mutated": False, "readiness_granted": False, "scientific_trials_added": 0,
    }
    with (EVIDENCE / "validation.json").open("x") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"checks_passed": len(checks), "review_digest": review["review_digest"],
                      "current_runtime_admissible": False}))


if __name__ == "__main__":
    main()
