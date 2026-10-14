"""Planning contracts are pure models; no database."""

from types import SimpleNamespace
from typing import get_args

import pytest
from pydantic import ValidationError

from quant_company.company import fingerprint
from quant_company.quant_feed.contracts import ResearchBrief
from quant_company.research.mission_contracts import Objective
from quant_company.research.planning_contracts import (
    ENGINE_RULE_CARDS,
    METRICS,
    PLANNING_LIMITS,
    TOPICS,
    CandidateReview,
    Estimand,
    PlanningCandidate,
    PlanningCritique,
    PlanningDecision,
    PlanningSlate,
    QuestionSpec,
    TriageCheck,
    TriageResult,
    engine_card,
    service_order,
    summarize,
    task_proposal,
)
from quant_company.research.program_contracts import ResearchTaskProposal


def candidate(**changes):
    return {"title": "Fixture candidate", "question": "Does the fixture hold?", "mode": "novel_hypothesis",
            "original_claim": "Synthetic claim", "method": "Controlled fixture", "departures": "Synthetic data",
            "falsification": "Consumer rejects a broken identity", "source_ids": ["fixture:baseline"],
            "citations": [{"source_id": "fixture:baseline", "location": "char:0",
                           "quote": "Synthetic original: point-in-time fixture data"}],
            "predecessor_mission_ids": [], "envelope": "etf", "contribution_kind": "alpha_signal",
            "mechanism": "Fixture mechanism", "baseline_comparator": "Buy and hold",
            "estimand": "stress-net-absolute-cagr", "execution_timing": "prior_session_open_fill",
            "implementation_scope": "config_only",
            "contradiction_search": {"status": "searched_not_found", "scope_note": "Fixture corpus only"},
            "lineage_relation": {"status": "new"}, **changes}


def question(**changes):
    return {"question": "Does momentum survive costs in Korean ETFs?",
            "decision_to_inform": "Whether to register a cost-aware momentum task",
            "market": "kr_etf", "search_terms_en": ["momentum"], "search_terms_ko": ["모멘텀"], **changes}


def test_projection_is_verbatim_and_digest_stable():
    value = PlanningCandidate.model_validate(candidate())
    expected = {key: candidate()[key] for key in ResearchTaskProposal.model_fields}
    projected = task_proposal(value).model_dump(mode="json")
    assert projected == ResearchTaskProposal.model_validate(expected).model_dump(mode="json")
    assert fingerprint(projected) == fingerprint(ResearchTaskProposal.model_validate(expected).model_dump(mode="json"))


def test_candidate_without_envelope_has_no_experiment_path():
    with pytest.raises(ValueError, match="no experiment path"):
        task_proposal(PlanningCandidate.model_validate(candidate(envelope=None)))


def test_literals_follow_existing_contracts():
    assert set(get_args(Estimand)) == set(get_args(Objective.model_fields["metric"].annotation)) | {"other"}
    assert set(METRICS) == set(get_args(Objective.model_fields["metric"].annotation))
    assert set(TOPICS) == set(get_args(ResearchBrief.model_fields["topic"].annotation))


def test_negative_repeat_and_contradiction_states_are_explicit():
    with pytest.raises(ValidationError, match="stated difference"):
        PlanningCandidate.model_validate(candidate(lineage_relation={"status": "repeats_negative"}))
    with pytest.raises(ValidationError, match="found contradiction"):
        PlanningCandidate.model_validate(candidate(contradiction_search={"status": "found"}))
    refs = [{"source_id": "fixture:baseline", "location": "char:0", "quote": "Synthetic original text"}]
    with pytest.raises(ValidationError, match="found contradiction"):
        PlanningCandidate.model_validate(candidate(contradiction_search={"status": "not_searched", "refs": refs}))


def test_question_needs_terms_and_unique_lists():
    assert QuestionSpec.model_validate(question()).mandate == "unknown"
    with pytest.raises(ValidationError, match="search terms"):
        QuestionSpec.model_validate(question(search_terms_en=[], search_terms_ko=[]))
    with pytest.raises(ValidationError, match="repeat"):
        QuestionSpec.model_validate(question(search_terms_en=["momentum", "momentum"]))
    with pytest.raises(ValidationError):
        QuestionSpec.model_validate(question(topics=["not_a_feed_topic"]))


