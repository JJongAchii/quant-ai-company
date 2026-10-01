"""Real PG authority/evidence gates with synthetic data; no financial research."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from quant_company.company import PolicyError, fingerprint
from quant_company.research.adaptive_contracts import digest_model
from quant_company.research.data_evidence import load_packets
from quant_company.research.mission_contracts import MissionSpec, RetrospectiveDataPolicy
from quant_company.research.program_contracts import DataAssessment, ResearchProgram

from .test_research_programs import program, provision_data_packet, ready, task_proposal  # noqa: F401


def exploratory_assessment(h, **changes):
    policy = h.program_spec.envelopes[0].template.data.policy
    return ready(decision="exploratory_only", point_in_time=False, executable_prices=False,
                 evaluation_prices=True, data_policy_digest=digest_model(policy), **changes)


def prepare_exploratory_program(h, *, cancel_prior=True, approve=True):
    """A separate fixture owner signs the amended digest, preserving the old record."""
    packet = provision_data_packet(h)
    h.strict_program_id, h.strict_digest, h.strict_spec = h.program_id, h.digest, h.program_spec
    with h.company.db.transaction() as conn:
        h.strict_approval = conn.execute("SELECT approval_event_id FROM research_programs WHERE id=%s",
                                        (h.program_id,)).fetchone()["approval_event_id"]
    if cancel_prior:
        cancel = h.owner_event("cancel", target={"kind": "program", "target_id": h.program_id,
            "revision": 1, "manifest_digest": h.digest})
        with h.company.db.transaction() as conn:
            h.program_store.owner_command(conn, h.program_id, **h.owner_args(cancel), action="cancel")
    policy = RetrospectiveDataPolicy(evidence_reports={name: item["sha256"] for name, item in packet["reports"].items()})
    raw = h.program_spec.model_dump(mode="json")
    raw["title"] = "Synthetic retrospective exploration"
    raw["envelopes"][0]["template"]["data"]["policy"] = policy.model_dump(mode="json")
    h.program_spec = ResearchProgram.model_validate(raw)
    with h.company.db.transaction() as conn:
        project = h.company._project(conn, h.project["project_id"])
        row = h.program_store.create(conn, project, h.program_spec)
        h.program_id, h.digest = row["id"], row["manifest_digest"]
    approval = h.owner_event("approve", target={"kind": "program", "target_id": h.program_id,
        "revision": 1, "manifest_digest": h.digest})
    with h.company.db.transaction() as conn:
        if approve:
            h.program_store.owner_command(conn, h.program_id, **h.owner_args(approval), action="approve")
        conn.execute("UPDATE turns SET status='stale'")
        conn.execute("UPDATE tasks SET status='completed'")
    packet.update(program_digest=h.digest, data_policy=policy.model_dump(mode="json"), blocking_gaps=[])
    write_registry(h, packet)
    return packet


def write_registry(h, packet):
    h.company.settings.research_data_evidence_file.write_text(json.dumps({"schema_version": 1, "packets": [packet]}))


def create_exploratory_task(h, **changes):
    with h.company.db.transaction() as conn:
        task = h.program_store.propose(conn, h.program_id, task_proposal(**changes), actor="researcher_kr")
        h.program_store.assess(conn, h.program_id, task, exploratory_assessment(h), actor="data")
        h.program_store.decide(conn, h.program_id, task, {"decision": "accept", "rationale": "Synthetic exploration"}, actor="director")
        h.mission_id = str(conn.execute("SELECT mission_id FROM research_program_tasks WHERE id=%s", (task,)).fetchone()["mission_id"])
    return task


def test_original_signed_program_digest_and_assessment_bytes_are_unchanged():
    old = json.loads(Path("docs/project/evidence/research-programs-20260928/first-program-draft.json").read_text())
    parsed = ResearchProgram.model_validate(old["program"])
    assert parsed.model_dump(mode="json") == old["program"]
    assert fingerprint(parsed.model_dump(mode="json")) == old["program_digest"]
    assert DataAssessment.model_validate(ready()).model_dump(mode="json") == ready()


@pytest.mark.parametrize("changes", [
    {"confirmation_eligible": True}, {"deployment_eligible": True},
    {"availability": "verified"}, {"evidence_reports": {"../proof.json": "1" * 64}},
])
def test_policy_cannot_claim_verified_history_or_promote_results(changes):
    with pytest.raises(ValidationError):
        RetrospectiveDataPolicy.model_validate({"evidence_reports": {"proof.json": "1" * 64}, **changes})


@pytest.mark.parametrize("changes", [{"kind": "claim"}, {"market": "kr_stock"}])
def test_policy_is_limited_to_the_reviewed_etf_strategy_scope(changes):
    old = json.loads(Path("docs/project/evidence/research-programs-20260928/first-program-draft.json").read_text())
    spec = old["program"]["envelopes"][0]["template"]
    spec["data"]["policy"] = RetrospectiveDataPolicy(evidence_reports={"proof.json": "1" * 64}).model_dump(mode="json")
    with pytest.raises(ValidationError, match="limited to ETF strategy exploration"):
        MissionSpec.model_validate(spec | changes)


def test_strict_program_cannot_use_an_exploratory_decision(program):  # noqa: F811
    h = program
    with h.company.db.transaction() as conn:
        task = h.program_store.propose(conn, h.program_id, task_proposal(), actor="researcher_kr")
    with pytest.raises(PolicyError, match="exact owner-approved data policy"):
        with h.company.db.transaction() as conn:
            h.program_store.assess(conn, h.program_id, task,
                ready(decision="exploratory_only", point_in_time=False, executable_prices=False,
                      data_policy_digest="1" * 64, evaluation_prices=True), actor="data")


def test_new_scope_requires_its_own_signed_owner_digest(program):  # noqa: F811
    h = program
    prepare_exploratory_program(h)
    assert h.digest != h.strict_digest
    with pytest.raises(PolicyError, match="bound authenticated"):
        with h.company.db.transaction() as conn:
            h.program_store.owner_command(conn, h.program_id, owner="UHUMAN", revision=1,
                manifest_digest=h.digest, event_key=h.strict_approval, action="approve")
    with h.company.db.transaction() as conn:
        _, old, _ = h.program_store.locked(conn, h.strict_program_id)
        assert old["spec"] == h.strict_spec.model_dump(mode="json")
        assert old["manifest_digest"] == h.strict_digest and old["approval_event_id"] == h.strict_approval


def test_replacement_cannot_duplicate_the_active_program_budget(program):  # noqa: F811
    h = program
    prepare_exploratory_program(h, cancel_prior=False, approve=False)
    approval = h.owner_event("approve", target={"kind": "program", "target_id": h.program_id,
        "revision": 1, "manifest_digest": h.digest})
    with pytest.raises(PolicyError, match="cancellation of the prior active program"):
        with h.company.db.transaction() as conn:
            h.program_store.owner_command(conn, h.program_id, **h.owner_args(approval), action="approve")


@pytest.mark.parametrize("changes", [
    {"coverage": False}, {"evaluation_prices": False}, {"evaluation_prices": None},
    {"point_in_time": True}, {"executable_prices": True}, {"original_conditions": True},
    {"data_policy_digest": None},
])
def test_exploration_keeps_unknown_flags_and_requires_verified_evaluation_inputs(changes):
    value = ready(decision="exploratory_only", point_in_time=False, executable_prices=False,
                  data_policy_digest="1" * 64, evaluation_prices=True)
    with pytest.raises(ValidationError):
        DataAssessment.model_validate(value | changes)


@pytest.mark.parametrize("problem", ["absent", "policy", "report", "gap", "assessment_digest"])
def test_exploration_requires_the_exact_packet_without_other_blocking_gaps(program, problem):  # noqa: F811
    h = program
    packet = prepare_exploratory_program(h)
    with h.company.db.transaction() as conn:
        task = h.program_store.propose(conn, h.program_id, task_proposal(), actor="researcher_kr")
    assessment = exploratory_assessment(h)
    if problem == "absent":
        h.company.settings.research_data_evidence_file = None
    elif problem == "policy":
        packet.pop("data_policy")
        write_registry(h, packet)
    elif problem == "report":
        Path(packet["reports"]["limitations.txt"]["path"]).write_text("changed proof")
    elif problem == "gap":
        packet["blocking_gaps"] = ["Missing coverage outside acknowledged historical assumptions"]
        write_registry(h, packet)
    else:
        assessment["data_policy_digest"] = "1" * 64
    with pytest.raises(PolicyError):
        with h.company.db.transaction() as conn:
            h.program_store.assess(conn, h.program_id, task, assessment, actor="data")


def test_exploration_cannot_be_ready_or_exact_replication(program):  # noqa: F811
    h = program
    prepare_exploratory_program(h)
    with pytest.raises(PolicyError, match="exact replication"):
        with h.company.db.transaction() as conn:
            h.program_store.propose(conn, h.program_id, task_proposal(mode="exact_replication"), actor="researcher_kr")
    with h.company.db.transaction() as conn:
        task = h.program_store.propose(conn, h.program_id, task_proposal(), actor="researcher_kr")
    with pytest.raises(PolicyError, match="strict data readiness"):
        with h.company.db.transaction() as conn:
            h.program_store.assess(conn, h.program_id, task, ready(), actor="data")


def test_direct_mission_approval_cannot_skip_program_data_review(program):  # noqa: F811
    h = program
    prepare_exploratory_program(h)
    h.mission(approve=False, spec=h.program_spec.envelopes[0].template)
    approval = h.owner_event("approve")
    with pytest.raises(PolicyError, match="separate data assessment"):
        h.invoke("approve", h.mission_id, **h.owner_args(approval))


def test_independent_block_still_prevents_acceptance(program):  # noqa: F811
    h = program
    prepare_exploratory_program(h)
    with h.company.db.transaction() as conn:
        task = h.program_store.propose(conn, h.program_id, task_proposal(), actor="researcher_kr")
        h.program_store.assess(conn, h.program_id, task, ready(decision="blocked", coverage=False,
                              point_in_time=False, executable_prices=False), actor="data")
    with pytest.raises(PolicyError, match="prerequisites are unresolved"):
        with h.company.db.transaction() as conn:
            h.program_store.decide(conn, h.program_id, task, {"decision": "accept", "rationale": "Owner chose exploration"}, actor="director")


def test_selection_rechecks_proof_before_creating_a_child(program):  # noqa: F811
    h = program
    packet = prepare_exploratory_program(h)
    with h.company.db.transaction() as conn:
        task = h.program_store.propose(conn, h.program_id, task_proposal(), actor="researcher_kr")
        h.program_store.assess(conn, h.program_id, task, exploratory_assessment(h), actor="data")
    Path(packet["reports"]["limitations.txt"]["path"]).write_text("changed after assessment")
    with pytest.raises(PolicyError, match="file changed"):
        with h.company.db.transaction() as conn:
            h.program_store.decide(conn, h.program_id, task, {"decision": "accept", "rationale": "Original assessment"}, actor="director")


def test_child_retains_policy_inputs_limits_and_unverified_flags(program):  # noqa: F811
    h = program
    prepare_exploratory_program(h)
    task = create_exploratory_task(h)
    snap = h.snapshot()
    assert snap["spec"]["data"] == h.program_spec.envelopes[0].template.data.model_dump(mode="json")
    assert snap["spec"]["search"] == h.strict_spec.envelopes[0].template.search.model_dump(mode="json")
    with h.company.db.transaction() as conn:
        value = conn.execute("SELECT data_assessment FROM research_program_tasks WHERE id=%s", (task,)).fetchone()["data_assessment"]
        assert value["decision"] == "exploratory_only" and value["coverage"] and value["evaluation_prices"]
        assert not any(value[key] for key in ("point_in_time", "executable_prices", "original_conditions"))
        h.program_store.require_authorized(conn, snap)
        _, row, spec = h.program_store.locked(conn, h.program_id)
    packets = load_packets(h.company, row, {e.name: e for e in spec.envelopes})
    assert packets["etf"][0].data_policy == spec.envelopes[0].template.data.policy
    with pytest.raises(PolicyError, match="parallel mission limit"):
        create_exploratory_task(h, title="Second independent exploratory fixture")
