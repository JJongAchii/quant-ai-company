"""Planning triage on real PostgreSQL; synthetic programs, packets and employees. No model calls."""

import json

import pytest

from quant_company.company import PolicyError, fingerprint
from quant_company.company import fingerprint as digest_of
from quant_company.research.data_evidence import DataEvidencePacket
from quant_company.research.planning_triage import replay, triage
from quant_company.research.programs import (
    BUDGET_EXHAUSTED,
    CONDITIONAL_SOURCE,
    DUPLICATE_TASK,
    MISSING_PACKET,
    ProgramStore,
    proposal_mode_error,
)
from quant_company.research.scientific_lineages import LINEAGE_EXHAUSTED

from .test_research_conditional import (
    conditional,  # noqa: F401
    conditional_ready,
    conditional_task,
)
from .test_research_planning_contracts import candidate
from .test_research_programs import program  # noqa: F401
from .test_research_programs import task_proposal as legacy_proposal


def test_shared_messages_are_the_legacy_messages():
    assert MISSING_PACKET == "Real data readiness requires a verified input evidence packet"
    assert CONDITIONAL_SOURCE == "Conditional source evidence cannot establish original replication or transfer conditions"
    assert DUPLICATE_TASK == "Task already considered; use prior evidence to change the question"
    assert BUDGET_EXHAUSTED == "Program budget exhausted"
    assert LINEAGE_EXHAUSTED == "Cumulative scientific lineage budget exhausted"


def test_mode_rule_follows_the_signed_policy(conditional):  # noqa: F811
    envelope = conditional.program_spec.envelopes[0]
    assert proposal_mode_error(envelope, "novel_hypothesis") is None
    assert proposal_mode_error(envelope, "market_transfer") == "Conditional retrospective research admits novel hypotheses only"


def test_capacity_and_packet_helpers_read_without_side_effects(conditional):  # noqa: F811
    store = ProgramStore(conditional.company)
    with conditional.company.db.transaction() as conn:
        _, row, spec = store.locked(conn, conditional.program_id, active=True)
        envelope = spec.envelopes[0]
        assert store.capacity_error(conn, conditional.program_id, spec) is None
        assert store.packet_required(envelope, exploratory=False) is True
        assert store.envelope_packet(row, envelope) is not None
        before = conn.execute("SELECT count(*) AS n FROM research_program_tasks").fetchone()["n"]
        identity, digest = store.task_identity(conditional.program_id, {"title": "x"})
        assert conn.execute("SELECT count(*) AS n FROM research_program_tasks").fetchone()["n"] == before
        assert len(digest) == 64 and identity


def run(h, admission=("fixture:baseline",), **changes):
    with h.company.db.transaction() as conn:
        return triage(h.company, conn, candidate(**changes), admission=set(admission), program_id=h.program_id)


def failed(result):
    return {check.code: check for check in result.checks if check.status == "fail"}


def test_conditional_candidate_is_eligible(conditional):  # noqa: F811
    result = run(conditional)
    assert result.outcome == "eligible", failed(result)
    assert result.basis["engine_card"] == "kr-research-python-v2@1" and result.basis["packet_digest"]


@pytest.mark.parametrize("change,code,outcome,kind", [
    ({"mode": "market_transfer"}, "T4", "out_of_scope", "data_policy"),
    ({"estimand": "mean-effect"}, "T5", "needs_evaluator", "evaluator"),
    ({"execution_timing": "same_day_close_fill"}, "T6", "needs_evaluator", "execution_timing"),
    ({"envelope": "missing"}, "T3", "needs_program", "program_missing"),
])
def test_each_rule_violation_has_outcome_and_gap(conditional, change, code, outcome, kind):  # noqa: F811
    result = run(conditional, **change)
    check = failed(result)[code]
    assert (check.outcome, check.gap.kind) == (outcome, kind)
    assert result.outcome == outcome


def test_outside_admission_is_invalid_and_unbound_candidates_have_no_experiment_path(conditional):  # noqa: F811
    assert run(conditional, admission=()).outcome == "invalid"
    with conditional.company.db.transaction() as conn:
        unbound = triage(conditional.company, conn, candidate(envelope=None), admission={"fixture:baseline"})
    assert unbound.outcome == "unbound"
    assert all(c.status == "not_applicable" for c in unbound.checks[2:])


def test_packet_gaps_match_the_data_assessment_message(conditional):  # noqa: F811
    from quant_company.research.programs import ProgramStore

    registry = conditional.company.settings.research_data_evidence_file
    packet = json.loads(registry.read_text())["packets"][0] | {"blocking_gaps": ["Synthetic unresolved source"]}
    registry.write_text(json.dumps({"packets": [packet]}))
    check = failed(run(conditional))["T7"]
    assert check.outcome == "needs_data" and check.gap.resolver == "operator"
    # The data employee cites the same (changed) packet, so the legacy path reaches the same gap rule.
    ready = conditional_ready(conditional, packet_digest=fingerprint(
        DataEvidencePacket.model_validate(packet).model_dump(mode="json")))
    store = ProgramStore(conditional.company)
    with pytest.raises(PolicyError) as legacy:
        with conditional.company.db.transaction() as conn:
            task_id = store.propose(conn, conditional.program_id, legacy_proposal(mode="novel_hypothesis"),
                                    actor="researcher_kr")
            store.assess(conn, conditional.program_id, task_id, ready, actor="data")
    assert str(legacy.value) == check.message == "Data evidence packet still records blocking gaps"