def test_triage_records_all_checks_and_most_terminal_outcome():
    checks = [TriageCheck(code=f"T{n}", status="pass") for n in range(1, 10)]
    assert summarize(checks, bound=True) == "eligible"
    assert summarize(checks, bound=False) == "unbound"
    checks[6] = TriageCheck(code="T7", status="fail", outcome="needs_data", message="packet")
    checks[3] = TriageCheck(code="T4", status="fail", outcome="out_of_scope", message="mode")
    assert summarize(checks, bound=True) == "out_of_scope"
    with pytest.raises(ValidationError, match="every check once"):
        TriageResult(outcome="eligible", checks=list(reversed(checks)), basis={})
    with pytest.raises(ValidationError, match="Only a failed check"):
        TriageCheck(code="T1", status="pass", outcome="stale")


def test_engine_card_follows_worker_transport_selection():
    assert engine_card(SimpleNamespace(schema_version=3, execution_profile="kr-etf-research-v2")).id == "kr-research-python-v2"
    assert engine_card(SimpleNamespace(schema_version=1, execution_profile="kr-etf-monthly-python-v1")) is None
    assert ENGINE_RULE_CARDS["kr-research-python-v2"].supported_execution_timings == ["prior_session_open_fill"]


def test_slate_states_why_it_is_empty_and_caps_candidates():
    assert PlanningSlate.model_validate({"candidates": [], "no_candidate_reason": "No eligible evidence",
                                         "reading_notes": "Read the question and cards"}).candidates == []
    with pytest.raises(ValidationError, match="no_candidate_reason"):
        PlanningSlate.model_validate({"candidates": [], "reading_notes": "Read"})
    with pytest.raises(ValidationError, match="no_candidate_reason"):
        PlanningSlate.model_validate({"candidates": [candidate()], "no_candidate_reason": "x", "reading_notes": "Read"})
    with pytest.raises(ValidationError):
        PlanningSlate.model_validate({"candidates": [candidate(title=f"Fixture {n}") for n in range(4)],
                                      "reading_notes": "Read"})
    assert (PLANNING_LIMITS["slate_turns"], PLANNING_LIMITS["attempts"], PLANNING_LIMITS["run_requests"],
            PLANNING_LIMITS["owner_day_requests"]) == (36, 3, 90, 120)


def review(ordinal, **changes):
    return {"candidate_ordinal": ordinal, "verdict": "support", "economic_rationale": "Fixture mechanism holds",
            "alternative_explanations": "Market beta", "decisive_test": "Cost-adjusted comparison",
            "cost_and_capacity_concerns": "Turnover", "evidence_quality": 2, "decision_value": 2,
            "novelty": "distinct", **changes}


def decision(**changes):
    return {"order": [1, 2], "selected_ordinal": 1,
            "routes": [{"candidate_ordinal": 1, "route": "experiment", "rationale": "Eligible and supported"},
                       {"candidate_ordinal": 2, "route": "defer", "rationale": "Needs an evaluator"}],
            "owner_summary": "Compared two fixture candidates; literature and plan review only.", **changes}


def test_critique_reviews_each_candidate_once_and_keeps_unknown():
    assert PlanningCritique.model_validate({"reviews": [review(1, evidence_quality="unknown")]}).reviews[0].evidence_quality == "unknown"
    with pytest.raises(ValidationError, match="exactly once"):
        PlanningCritique.model_validate({"reviews": [review(1), review(1)]})


def test_decision_routes_every_candidate_with_one_selected_experiment():
    assert PlanningDecision.model_validate(decision()).selected_ordinal == 1
    with pytest.raises(ValidationError, match="each candidate once"):
        PlanningDecision.model_validate(decision(order=[1]))
    with pytest.raises(ValidationError, match="single experiment"):
        PlanningDecision.model_validate(decision(selected_ordinal=None))
    with pytest.raises(ValidationError, match="single experiment"):
        PlanningDecision.model_validate(decision(routes=[
            {"candidate_ordinal": 1, "route": "experiment", "rationale": "a"},
            {"candidate_ordinal": 2, "route": "experiment", "rationale": "b"}]))


def test_service_order_follows_the_design_keys():
    candidates = {1: PlanningCandidate.model_validate(candidate(implementation_scope="new_model")),
                  2: PlanningCandidate.model_validate(candidate(title="Second", implementation_scope="config_only")),
                  3: PlanningCandidate.model_validate(candidate(title="Third"))}
    reviews = {1: CandidateReview.model_validate(review(1)), 2: CandidateReview.model_validate(review(2)),
               3: CandidateReview.model_validate(review(3, verdict="weak"))}
    # Triage class first, then verdict, scores, implementation scope, novelty, ordinal.
    assert service_order(candidates, {1: "eligible", 2: "eligible", 3: "eligible"}, reviews) == [2, 1, 3]
    assert service_order(candidates, {1: "needs_data", 2: "duplicate", 3: "eligible"}, reviews) == [3, 1, 2]
