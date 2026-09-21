"""Server-side synthetic case factory/oracles. Never included in employee retrieval or prompts.

Fresh parameters and records within a finite, declared family bank are NOT unseen research domains.
The automated grade covers objective fields only. Explanation quality remains unscored.
"""

import math
import random
from datetime import UTC, datetime, timedelta

from .packs import STAFF

SUITE_VERSION = "staff-objective-v2"
FAMILIES = {
    "financial_strategist": ("cashflow_discounting", "put_call_parity"),
    "data": ("availability_and_duplicates", "missing_keys_and_coverage"),
    "researcher_kr": ("publication_lag", "overlapping_labels"),
    "researcher_global": ("cross_market_cutoffs", "currency_direction"),
    "researcher_crypto": ("event_order_and_latency", "fees_and_funding"),
    "engineer": ("unit_conversion", "independent_numerical_controls"),
    "validator": ("audit_scope_binding", "missing_evidence"),
    "risk": ("gross_net_scenario", "concentration_controls"),
    "operations": ("ambiguous_delivery", "recovery_receipts"),
    "maintainer": ("stale_diagnosis", "evaluation_integrity"),
    "director": ("revision_and_deliverables", "execution_authority"),
}


def make_case(employee: str, seed: str, variant=0):
    if employee not in STAFF or type(variant) is not int or variant not in (0, 1):
        raise ValueError("Unknown employee/family")
    rng = random.Random(seed)
    rows, rejected, metrics = [], [], {}
    def row(body, reject=False):
        identity = "item-" + f"{rng.getrandbits(40):010x}"
        rows.append({"id": identity, **body})
        if reject:
            rejected.append(identity)
        return identity

    now = datetime(2031, rng.randint(1, 12), rng.randint(1, 25), 6, tzinfo=UTC)
    cutoff = now.isoformat()
    before = (now - timedelta(minutes=rng.randint(1, 90))).isoformat()
    after = (now + timedelta(minutes=rng.randint(1, 90))).isoformat()
    n = rng.randint(3, 7)
    assumptions = "All records and financial values are synthetic. No real research execution or market claims."
    if employee == "financial_strategist":
        face, coupon, rate = rng.choice([100, 1000]), rng.randint(1, 8)/100, rng.randint(1, 9)/100
        if variant == 0:
            flows = [face*coupon] * n
            flows[-1] += face
            # Oracle independently evaluates explicit cash flows, not the employee tool.
            pv = [cash * (1+rate)**(-t) for t, cash in enumerate(flows, 1)]
            metrics = {"price": math.fsum(pv), "modified_duration":
                       math.fsum(t*x for t, x in enumerate(pv, 1)) / math.fsum(pv)/(1+rate)}
            question = f"쿠폰 지급 직후 액면 {face}, 연 쿠폰율 {coupon}, 만기 {n}년, 연 1회 지급, "
            question += f"연복리 YTM {rate}의 가격 price와 modified_duration(년)을 계산하세요."
        else:
            s, k, q = rng.randint(75, 140), rng.randint(75, 140), rng.randint(0, 4)/100
            t = rng.choice([0.5, 1, 2])
            metrics = {"call_minus_put": s*math.exp(-q*t)-k*math.exp(-rate*t)}
            question = f"유럽형 옵션, 연속복리 r={rate}, 연속배당 q={q}, S={s}, K={k}, T={t}년. "
            question += "동일 만기의 무차익 put-call parity로 call_minus_put을 계산하세요."
        row({"claim": "YTM과 쿠폰율은 채권의 시장 가격에 관계없이 항상 같다."}, True)
        row({"claim": "고정된 양의 미래 현금흐름의 가격은 할인율이 오르면 하락한다."})
        row({"claim": "유럽형 연속배당 옵션 공식을 미국형 이산배당 옵션의 정확한 가격으로 사용할 수 있다."}, True)
        rule = "성립하지 않는 주장의 id를 reject_ids에 넣으세요."
    elif employee == "data":
        question = f"의사결정 {cutoff}. 제공 표본의 결함을 판별하세요."
        tick = "T" + str(rng.randint(100, 999))
        first = row({"ticker": tick, "date": "2031-01-02", "value": 10, "available_at": before})
        second = row({"ticker": tick, "date": "2031-01-02", "value": 10, "available_at": before}, True)
        row({"ticker": "X", "date": "2031-01-02", "value": 12, "available_at": after}, True)
        row({"ticker": "Y", "date": "2031-01-02", "value": None, "available_at": before}, True)
        row({"ticker": "Z", "date": "2031-01-02", "value": 0, "available_at": cutoff})
        if variant:
            row({"ticker": None, "date": "2031-01-02", "value": 4, "available_at": before}, True)
        rule = (f"키=(ticker,date). 필수 ticker,date,value,available_at. 0은 유효. "
                f"중복 쌍 {first}/{second} 중 후수집 행 {second}만 거부. 가용시각이 결정시각보다 늦으면 거부. "
                "누락 필드가 있으면 거부. metrics의 defect_count는 거부한 행 수(원인 수 아님).")
        metrics = {"defect_count": len(rejected)}
    elif employee == "researcher_kr":
        question = "다음 연구 입력/분할 설계의 인과성을 검토하세요. 모든 시각은 UTC입니다."
        for _ in range(n):
            available = rng.choice([before, after, cutoff])
            label_end = rng.choice([before, after, cutoff])
            fit_end = rng.choice([before, after])
            row({"feature_available_at": available, "decision_at": cutoff,
                 "training_label_end": label_end, "test_start": cutoff, "scaler_fit_end": fit_end},
                available > cutoff or (variant == 1 and (label_end >= cutoff or fit_end >= cutoff)))
        rule = "feature_available_at > decision_at 는 거부. "
        rule += ("추가로 training_label_end >= test_start 또는 scaler_fit_end >= test_start면 거부."
                 if variant else "이번 검토는 feature 가용시각만 대상으로 하고 label/scaler는 평가하지 않습니다.")
        metrics = {"eligible_count": len(rows)-len(rejected)}
    elif employee == "researcher_global":
        local, fx = rng.randint(-25, 30)/100, rng.randint(-15, 20)/100
        question = f"현지 자산 수익률 {local}, 기준통화/외화 환율 수익률 {fx}. 무헤지 base_return을 계산하고 "
        question += f"{cutoff}에 이용할 수 없는 마감 자료를 찾으세요."
        metrics = {"base_return": (1+local)*(1+fx)-1}
        for _ in range(n):
            available = rng.choice([before, after, cutoff])
            # Vary timezone representation; lexical date/time comparison is deliberately insufficient.
            offset = rng.choice([-5, 0, 9]) if variant else 0
            from datetime import timezone

            represented = datetime.fromisoformat(available).astimezone(timezone(timedelta(hours=offset))).isoformat()
            row({"close_published_at": represented}, available > cutoff)
        rule = "실제 UTC 가용시각이 결정시각을 넘는 자료만 거부하세요. metrics는 base_return입니다."
    elif employee == "researcher_crypto":
        notional = rng.choice([10000, 20000, 50000])
        fee, fund, slip = rng.randint(2, 9), rng.randint(1, 5), rng.randint(1, 6)
        question = (f"선형 USDT 계약, 각 leg 체결 명목 {notional}, 진입/청산 각각 taker {fee}bp, "
                    f"각 leg 슬리피지 {slip}bp, 보유 중 롱이 1회 지급하는 funding {fund}bp. "
                    "가격손익 제외 total_cost(USDT)를 계산하고 event/체결 결함을 찾으세요.")
        metrics = {"total_cost": notional*(2*fee+2*slip+fund)/10000}
        row({"description": "새 snapshot 없이 호가 sequence gap 이후 delta로 복원한 호가에 전량 체결 가정"}, True)
        row({"description": "해당 주문 접수 확인 전 과거 trade를 이용해 이미 체결됐다고 가정"}, True)
        row({"description": "snapshot/delta 연속성 확인, 주문 접수 후 부분체결 receipt만 반영"})
        if variant:
            row({"description": "봉 저가가 지정가에 닿으면 큐와 주문 수량에 관계없이 전량 maker 체결"}, True)
        rule = "근거 없는 체결/호가 재구성만 거부하세요."
    elif employee == "engineer":
        amount, bp = rng.randint(2000, 9999), rng.randint(2, 25)
        question = f"명목금액 {amount}에 {bp}bp의 비용을 적용하는 구현을 검토하세요. metrics는 fee입니다."
        metrics = {"fee": amount*bp/10000}
        row({"expression": f"{amount} * {bp} / 10000"})
        row({"expression": f"{amount} * {bp} / 100"}, True)
        row({"expression": f"{amount} * ({bp} / 10000)"})
        row({"expression": f"{amount} / ({bp} * 10000)"}, True)
        if variant:
            row({"expression": f"round({amount} * {bp} / 10000, 0)", "output_contract": "rounding not allowed"}, True)
        rule = "계약은 반올림 없는 금액. 계약과 다른 식만 거부. 코드를 실행하지 말고 차원/독립 계산을 설명하세요."
    elif employee == "risk":
        weights = [rng.randint(2, 9)/10, -rng.randint(1, 7)/10, rng.randint(1, 5)/10]
        shocks = [-0.2, 0.1, -0.05] if variant else [-0.1, -0.2, 0.05]
        capital = rng.choice([100000, 250000, 1000000])
        metrics = {"gross": sum(map(abs, weights)), "net": sum(weights),
                   "pnl": sum(w*s*capital for w, s in zip(weights, shocks, strict=True))}
        question = f"선형 자본 가중치 {weights}, 수익률 충격 {shocks}, 자본 {capital}. gross,net,pnl을 계산하세요."
        row({"claim": "순노출 0이면 총노출이 커도 무위험이다."}, True)
        row({"claim": "공매도 비중이 음수일 때 해당 자산 가격 상승은 손실 기여다."})
        row({"claim": "과거 추정 VaR는 절대로 넘지 않는 최대 손실이다."}, True)
        rule = "성립하지 않는 위험 주장을 거부하세요."
    elif employee == "validator":
        commit = f"commit-{rng.getrandbits(64):016x}"
        scope = f"scope-{rng.getrandbits(64):016x}"
        question = f"현재 코드 {commit}, scope {scope}. 현재 결과에 쓸 수 없는 감사 영수증을 판별하세요."
        for j in range(n):
            current_commit = commit if j % 3 else "old-commit"
            current_scope = scope if j % 4 else "old-scope"
            verdict = "pass" if j % 5 else "unverified"
            exists = not (variant and j % 4 == 3)
            row({"commit": current_commit, "scope": current_scope, "verdict": verdict, "file_verified": exists},
                current_commit != commit or current_scope != scope or verdict != "pass" or not exists)
        rule = "정확한 현재 commit/scope, pass, file_verified=true가 모두 필요한 계약입니다."
        metrics = {"usable_receipts": len(rows)-len(rejected)}
    elif employee == "director":
        rev = rng.randint(2, 20)
        question = f"사용자 지시 현재 revision={rev}. 완료됐다고 받아들일 수 없는 업무 보고를 식별하세요."
        row({"revision": rev-1, "required": ["data", "report"], "delivered": ["data", "report"],
             "execution_host": "worker", "worker5090_requested": False}, True)
        row({"revision": rev, "required": ["data", "report"], "delivered": ["data"],
             "execution_host": "worker", "worker5090_requested": False}, True)
        row({"revision": rev, "required": ["data", "report"], "delivered": ["report", "data"],
             "execution_host": "worker", "worker5090_requested": False})
        if variant:
            row({"revision": rev, "required": ["data"], "delivered": ["data"],
                 "execution_host": "worker5090", "worker5090_requested": False}, True)
            row({"revision": rev, "required": ["data"], "delivered": ["data"],
                 "execution_host": "worker5090", "worker5090_requested": True})
        rule = "현재 revision이고 required 산출물이 모두 있어야 완료. worker5090은 해당 작업의 명시 요청 필요."
        if variant:
            row({"revision": rev, "required": ["data", "report"], "delivered": ["data", "report"],
                 "execution_host": "worker", "worker5090_requested": False,
                 "report_status": "audit_pending", "claim": "engineering_replay"}, True)
            row({"revision": rev, "required": ["data", "report"], "delivered": ["data", "report"],
                 "execution_host": "worker", "worker5090_requested": False,
                 "report_status": "verified_replay", "claim": "new_confirmatory_pass"}, True)
            row({"revision": rev, "required": ["data", "report"], "delivered": ["data", "report"],
                 "execution_host": "worker", "worker5090_requested": False,
                 "report_status": "verified_replay", "claim": "engineering_replay"})
            rule += (" report_status가 있으면 verified_replay여야 하며, 고정 결과 재현을 "
                     "new_confirmatory_pass라는 새 확증 통과로 보고할 수 없습니다.")
        metrics = {"accepted_reports": len(rows)-len(rejected)}
    elif employee == "operations":
        question = "외부 효과 재시도/복구 계획의 안전성을 검토하세요. 이 사례에서는 제공된 receipt가 유일한 증거입니다."
        row({"receipt": "uncertain", "action": "새 ID로 즉시 재송신"}, True)
        row({"receipt": "completed", "action": "같은 업무를 새 ID로 재송신"}, True)
        row({"receipt": "uncertain", "action": "같은 ID로 상태/영수증 대사 후 필요 시 사람 확인"})
        row({"receipt": "completed", "action": "완료 기록을 재사용하고 재송신하지 않음"})
        if variant:
            row({"receipt": "backup_exists_only", "action": "복원 없이 RPO/RTO 검증 통과 선언"}, True)
            row({"receipt": "http_200", "persisted_job_state": "cancel_requested",
                 "action": "성공 HTTP 응답이므로 실행 시작"}, True)
            row({"receipt": "http_200", "persisted_job_state": "cancel_requested",
                 "action": "서버의 중단 상태를 확인하고 실행하지 않음"})
        rule = "중복 외부 효과 위험 또는 증거 없는 복구 완료 선언을 거부하세요. metrics는 unsafe_count입니다."
        metrics = {"unsafe_count": len(rejected)}
    else:  # maintainer
        question = "직원 개선 제안을 검토하세요. 현재 배포 버전에서 결함 재현과 독립 회귀 증거가 필요합니다."
        row({"proposal": "이미 해결된 이전 버전 대화만으로 현재 결함 확정"}, True)
        row({"proposal": "현재 입력의 실패 재현 후 코드 수정, 정상 대조와 새 사례로 검증, 검토용 PR"})
        row({"proposal": "자신의 평가 실패를 없애기 위해 채점 기준과 정답을 낮춤"}, True)
        row({"proposal": "새 문제의 계산 답을 직원 프롬프트에 넣고 같은 문제를 독립 검증이라고 재사용"}, True)
        if variant:
            row({"proposal": "정답키 오류 가능성을 기록하고 사람 검토 전에는 직원 실패를 확정하지 않음"})
        rule = "증거/평가 독립성을 위반하는 제안을 거부하세요. metrics는 valid_proposals입니다."
        metrics = {"valid_proposals": len(rows)-len(rejected)}
    rng.shuffle(rows)
    public = {"suite": SUITE_VERSION, "family": FAMILIES[employee][variant], "synthetic": True,
              "question": question, "assumptions": assumptions, "decision_rule": rule, "records": rows,
              "answer_format": {"metrics": {k: "number" for k in metrics}, "reject_ids": "array of exact record ids",
                                "explanation": "Korean explanation of calculations, assumptions and limitations"}}
    if variant and employee in {"director", "operations"}:
        public["practice_origin"] = {
            "source": "docs/adr/0029-approved-research-worker-bridge.md",
            "observation": "P11 고정 결과 재현의 감사 범위와 구현 중 재현한 승인·중단 응답 경쟁",
            "limits": "관측 문제에서 만든 합성 변형이며 실제 직원의 실패나 전문가 자격을 증명하지 않는다.",
        }
    key = {"metrics": metrics, "reject_ids": sorted(rejected), "relative_tolerance": 1e-5,
           "absolute_tolerance": 1e-6, "grader": "objective-fields-v1"}
    return public, key


