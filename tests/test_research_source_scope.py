"""A hypothesis-only source cannot be promoted by selecting a strict program."""

import pytest
from psycopg.types.json import Jsonb

from quant_company.company import PolicyError
from quant_company.research.mission_contracts import RetrospectiveDataPolicy

from .test_research_programs import program, task_proposal  # noqa: F401


@pytest.mark.parametrize("source_policy", ["legacy_exploratory", "conditional_retrospective"])
@pytest.mark.parametrize("mode", ["exact_replication", "market_transfer", "novel_hypothesis"])
def test_hypothesis_only_source_retains_scope_in_a_strict_program(program, source_policy, mode):  # noqa: F811
    h = program
    metadata = {"data_policy": RetrospectiveDataPolicy(evidence_reports={"proof.json": "1" * 64}).model_dump(mode="json")}
    if source_policy == "conditional_retrospective":
        metadata = {"research_scope": {"result_scope": "conditional_retrospective_development"}}
    with h.company.db.transaction() as conn:
        conn.execute("UPDATE sources SET metadata=%s WHERE id='fixture:baseline'", (Jsonb(metadata),))
    if mode == "novel_hypothesis":
        with h.company.db.transaction() as conn:
            assert h.program_store.propose(conn, h.program_id, task_proposal(mode=mode), actor="researcher_kr")
    else:
        with pytest.raises(PolicyError, match="cannot establish original replication or transfer conditions"):
            with h.company.db.transaction() as conn:
                h.program_store.propose(conn, h.program_id, task_proposal(mode=mode), actor="researcher_kr")
