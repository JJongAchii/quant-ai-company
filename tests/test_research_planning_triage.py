"""Planning triage on real PostgreSQL; synthetic programs, packets and employees. No model calls."""

from quant_company.research.programs import (
    BUDGET_EXHAUSTED,
    CONDITIONAL_SOURCE,
    DUPLICATE_TASK,
    MISSING_PACKET,
    ProgramStore,
    proposal_mode_error,
)
from quant_company.research.scientific_lineages import LINEAGE_EXHAUSTED

from .test_research_conditional import conditional  # noqa: F401
from .test_research_programs import program  # noqa: F401


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