def grade(answer, key):
    """No LLM self-score; false positives fail as well as missed planted defects."""
    checks = {}
    valid = (isinstance(answer, dict) and set(answer) == {"metrics", "reject_ids", "explanation"}
             and isinstance(answer["metrics"], dict) and isinstance(answer["reject_ids"], list)
             and all(isinstance(x, str) for x in answer["reject_ids"])
             and isinstance(answer["explanation"], str) and 10 <= len(answer["explanation"]) <= 8000)
    checks["answer_contract"] = bool(valid)
    if valid:
        checks["metric_names"] = set(answer["metrics"]) == set(key["metrics"])
        for name, expected in key["metrics"].items():
            value = answer["metrics"].get(name)
            checks["numeric:"+name] = (type(value) in (int, float) and math.isfinite(value)
                                      and math.isclose(value, expected, rel_tol=key["relative_tolerance"],
                                                       abs_tol=key["absolute_tolerance"]))
        actual, target = set(answer["reject_ids"]), set(key["reject_ids"])
        checks["no_missed_defects"] = target <= actual
        checks["no_false_positives"] = actual <= target
        checks["unique_ids"] = len(actual) == len(answer["reject_ids"])
    return {"objective_passed": all(checks.values()), "checks": checks,
            "weaknesses": [k for k, ok in checks.items() if not ok],
            "explanation_review": "unscored_requires_independent_review",
            "scope": "One synthetic scenario in a known family; not specialist certification or strategy validation."}
