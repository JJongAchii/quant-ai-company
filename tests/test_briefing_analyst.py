import json
from html import escape
from pathlib import Path

import pytest

from quant_company.briefing.contracts import BRIEFER, REVIEW_CHECKS, BriefProposal, BriefReview, item_map
from quant_company.briefing.editor import prompt, prune, render, validate
from quant_company.company import load_roles
from quant_company.config import Settings
from quant_company.staff.cases import FAMILIES, grade, make_case
from quant_company.staff.packs import pack
from quant_company.staff.progress import profile
from quant_company.staff.store import StaffStore

from .test_briefing import CONTENT, brief, bundle, evidence, proposal, response, review, seed  # noqa: F401
from .test_staff_development import reply


def analytical_proposal():
    value = proposal().model_dump(mode="json")

    def claim(identity, text, kind):
        return {"id": identity, "text": text, "kind": kind, "evidence": evidence()}

    value["watchpoints"] = []
    value["issues"] = [{
        "headline": "반도체 주도의 상승, 시장 전반 확산은 미확인",
        "fact": claim("driver-fact", "국채 수익률이 하락했고 반도체가 상승을 주도했습니다.", "fact"),
        "interpretation": claim("driver-view", "할인율 하락이 성장주에 우호적일 수 있지만, 업종 고유 재료도 구분해야 합니다.", "interpretation"),
        "next_check": claim("driver-check", "비기술 업종의 흐름이 계속 엇갈리면 시장 전반의 강세라는 해석을 유보합니다.", "condition"),
        "analysis": {"horizon": "days_weeks", "causal_basis": "conditional_hypothesis",
            "mechanism": claim("driver-mechanism", "다른 조건이 같다면 할인율 하락이 미래 현금흐름의 가치에 우호적일 수 있습니다.", "interpretation"),
            "alternative": claim("driver-alternative", "기술주 밖의 혼조를 보면 업종 고유 재료가 주도했을 가능성도 있습니다.", "interpretation")}}]
    return BriefProposal.model_validate(value)


def test_analyst_identity_keeps_existing_receipt_and_authorization_key():
    role = load_roles(Settings())[BRIEFER]
    manifest = json.loads(Path("slack-apps/market_brief.json").read_text())
    assert role.id == "market_brief" and role.name == "애널리스트"
    assert manifest["display_information"]["name"] == "Analyst"
    assert manifest["features"]["bot_user"]["display_name"] == "analyst"
    assert not role.active and not role.can_delegate_to
    assert {"finance_compute", "finance_read", "finance_search"} <= set(role.tools)


def test_analysis_is_required_and_unsupported_reasoning_removes_whole_issue():
    p = analytical_proposal()
    assert validate(p, bundle()) == {}
    missing = p.model_dump(mode="json")
    del missing["issues"][0]["analysis"]
    with pytest.raises(ValueError):
        BriefProposal.model_validate(missing)
    p.issues[0].analysis.mechanism.text = "이익이 9999% 증가했습니다."
    rejected = validate(p, bundle())
    assert rejected["driver-mechanism"] == "unsupported_prose_number"
    assert prune(p, rejected).issues == []


@pytest.mark.parametrize("part", ["mechanism", "alternative"])
def test_analyst_hypotheses_cannot_be_typed_as_observed_facts(part):
    p = analytical_proposal()
    claim = getattr(p.issues[0].analysis, part)
    claim.kind = "fact"
    assert validate(p, bundle())[claim.id] == "analyst_reasoning_must_be_interpretation"
    claim.kind = "interpretation"
    claim.evidence[0].quote = "A different unprovided report supposedly proves this claim."
    assert validate(p, bundle())[claim.id] == "evidence_not_in_frozen_original"


def test_professional_review_cannot_omit_transmission_alternatives_or_falsifiability():
    for check in ("transmission", "alternatives", "falsifiability"):
        value = review().model_dump()
        value["checks"].pop(check)
        with pytest.raises(ValueError, match=f"checks.{check}"):
            BriefReview.model_validate(value)
    assert len(REVIEW_CHECKS) == 12


def test_main_post_keeps_assessment_effect_and_alternative_visible_without_thread_duplication():
    p = analytical_proposal()
    parts, quality = render(p, bundle())
    assert "Analyst · AI 시장분석" in parts[0] and "Analyst 해석" in parts[0]
    assert p.issues[0].next_check.text in parts[0]
    assert p.issues[0].analysis.mechanism.text in parts[0]
    assert p.issues[0].interpretation.text in parts[0]
    assert p.issues[0].interpretation.text not in "\n".join(parts[1:])
    assert p.issues[0].analysis.alternative.text in parts[0]
    assert p.issues[0].analysis.alternative.text not in "\n".join(parts[1:])
    assert "수일~수주" in parts[0]
    assert p.issues[0].headline not in "\n".join(parts[1:])
    assert len(parts[0]) < 2000 and quality["format_version"] == 19
    payload = json.loads(prompt(bundle(), "review", p.model_dump(mode="json")).split("BRIEF DATA JSON:\n")[1])
    items = item_map(p)
    preview = "".join(part if isinstance(part, str) else escape(items[part["item_text"]].text, quote=False)
                      for part in payload["main_post_preview"])
    assert preview == parts[0]