def test_duplicate_and_budget_match_the_selection_messages(conditional):  # noqa: F811
    from quant_company.research.planning_contracts import PlanningCandidate, task_proposal
    from quant_company.research.programs import ProgramStore

    value = candidate()
    with conditional.company.db.transaction() as conn:
        ProgramStore(conditional.company).propose(conn, conditional.program_id,
            task_proposal(PlanningCandidate.model_validate(value)).model_dump(mode="json"), actor="researcher_kr")
    assert failed(run(conditional))["T8"].outcome == "duplicate"
    conditional_task(conditional)  # max_missions=1 is now used
    check = failed(run(conditional, title="Another fixture candidate"))["T9"]
    assert check.message == "Program budget exhausted" and check.gap.kind == "program_budget"


def test_lineage_exhaustion_matches_reservation_rule(conditional, monkeypatch):  # noqa: F811
    import quant_company.research.scientific_lineages as lineages

    monkeypatch.setattr(lineages, "usage", lambda conn, lineage_id: 4)
    check = failed(run(conditional))["T8"]
    assert check.message == "Cumulative scientific lineage budget exhausted"
    assert check.gap.kind == "lineage_budget"


def test_stale_source_after_correction(conditional):  # noqa: F811
    with conditional.company.db.transaction() as conn:
        conn.execute("""INSERT INTO quant_feed_sources(id,config,config_digest,enabled)
            VALUES('fixture','{}','fixture',false) ON CONFLICT DO NOTHING""")
        conn.execute("INSERT INTO quant_feed_works(id) VALUES('w') ON CONFLICT DO NOTHING")
        conn.execute("""INSERT INTO quant_feed_candidates(id,source_id,url,title,source_digest)
            VALUES('d','fixture','https://example.org/d','d','x')""")
        conn.execute("""INSERT INTO quant_feed_documents(id,work_id,candidate_id,source_digest,content_digest,metadata,
            receipt,pages) VALUES('d','w','d','x','y','{}','{}','[]')""")
        conn.execute("""INSERT INTO research_literature(source_id,project_id,document_id,work_id,content_digest,state)
            VALUES('fixture:baseline',%s,'d','w','y','superseded')""", (conditional.project["project_id"],))
    check = failed(run(conditional))["T1"]
    assert check.outcome == "stale" and check.gap.resolver == "employee"


def bundle_for(h, *, blocking=("Unverified source timing",), declared=None):
    spec = h.program_spec.model_dump(mode="json")
    proposal = {key: value for key, value in candidate().items()
                if key in {"title", "envelope", "question", "mode", "original_claim", "method", "departures",
                           "falsification", "source_ids", "citations", "predecessor_mission_ids"}}
    return {"program_id": h.program_id, "program_manifest_digest": digest_of(spec), "program": spec,
            "packets": [{"envelope": "etf", "packet_present": True, "blocking_gaps": list(blocking),
                         "provenance": "fixture packet"}],
            "tasks": [{"id": "00000000-0000-0000-0000-000000000001", "state": "waiting", "proposal": proposal,
                       "data_decision": "blocked", "selection_decision": "wait", "created_at": "2026-09-29T00:00:00Z",
                       **(declared or {})}],
            "provenance": ["synthetic fixture"]}


def test_replay_is_deterministic_and_marks_unknowns(conditional):  # noqa: F811
    first, second = replay(bundle_for(conditional)), replay(bundle_for(conditional))
    assert first == second
    task = first["tasks"][0]
    assert task["replay_outcome"] == "needs_data" and task["stopped_before_data_stage"]
    statuses = {c["code"]: c["status"] for c in task["checks"]}
    assert statuses["T5"] == "not_declared" and statuses["T9"] == "not_declared"
    assert first["summary"] == {"tasks": 1, "stopped_before_data_stage": 1, "actual_data_blocked": 1,
                                "blocked_and_stopped": 1, "by_outcome": {"needs_data": 1}}


def test_replay_uses_only_recorded_declarations(conditional):  # noqa: F811
    declared = {"declared_execution_timing": "same_day_close_fill", "declaration_provenance": "fixture note"}
    task = replay(bundle_for(conditional, blocking=(), declared=declared))["tasks"][0]
    t6 = next(c for c in task["checks"] if c["code"] == "T6")
    assert t6["status"] == "fail" and task["replay_outcome"] == "needs_evaluator"


def test_replay_rejects_a_changed_program(conditional):  # noqa: F811
    value = bundle_for(conditional)
    value["program_manifest_digest"] = "0" * 64
    with pytest.raises(PolicyError, match="manifest digest"):
        replay(value)


def test_commands_are_read_only(tmp_path, conditional):  # noqa: F811
    from quant_company.research.planning_commands import command

    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle_for(conditional)))
    settings, company = conditional.company.settings, conditional.company
    assert command(settings, "replay-triage", bundle=path)["summary"]["tasks"] == 1
    assert command(settings, "status", company=company) == {"projects": []}
    with company.db.transaction() as conn:
        before = conn.execute("SELECT count(*) AS n FROM research_literature").fetchone()["n"]
    with pytest.raises(PolicyError, match="Unknown research question"):
        command(settings, "preview", question="00000000-0000-0000-0000-000000000000", company=company)
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM research_literature").fetchone()["n"] == before
