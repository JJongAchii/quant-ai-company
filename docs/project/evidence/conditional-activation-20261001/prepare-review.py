"""Prepare canonical review bytes and local profiles; grants no research authority."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from quant_company.company import fingerprint, stable
from quant_company.research.data_evidence import DataEvidenceGap, DataEvidencePacket, EvidenceFile
from quant_company.research.domestic_profile import prepare_domestic_profile
from quant_company.research.policy_contracts import ResearchDataPolicy
from quant_company.research.program_contracts import ResearchProgram

ROOT = Path(__file__).resolve().parents[4]
EVIDENCE = Path(__file__).resolve().parent
LOCAL = ROOT / ".local/conditional-activation-20261001"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    with path.open("x") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")


def main():
    baseline = read(EVIDENCE / "server-baseline.json")
    project = baseline["database"]["project"]
    programs = baseline["database"]["programs"]
    assert len(programs) == 1 and programs[0]["state"] == "active"
    old = programs[0]
    assert not baseline["database"]["missions"] and not baseline["database"]["usage"]
    assert not baseline["database"]["activity"]["active_jobs"]
    quality = LOCAL / "inputs/receipt.json"
    assert read(quality)["scope"] == "numeric_quality_only_for_frozen_vintage_retrospective_policy"
    draft = ROOT / "docs/project/evidence/etf-execution-contract-20260930"
    original_policy = read(draft / "data-policy-draft.json")
    sources = [quality, draft / "data-policy-draft.json", ROOT / original_policy["lineage_receipt"]["path"],
               ROOT / original_policy["raw_manifest"]["path"], draft / "availability-survey.json",
               draft / "official-source-review.json"]
    reports = EVIDENCE / "source-reports"
    reports.mkdir(exist_ok=False)
    refs = []
    for source in sources:
        assert source.is_file() and not source.is_symlink() and source.stat().st_size < 1_000_000
        name = source.stem + "-" + sha(source)[:12] + ".json"
        with (reports / name).open("xb") as stream:
            stream.write(source.read_bytes())
        refs.append({"name": name, "sha256": sha(source)})
    gaps = ["historical_publication_unverified", "historical_revision_vintage_unverified",
            "original_preparation_bytes_unavailable"]
    policy = ResearchDataPolicy(mode="frozen_vintage_retrospective", input_files=original_policy["input_files"],
        evidence_refs=refs, availability_status="unverified_historical",
        assumed_available_at_rule="trade_day_T23:59:00+09:00",
        result_scope="conditional_retrospective_development", acknowledged_gap_codes=gaps)
    write(EVIDENCE / "data-policy.json", policy.model_dump(mode="json"))
    original_server = read(ROOT / ".local/real-profiles/kr_etf/server-profile.json")
    runtime = read(ROOT / ".local/real-profiles/kr_etf/worker-profile.json")["runtime"]
    runtime["profile_id"] = "kr-etf-retrospective-v1"
    profile_root = LOCAL / "profile"
    identity = prepare_domestic_profile(
        source_bundle=Path(original_server["source_bundle"]),
        source_sha256=original_server["source_bundle_sha256"], base_commit=original_server["base_commit"],
        destination=profile_root, runtime=runtime,
        input_sources={name: LOCAL / "inputs" / name for name in policy.input_files}, market="kr_etf",
        qualification_seconds=120, evaluation_seconds=900, data_policy=policy,
        policy_files={ref.name: reports / ref.name for ref in policy.evidence_refs},
    )
    assert identity["base_commit"] == original_server["base_commit"] and not identity["qualified"]
    public = read(profile_root / "server-profile.json")["public_profile"]
    write(EVIDENCE / "profile-identity.json", identity)
    write(EVIDENCE / "public-profile.json", public)
    lineage_id = stable(f"scientific-lineage:{project['id']}:kr-etf-069500-gmm-development")
    tasks = baseline["database"]["tasks"]
    assert len(tasks) == 10 and all(task["proposal"]["envelope"] == "etf_strategy" for task in tasks)
    assert all(task["mission_id"] is None and fingerprint(task["proposal"]) == task["digest"] for task in tasks)
    origins = [{"program_id": task["program_id"], "task_id": task["id"], "task_digest": task["digest"]}
               for task in sorted(tasks, key=lambda task: task["id"])]
    history = {"scientific_lineage_id": str(lineage_id), "project_id": project["id"],
               "trial_limit": None, "origins": origins, "trials": []}
    write(EVIDENCE / "lineage-preimage-preview.json", {
        "history": history, "history_digest": fingerprint(history),
        "basis": "actual_read_only_database_baseline; must be rechecked by the deployed history() function",
        "owner_authorized": False,
    })
    candidate = ResearchProgram.model_validate(old["spec"]).model_dump(mode="json")
    template = next(e["template"] for e in candidate["envelopes"] if e["name"] == "etf_strategy")
    template.update(schema_version=3, title="고정 빈티지 ETF의 조건부 개발 연구",
        data={"lake_id": "krx-etf-frozen-vintage-20260930", "input_files": policy.input_files},
        data_policy=policy.model_dump(mode="json"),
        scientific_lineage={"id": str(lineage_id), "max_total_trials": 4,
            "history_digest": fingerprint(history), "originating_task_refs": origins},
        execution_profile="kr-etf-retrospective-v1", execution_profile_digest=identity["execution_profile_digest"],
        search=dict(template["search"], continuous=False, max_total_trials=4, max_trials_per_cycle=2),
    )
    candidate.update(schema_version=2, title="고정 빈티지 ETF 조건부 개발 연구 — 기존 10개 과제 이력 포함",
        objective="현재 고정 자료와 명시한 공개시각 가정에 조건부인 ETF 개발 가설을 검토한다. "
                  "기존 GMM 노출조절 후보의 중복 제안·차단 이력을 상속한다. "
                  "최대 1미션·첫 회차 2결과만 진행하며, 과거 PIT·실제 체결·확증·운영 배치를 주장하지 않는다.",
        envelopes=[{"name": "etf_strategy", "market": "kr_etf", "template": template}],
        max_total_trials=4, max_compute_seconds=7200, max_missions=1, max_parallel_missions=1,
    )
    program = ResearchProgram.model_validate(candidate)
    digest = fingerprint(program.model_dump(mode="json"))
    write(EVIDENCE / "candidate-program.json", program.model_dump(mode="json"))
    managed = Path("/state/research/provisioned/data-evidence/conditional-20261001")
    report_refs = {ref.name: EvidenceFile(path=managed / ref.name, sha256=ref.sha256) for ref in policy.evidence_refs}
    input_refs = {name: EvidenceFile(path=managed / name, sha256=expected) for name, expected in policy.input_files.items()}
    cited = [ref.name for ref in policy.evidence_refs if ref.name.startswith(("data-policy-draft-", "availability-survey-", "raw-lineage-receipt-"))]
    descriptions = {
        "historical_publication_unverified": "Actual historical publication/collection timestamps are unavailable; same-day 23:59 KST is an assumption.",
        "historical_revision_vintage_unverified": "Historical revision vintages are unavailable; the current frozen reference-price chain is the declared estimand.",
        "original_preparation_bytes_unavailable": "Original preparation clean bytes and absolute adjustment anchor were not recovered; these are new independently checked input hashes.",
    }
    packet = DataEvidencePacket(schema_version=2, program_digest=digest, envelope="etf_strategy",
        research_scope=program.envelopes[0].template.research_scope,
        execution_profile_digest=identity["execution_profile_digest"],
        lake_id=template["data"]["lake_id"], input_files=input_refs,
        engine=EvidenceFile(path=managed / "engine.py", sha256=original_policy["protected_engine_sha256"]),
        reports=report_refs, blocking_gaps=[],
        gaps=[DataEvidenceGap(code=code, description=descriptions[code], report_names=cited,
                              input_files=policy.input_files) for code in gaps],
    )
    assert packet.execution_profile_digest == identity["execution_profile_digest"]
    write(EVIDENCE / "candidate-evidence-packet.json", packet.model_dump(mode="json"))
    write(EVIDENCE / "review-package.json", {
        "schema_version": 1, "state": "prepared_not_submitted_or_approved", "observed_at": datetime.now(UTC).isoformat(),
        "source_commit": "c113244b3af96d3a18b18f26761a0e7a432b8382", "project_id": project["id"],
        "revision": project["revision"], "old_program_id": old["id"], "old_program_digest": old["manifest_digest"],
        "proposed_program_id": str(stable(f"program:{project['id']}:{project['revision']}:{digest}")),
        "program_digest": digest, "data_policy_digest": fingerprint(policy.model_dump(mode="json")),
        "profile_digest": identity["execution_profile_digest"], "lineage_history_digest": fingerprint(history),
        "origin_task_count": len(origins), "prior_scientific_trials": 0, "prior_compute_seconds": 0,
        "program_file_sha256": sha(EVIDENCE / "candidate-program.json"),
        "packet_file_sha256": sha(EVIDENCE / "candidate-evidence-packet.json"),
        "packet_digest": fingerprint(packet.model_dump(mode="json")),
        "input_files": policy.input_files, "reports": {ref.name: ref.sha256 for ref in policy.evidence_refs},
        "operating_registry_sha256": baseline["registry_sha256"],
        "approval_sequence": ["healthy compatible release", "actual database history preimage check",
            "owner cancels prior program", "fresh signed canonical program approval",
            "actual independent data assessment", "actual director task selection"],
        "old_signature_reusable": False, "staff_data_decision": "not_made", "owner_approval": None,
        "production_mutated": False, "sealed_price_rows_read": False, "performance_computed": False,
        "scientific_trials_added": 0,
    })
    print(json.dumps({"program_digest": digest, "origins": len(origins), "policy_reports": len(refs),
                      "profile": identity["execution_profile"], "state": "prepared_not_submitted_or_approved"}))


if __name__ == "__main__":
    main()
