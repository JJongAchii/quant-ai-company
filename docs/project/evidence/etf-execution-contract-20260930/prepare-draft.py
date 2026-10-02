"""Freeze a review package; do not register a profile, approve data, or run a candidate."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from quant_company.company import fingerprint
from quant_company.research.adaptive_contracts import AdaptiveExecutionProfile, digest_model
from quant_company.research.program_contracts import ResearchProgram

ROOT = Path(__file__).resolve().parents[4]
EVIDENCE = Path(__file__).resolve().parent
PRIOR = ROOT / "docs/project/evidence/etf-raw-lineage-20260930"
STAGED = ROOT / ".local/etf-execution-contract-20260930/inputs"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    with path.open("x") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")


def main():
    lineage = read(PRIOR / "raw-lineage-receipt.json")
    manifest = read(PRIOR / "raw-body-manifest.json")
    old = read(ROOT / "docs/project/evidence/research-programs-20260928/first-program-draft.json")
    server = read(ROOT / ".local/real-profiles/kr_etf/server-profile.json")
    official = EVIDENCE / "official-source-review.json"
    survey = EVIDENCE / "availability-survey.json"
    if len(manifest["objects"]) != 835 or manifest["failures"]:
        raise ValueError("Incomplete prior raw-body evidence")
    if sha(PRIOR / "raw-body-manifest.json") != lineage["raw_bodies"]["body_manifest_sha256"]:
        raise ValueError("Prior raw-body manifest changed")
    expected = lineage["generated_input_comparison"]["audit_only_input_files"]
    original = lineage["approved_input_files"]
    source = ROOT / ".local/etf-raw-lineage-20260930/run-02/reconstructed-audit-inputs"
    contents = {name: (source / name).read_bytes() for name in expected}
    if {name: hashlib.sha256(body).hexdigest() for name, body in contents.items()} != expected:
        raise ValueError("Reconstructed inputs changed")
    active = ROOT / ".local/science-inputs/kr_etf-prepared-v2"
    if {name: sha(active / name) for name in original} != original:
        raise ValueError("Approved inputs changed")
    engine = ROOT / "src/quant_company/research/reference/domestic_engine.py"
    if sha(engine) != lineage["frozen_engine_sha256"]:
        raise ValueError("Protected evaluator changed")
    STAGED.mkdir(parents=True, exist_ok=False)
    for name, body in contents.items():
        with (STAGED / name).open("xb") as stream:
            stream.write(body)
    write(STAGED / "receipt.json", {
        "state": "review_draft_not_ready", "market": "kr_etf", "input_files": expected,
        "quality": lineage["generator_quality"], "historical_point_in_time_verified": False,
        "current_runtime_admissible": False, "activated": False,
    })

    public = dict(server["public_profile"], id="kr-etf-retrospective-v1")
    public = AdaptiveExecutionProfile.model_validate(public)
    write(EVIDENCE / "public-profile-draft.json", public.model_dump(mode="json"))
    template = dict(old["program"]["envelopes"][0]["template"])
    template.update(
        title="고정 빈티지 ETF의 조건부 개발 연구", execution_profile=public.id,
        execution_profile_digest=digest_model(public),
        data={"lake_id": "krx-etf-frozen-vintage-20260930", "input_files": expected},
        search=dict(template["search"], continuous=False),
    )
    policy = {
        "schema_version": 1, "id": "kr-etf-frozen-vintage-retrospective-v1",
        "proposed_mode": "frozen_vintage_retrospective",
        "allowed_task_modes": ["novel_hypothesis"],
        "required_result_scope": "conditional_retrospective_development",
        "historical_point_in_time_verified": False,
        "historical_availability_assumption": "trade_day_T23:59:00+09:00",
        "source_publication_time": None, "historical_revision_history": None,
        "current_bytes_observed_start_utc": min(o["read_started_at"] for o in manifest["objects"]),
        "current_bytes_observed_end_utc": max(o["read_completed_at"] for o in manifest["objects"]),
        "s3_last_modified_role": "mirror_object_upload_metadata_not_source_publication_time",
        "unverified_assumptions": [
            "Historical values were available by the synthetic trade-day 23:59 KST cutoff.",
            "Later revisions to FLUC_RT, raw prices, liquidity and ETF classifications are immaterial.",
            "Observed daily opens and the reference-adjusted series represent the stated theoretical estimand.",
        ],
        "permitted_claim": "Conditional behavior on these frozen bytes under the stated assumptions only.",
        "excluded_claims": [
            "historically_tradable_alpha", "verified_historical_point_in_time",
            "exact_original_replication", "cash_distribution_reinvested_total_return",
            "live_fill_feasibility", "paper_or_live_deployment_readiness", "untouched_2026_confirmation",
        ],
        "execution_price_contract": {
            "signal_cutoff": "trade_day_09:00_KST; history date < trade day and available_at < cutoff",
            "history_scale": "per ticker rebased to its last visible raw close",
            "adjusted_open": "open * adj_close / close",
            "return": "next observed session adjusted_open / current adjusted_open - 1",
            "weights": "long-only; gross <= 1; residual cash return zero",
            "turnover": "sum(abs(proposed_weight - pretrade_weight)) including sells",
            "cost": "(1 - turnover * bps / 10000) * gross_return_factor - 1",
            "base_cost_bps": 10, "stress_cost_bps": 30,
            "events": "missing held prices or nontradable current/next rows abort; no event settlement adapter",
            "terminal_session": "2025-12-30 supplies the last valuation open; no signal on that session",
            "initial_observation": "2023-01-02 zero; elapsed-calendar-day CAGR",
            "terminal_liquidation_cost": "not charged by the frozen engine; no extra liquidation claim",
        },
        "universe_contract": {
            "selection_cutoff": "2022-12-29", "selection_sessions": 20,
            "selection_basis": "mean raw trading value; complete valid rows; nonleveraged noninverse metadata",
            "selected_tickers": lineage["selection_prefix_check"]["selected_tickers"],
            "selection_prefix_after_cutoff_objects": 0,
            "metadata_availability": "frozen historical classification; publication/revision vintage unverified",
            "reselection_during_development": False,
        },
        "warmup": {"start": "2022-08-01", "end": "2022-12-29", "sessions": 104, "rows": 1040},
        "development": {"start": "2023-01-02", "end": "2025-12-30", "sessions": 731, "rows": 7310},
        "sealed": template["sealed"],
        "sealed_status": "price rows not read by this work; earlier P11 aggregate results known to staff",
        "qualification_inputs": ["warmup.json"], "evaluation_inputs": sorted(expected),
        "input_files": expected,
        "raw_manifest": {"path": str((PRIOR / "raw-body-manifest.json").relative_to(ROOT)),
                         "sha256": sha(PRIOR / "raw-body-manifest.json"), "objects": 835},
        "lineage_receipt": {"path": str((PRIOR / "raw-lineage-receipt.json").relative_to(ROOT)),
                            "sha256": sha(PRIOR / "raw-lineage-receipt.json")},
        "availability_survey": {"path": str(survey.relative_to(ROOT)), "sha256": sha(survey)},
        "official_source_review": {"path": str(official.relative_to(ROOT)), "sha256": sha(official)},
        "transform": {
            "qdata_commit": lineage["qdata_commit"], "verified_package_files": 47,
            "builder_sha256": lineage["pinned_builder_sha256"],
            "numeric_normalizer_sha256": lineage["pinned_numeric_normalizer_sha256"],
            "generator_sha256": lineage["generator_sha256"],
            "bounded_clean_files": {
                name: record["sha256"] for name, record in lineage["reconstructed_clean"]["approved_window"].items()
            },
        },
        "protected_engine_sha256": sha(engine),
        "required_future_admission_checks": [
            "Signed new program binds the typed policy, exact inputs, mode, profile and economic contract.",
            "Independent data assessment records PIT unverified and conditional admission as distinct facts.",
            "Only historical publication/revision absence and old-preparation identity are explicit scope limits.",
            "All other numerical, coverage, provenance, price-estimand and causal-code gaps remain hard blockers.",
            "No existing blocked packet, PIT flag or signed program is rewritten to ready.",
            "Independent director accepts a sourced falsifiable question; duplicate candidates share trial history.",
            "Qualified producer-consumer run checks hashes, prefix causality, costs and result scope without sealed data.",
            "Every result, audit, report and adoption decision retains the conditional result scope.",
        ],
    }
    write(EVIDENCE / "data-policy-draft.json", policy)
    program = ResearchProgram.model_validate({
        "schema_version": 1, "title": "ETF 고정 빈티지 조건부 연구 — 구조 검토용 초안",
        "objective": "조건부 개발 연구. data-policy-draft.json SHA256=" + sha(EVIDENCE / "data-policy-draft.json")
                     + ". 현행 schema 1은 정책을 강제하지 않으므로 이 초안의 제출·승인·실행은 금지된다.",
        "envelopes": [{"name": "etf_retrospective_strategy", "market": "kr_etf", "template": template}],
        "max_total_trials": 4, "max_compute_seconds": 7200, "max_missions": 1,
        "max_parallel_missions": 1, "source_ids": old["program"]["source_ids"], "include_quant_feed": False,
    })
    write(EVIDENCE / "program-schema-preview.json", program.model_dump(mode="json"))
    files = {name: sha(EVIDENCE / name) for name in
             ("data-policy-draft.json", "program-schema-preview.json", "public-profile-draft.json")}
    package = {
        "schema_version": 1, "state": "implementation_and_new_signed_approval_required",
        "files": files, "input_files": expected, "active_input_files_unchanged": original,
        "policy_digest": fingerprint(policy), "program_schema_preview_digest": fingerprint(program.model_dump(mode="json")),
        "profile_digest": digest_model(public),
        "current_runtime_admissible": False, "production_profiles_registered": False,
        "owner_approval": None, "activation": False,
        "policy_binding_in_current_schema": "text_reference_only_not_enforced",
        "implementation_gates": [
            "Versioned typed policy in program/envelope, mission, assessment and result; existing PIT path unchanged.",
            "Assess/decide/admission enforce a distinct conditional path, exact mode/digest and scoped gap codes.",
            "Profile preparation supports new distinct ID and verified policy/quality receipts; register only after E2E.",
            "Audits and reports cannot promote conditional evidence to historical/live or exact replication claims.",
            "Real PostgreSQL/Temporal and isolated worker E2E prove approval, recovery, budget and scope propagation.",
            "Fresh signed owner approval covers the implemented canonical program, not this schema preview.",
        ],
        "future_prospective_receipt_requirements": [
            "Actual request start/end and source-observed UTC/KST times; endpoint and collector code/version.",
            "Append-only byte-hashed response and per-date revision identities; preserve every observed replacement.",
            "As-known-at query selects only versions actually observed before the signal cutoff.",
            "Do not backdate receipts or use current policy/ETag/filemtime as historical publication evidence.",
        ],
        "scientific_trials_added": 0, "strategy_performance_computed": False, "production_mutated": False,
    }
    write(EVIDENCE / "review-package.json", {"package": package, "review_digest": fingerprint(package)})
    write(EVIDENCE / "preparation-receipt.json", {
        "observed_at": datetime.now(UTC).isoformat(), "script_sha256": sha(Path(__file__)),
        "staged_input_directory": str(STAGED.relative_to(ROOT)),
        "staged_input_files": {name: sha(STAGED / name) for name in expected},
        "numeric_receipt_state": "review_draft_not_ready", "original_inputs_preserved": True,
        "source_bundle_sha256": sha(Path(server["source_bundle"])),
        "source_bundle_matches_original_profile": sha(Path(server["source_bundle"])) == server["source_bundle_sha256"],
        "protected_engine_sha256": sha(engine), "readiness_granted": False,
        "activated": False, "performance_computed": False, "scientific_trials_added": 0,
    })
    print(json.dumps({"review_digest": fingerprint(package), "state": package["state"], "activated": False}))


if __name__ == "__main__":
    main()
