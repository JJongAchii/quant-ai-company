"""Freeze a review proposal from verified operator receipts, without submitting or executing it."""

import copy
import hashlib
import json
from pathlib import Path

from quant_company.company import fingerprint, stable
from quant_company.research.adaptive_contracts import digest_model
from quant_company.research.data_evidence import DataEvidencePacket
from quant_company.research.mission_contracts import RetrospectiveDataPolicy
from quant_company.research.program_contracts import ResearchProgram

ROOT = Path(__file__).parent
PROOF = ROOT.parent / "etf-data-resolution-20260930" / "approved-history-readback.json"
ENGINE = "1984bf6b7fa5997a8b7ba446061ff663bdf78c683776be945da9130a067480cc"


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def write(name, content):
    path = ROOT / name
    if path.exists() and path.read_bytes() != content:
        raise ValueError("Review artifact already exists with different bytes: " + name)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def main():
    baseline = json.loads((ROOT / "preparation-baseline.json").read_bytes())
    old = ResearchProgram.model_validate(baseline["program"]["spec"])
    if fingerprint(old.model_dump(mode="json")) != baseline["program"]["manifest_digest"]:
        raise ValueError("Existing signed program bytes changed")
    if baseline["mission_count"] or any(baseline["usage"].values()):
        raise ValueError("Budget changed; prepare a new remaining-budget specification for review")
    packet = copy.deepcopy(baseline["packet"])
    if packet["engine"]["sha256"] != ENGINE:
        raise ValueError("Protected engine changed")
    proof_bytes = PROOF.read_bytes()
    proof_sha = hashlib.sha256(proof_bytes).hexdigest()
    history_name = "history-proof-" + proof_sha[:12] + ".json"
    history = json.loads(proof_bytes)
    if history["performance_computed"] or history["sealed_price_rows_returned_or_used"]:
        raise ValueError("History receipt exceeded preparation scope")
    raw = json.loads((ROOT / "source-reports" / "raw-lineage-f8a0595989be.json").read_bytes())
    if (raw["approved_input_files"] != {name: item["sha256"] for name, item in packet["input_files"].items()}
            or not raw["selection_prefix_check"]["approved_cohort_matches"]
            or raw["generated_input_comparison"]["adjusted_curve_shape"]["mismatches"]):
        raise ValueError("Raw reconstruction does not cover the frozen inputs")
    assumptions = {
        "schema_version": 1,
        "scope": "owner_review_proposal_for_retrospective_etf_strategy_exploration",
        "baseline_observed_at": baseline["observed_at"],
        "replaces_program_digest": baseline["program"]["manifest_digest"],
        "data_readiness_decision": "not_made",
        "historical_point_in_time_verified": False,
        "historical_revision_vintages_verified": False,
        "preparation_time_source_bytes_recovered": False,
        "availability_assumption": "trade date at 23:59:00+09:00; generated field, not an observed publication timestamp",
        "source_identity": "frozen approved JSON inputs plus reconstructed relative price curve; original clean bytes and absolute adjustment anchor remain unavailable",
        "return_convention": "KRX reference-price FLUC_RT chain, rebased to latest known raw close; theoretical adjusted open to next adjusted open, cash return zero",
        "result_use": "hypothesis generation only; ineligible for confirmation, portfolio adoption or paper/live deployment",
        "permitted_task_modes": ["novel_hypothesis", "market_transfer"],
        "market": "kr_etf", "kind": "strategy",
        "cohort": raw["selection_prefix_check"]["selected_tickers"],
        "development": {"start": "2023-01-02", "end": "2025-12-30"},
        "warmup": {"start": "2022-08-01", "end": "2022-12-29"},
        "sealed": {"start": "2026-01-02", "end": "2026-09-23", "access": "prohibited"},
        "prices": "positive frozen daily evaluation prices; actual fills, trading-halt liquidity, delisting settlement and cash distribution reinvestment are not certified",
        "signal_cutoff": "09:00 KST; history dates strictly earlier and available_at strictly before cutoff under the declared assumption",
        "blocking_gap_dispositions": [
            {"prior_gap": "original S3 versions/preparation clean bytes", "disposition": "unresolved, expressly acknowledged for retrospective hypotheses only"},
            {"prior_gap": "historical publication and revision timestamps", "disposition": "unresolved, generated availability is an explicit assumption"},
            {"prior_gap": "adj_close level differences", "disposition": "relative curve and actual historical rebasing reproduced; original absolute anchor still unverified"},
            {"prior_gap": "live fills and cash distributions", "disposition": "theoretical open-to-next-open reference-price evaluation only; tasks requiring live fills or cash-reinvested total return must block"},
            {"prior_gap": "old 08:30/same-day-close request", "disposition": "not within this new scope; proposals must follow the unchanged 09:00 historical cutoff and approved evaluator"},
        ],
        "required_gates": ["separate signed owner program approval", "current original citations and complete data evidence reads",
                           "independent coverage and evaluation-price assessment", "director selection within remaining budgets",
                           "protected engine/input/profile identity", "code causality and no sealed/future access",
                           "independent artifact-bound validity audit", "independent interpretation (inconclusive or not_supported)"],
        "operator_packet_is_not_a_readiness_verdict": True,
        "production_mutated": False, "scientific_trials_added": 0,
    }
    scope_bytes = encoded(assumptions)
    scope_sha = hashlib.sha256(scope_bytes).hexdigest()
    scope_name = "exploration-assumptions-" + scope_sha[:12] + ".json"
    write("source-reports/" + history_name, proof_bytes)
    write("source-reports/" + scope_name, scope_bytes)
    for name, sha in ((history_name, proof_sha), (scope_name, scope_sha)):
        packet["reports"][name] = {"path": "/state/research/provisioned/data-evidence/" + name, "sha256": sha}
    reviewed_names = ["input-check.json", "raw-lineage-f8a0595989be.json", history_name, scope_name]
    policy = RetrospectiveDataPolicy(evidence_reports={name: packet["reports"][name]["sha256"] for name in reviewed_names})
    program = old.model_dump(mode="json")
    program.update(title="한계를 명시한 ETF 탐색 연구 (2023~2025)",
                   objective="당시 공개 시각·수정 이력이 미확인인 고정 ETF 자료의 가정하에서 가설을 탐색한다. 결과는 가설 생성에만 사용하며 확증·운영 승격의 근거로 인정하지 않는다.")
    program["envelopes"] = [copy.deepcopy(next(e for e in program["envelopes"] if e["name"] == "etf_strategy"))]
    program["envelopes"][0]["template"]["title"] = "KR ETF retrospective strategy hypothesis generation"
    program["envelopes"][0]["template"]["data"]["policy"] = policy.model_dump(mode="json")
    candidate = ResearchProgram.model_validate(program)
    digest = fingerprint(candidate.model_dump(mode="json"))
    packet.update(program_digest=digest, data_policy=policy.model_dump(mode="json"), blocking_gaps=[])
    packet = DataEvidencePacket.model_validate(packet)
    spec_sha = write("candidate-program.json", encoded(candidate.model_dump(mode="json")))
    packet_sha = write("candidate-evidence-packet.json", encoded(packet.model_dump(mode="json")))
    review = {
        "schema_version": 1, "state": "prepared_not_submitted_or_approved",
        "baseline_observed_at": baseline["observed_at"], "project_id": baseline["program"]["project_id"],
        "revision": baseline["program"]["revision"],
        "old_program_id": baseline["program"]["id"], "old_program_digest": baseline["program"]["manifest_digest"],
        "proposed_program_id": str(stable(f"program:{baseline['program']['project_id']}:{baseline['program']['revision']}:{digest}")),
        "program_digest": digest, "data_policy_digest": digest_model(policy),
        "spec_file_sha256": spec_sha, "packet_file_sha256": packet_sha,
        "packet_digest": fingerprint(packet.model_dump(mode="json")),
        "baseline_registry_sha256": baseline["registry_sha256"],
        "reports": {name: entry.model_dump(mode="json") for name, entry in packet.reports.items()},
        "limits": {key: getattr(candidate, key) for key in ("max_total_trials", "max_compute_seconds", "max_missions", "max_parallel_missions")},
        "budget_basis": "old program zero used/reserved; cancel its authority before signing the replacement; abort cutover if usage or revision drifts",
        "old_signature_reusable": False, "data_readiness_decision": "not_made",
        "production_mutated": False, "scientific_trials_added": 0,
    }
    write("review-package.json", encoded(review))
    print(json.dumps({"state": review["state"], "program_digest": digest,
                      "data_policy_digest": review["data_policy_digest"], "reports": len(packet.reports)}))


if __name__ == "__main__":
    main()
