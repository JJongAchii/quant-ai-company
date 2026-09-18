"""Version-scoped practice evidence and bounded curriculum selection; never an expertise score."""

from .cases import FAMILIES, SUITE_VERSION
from .packs import pack

RECHECK_PASSES = 3

# These are explicitly unmeasured requirements, not claims inferred from two synthetic families.
UNMEASURED = {
    "director": ["복합 요청 완결성", "위임 결과의 근거 검토", "실제 업무 우선순위 판단"],
    "financial_strategist": ["거시 인과·반례", "공시·회계 심층 분석", "최신 원문 검증", "설명의 정확성"],
    "data": ["실제 데이터의 커버리지·조인", "기업행사·PIT 유니버스", "장기 품질 복구"],
    "researcher_kr": ["한국 시장 가설·반례", "실제 연구 재현", "경제성·일반화"],
    "researcher_global": ["국가 간 제도·시장 구조", "실제 연구 재현", "경제성·일반화"],
    "researcher_crypto": ["체결·유동성·거래소 위험", "실제 이벤트 데이터 검증", "경제성·일반화"],
    "engineer": ["실제 코드 결함 재현·수정", "독립 재실행", "성능·수치 안정성"],
    "validator": ["실제 코드·산출물 감사", "통계적 반증", "전문가 판정과 일치도"],
    "risk": ["포트폴리오 공통 노출", "비선형 손익·유동성", "실제 위험 한도 검증"],
    "operations": ["실제 복원·대사", "장애 대응", "장기 가용성"],
    "maintainer": ["실제 원인 분석", "수정의 새 사례 개선 효과", "정상 업무 회귀 방지"],
}


def history(conn, owner, employee):
    return conn.execute("""SELECT id::text,model,role_snapshot->>'reasoning_effort' AS reasoning_effort,
        pack_snapshot->>'digest' AS pack_digest,suite_version,
        public_case->>'family' AS family,case_digest,state,grade,created_at::text
        FROM staff_runs WHERE owner_user=%s AND employee=%s
        ORDER BY created_at DESC,id DESC LIMIT 200""", (owner, employee)).fetchall()


def profile(rows, employee, model, pack_digest, reasoning_effort=None):
    current = [r for r in rows if r["model"] == model and r["pack_digest"] == pack_digest
               and r.get("reasoning_effort") == reasoning_effort
               and r["suite_version"] == SUITE_VERSION]
    families = []
    for family in FAMILIES[employee]:
        matching = [r for r in current if r["family"] == family]
        completed = [r for r in matching if r["state"] == "completed" and r["grade"]]
        streak, seen = 0, set()
        for row in completed:
            if not row["grade"].get("objective_passed"):
                break
            if row["case_digest"] not in seen:
                streak += 1
                seen.add(row["case_digest"])
        failures = [r for r in completed if not r["grade"].get("objective_passed")]
        if not completed:
            state = "unassessed"
        elif streak >= RECHECK_PASSES:
            state = "repeated_objective_checks"
        elif failures:
            state = "needs_recheck"
        else:
            state = "limited_objective_evidence"
        families.append({"family": family, "state": state, "completed": len(completed),
                         "consecutive_fresh_passes": streak, "latest_run_id": completed[0]["id"] if completed else None,
                         "latest_failure_id": failures[0]["id"] if failures else None,
                         "weaknesses": failures[0]["grade"].get("weaknesses", []) if failures else [],
                         "blocked": sum(r["state"] == "blocked" for r in matching),
                         "disputed": sum(r["state"] == "disputed" for r in matching)})
    return {"employee": employee, "model": model, "reasoning_effort": reasoning_effort,
            "pack_digest": pack_digest, "suite_version": SUITE_VERSION,
            "families": families, "unmeasured_requirements": UNMEASURED[employee],
            "explanation_review": "unscored_requires_independent_review", "recheck_passes_required": RECHECK_PASSES,
            "meaning": "Bounded practice evidence for this exact configuration; not expertise or improvement proof."}


def development(conn, owner, employee, model, reasoning_effort=None):
    rows = history(conn, owner, employee)
    report = profile(rows, employee, model, pack(employee)["digest"], reasoning_effort)
    families = report["families"]
    # At most two consecutive allocations to a family, including blocked calls, preserve breadth.
    last = [r["family"] for r in rows[:2]]
    eligible = [f for f in families if last != [f["family"], f["family"]]]
    priority = {"unassessed": 0, "needs_recheck": 1, "limited_objective_evidence": 2,
                "repeated_objective_checks": 3}
    chosen = min(eligible or families, key=lambda f: (priority[f["state"]], f["completed"],
                                                    FAMILIES[employee].index(f["family"])))
    report["next_practice"] = {"family": chosen["family"], "family_index": FAMILIES[employee].index(chosen["family"]),
                               "reason": chosen["state"], "breadth_guard": len(eligible) < len(families)}
    return report