def test_market_brief_keeps_later_material_issues_at_the_same_heading_level():
    value = analytical_proposal().model_dump(mode="json")
    first = value["issues"][0]
    value["issues"] = []
    headlines = ("반도체 실적", "유가와 공급", "금리와 환율의 차이", "무역지표와 통상",
                 "금융·자동차 업종", "해외 중앙은행 결정")
    for index, headline in enumerate(headlines):
        issue = json.loads(json.dumps(first))
        issue["headline"] = headline
        for claim in (issue["fact"], issue["interpretation"], issue["next_check"],
                      issue["analysis"]["mechanism"], issue["analysis"]["alternative"]):
            claim["id"] += f"-{index}"
        value["issues"].append(issue)
    p = BriefProposal.model_validate(value)
    main = render(p, bundle())[0][0]
    assert main.count("*주요 이슈*") == 1 and "함께 볼 이슈" not in main
    assert main.index("주요 이슈") < main.index(p.issues[0].headline)
    assert [main.index(headline) for headline in headlines] == sorted(main.index(headline) for headline in headlines)
    for index, issue in enumerate(p.issues, 1):
        assert f"*{index}. {issue.headline}*" in main
        assert issue.interpretation.text in main and issue.analysis.alternative.text in main
        assert issue.next_check.text in main
    payload = json.loads(prompt(bundle(), "review", p.model_dump(mode="json")).split("BRIEF DATA JSON:\n")[1])
    items = item_map(p)
    preview = "".join(part if isinstance(part, str) else escape(items[part["item_text"]].text, quote=False)
                      for part in payload["main_post_preview"])
    assert preview == main


def test_frozen_procedure_is_used_by_writer_and_reviewer(brief, monkeypatch):  # noqa: F811
    store, _ = brief
    seed(brief)
    first = store.prepare()["request"]
    assert pack(BRIEFER)["digest"] in first["prompt"]
    store.commit(response(first, analytical_proposal()))
    monkeypatch.setattr("quant_company.briefing.editor.pack", lambda employee: {"procedure": "changed-host-pack"})
    second = store.prepare()["request"]
    assert pack(BRIEFER)["digest"] in second["prompt"]
    assert "changed-host-pack" not in second["prompt"]
    assert "driver-alternative" in second["prompt"]


def test_reviewer_can_remove_unsupported_analysis_without_leaking_it_to_slack(brief):  # noqa: F811
    store, clock = brief
    edition = seed(brief)
    store.commit(response(store.prepare()["request"], analytical_proposal()))
    critic = store.prepare()["request"]
    clock["at"] = edition.due_at
    verdict = review().model_dump()
    verdict.update(verdict="reduce", rejected_ids=["driver-alternative"], concerns=["대안 설명의 근거가 부족함"])
    verdict["checks"]["alternatives"] = False
    store.commit(response(critic, BriefReview.model_validate(verdict)))
    with store.db.transaction() as conn:
        row = conn.execute("SELECT * FROM brief_editions WHERE state='ready'").fetchone()
    assert row["proposal"]["issues"] == [] and row["quality"]["reduced"]
    assert "업종 고유 재료가 주도했을 가능성" not in "\n".join(row["rendered"])


def test_analyst_practice_failures_feed_brief_without_exposing_answer_key(brief):  # noqa: F811
    store, _ = brief
    staff = StaffStore(store.company)
    identity = staff.enqueue(BRIEFER, "UHUMAN")
    prepared = staff.prepare()
    assert prepared["run_id"] == identity
    assert "answer_key" not in prepared["request"]["prompt"]
    staff.commit(identity, reply(prepared["request"], answer={"metrics": {}, "reject_ids": [],
                 "explanation": "합성 사례의 의도적으로 틀린 테스트 답입니다."}))
    seed(brief)
    writer = store.prepare()["request"]
    payload = json.loads(writer["prompt"].split("BRIEF DATA JSON:\n")[1])
    assert payload["professional_feedback"][0]["run_id"] == identity
    assert payload["professional_feedback"][0]["weaknesses"]
    assert "answer_key" not in writer["prompt"]


def test_synthetic_checks_never_certify_unmeasured_analyst_judgment():
    role = load_roles(Settings())[BRIEFER]
    report = profile([], BRIEFER, role.model, pack(BRIEFER)["digest"], role.reasoning_effort)
    assert all(f["state"] == "unassessed" for f in report["families"])
    assert len(report["unmeasured_requirements"]) >= 4
    assert "not expertise" in report["meaning"]
    for variant in (0, 1):
        public, key = make_case(BRIEFER, "independent-analyst-fixture", variant)
        assert public["family"] == FAMILIES[BRIEFER][variant] and public["synthetic"]
        wrong = {"metrics": key["metrics"], "reject_ids": [], "explanation": "모든 주장을 무비판적으로 받아들이는 테스트입니다."}
        assert not grade(wrong, key)["objective_passed"]


def test_writer_receives_same_professional_procedure_as_followup_role():
    assert pack(BRIEFER)["digest"] in prompt(bundle(), "write")
    assert "surprise versus consensus differs from change versus prior" in prompt(bundle(), "write")
    assert CONTENT in prompt(bundle(), "review", analytical_proposal().model_dump(mode="json"))


@pytest.mark.asyncio
async def test_practice_does_not_start_a_model_call_during_brief_preparation(brief):  # noqa: F811
    from quant_company.staff.runner import StaffRunner

    class NeverCalled:
        async def run(self, request):
            raise AssertionError("Practice must yield to the scheduled briefing")

    store, _ = brief
    seed(brief)
    result = await StaffRunner(store.company, provider=NeverCalled()).tick(manual=True)
    assert result == {"state": "defer", "reason": "scheduled_briefing_priority"}
