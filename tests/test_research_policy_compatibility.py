"""Keep separately signed exploratory and scoped policies distinct after integration."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from quant_company.company import fingerprint
from quant_company.research.data_evidence import DataEvidencePacket
from quant_company.research.mission_contracts import MissionSpec, RetrospectiveDataPolicy
from quant_company.research.program_contracts import DataAssessment, ResearchProgram

from .test_research_conditional import conditional, conditional_ready  # noqa: F401
from .test_research_programs import program  # noqa: F401


def legacy_policy():
    return RetrospectiveDataPolicy(evidence_reports={"limitations.txt": "1" * 64}).model_dump(mode="json")


def test_frozen_exploratory_candidate_encoding_and_identity_remain_exact():
    path = Path("docs/project/evidence/etf-exploration-20260930/candidate-program.json")
    raw = json.loads(path.read_bytes())
    parsed = ResearchProgram.model_validate(raw).model_dump(mode="json")
    assert parsed == raw
    assert fingerprint(parsed) == fingerprint(raw)


def test_scoped_mission_cannot_carry_both_owner_policy_contracts(conditional):  # noqa: F811
    raw = conditional.program_spec.envelopes[0].template.model_dump(mode="json")
    raw["data"]["policy"] = legacy_policy()
    with pytest.raises(ValidationError, match="cannot mix a legacy exploratory policy"):
        MissionSpec.model_validate(raw)


@pytest.mark.parametrize("extra", [
    {"data_policy_digest": "1" * 64}, {"evaluation_prices": True}, {"decision": "exploratory_only"},
])
def test_legacy_assessment_fields_cannot_replace_scoped_admission(conditional, extra):  # noqa: F811
    with pytest.raises(ValidationError, match="cannot carry a legacy exploratory"):
        DataAssessment.model_validate(conditional_ready(conditional) | extra)


def test_scoped_evidence_cannot_carry_a_legacy_exploratory_policy(conditional):  # noqa: F811
    raw = conditional.packet.model_dump(mode="json") | {"data_policy": legacy_policy()}
    with pytest.raises(ValidationError, match="cannot carry a legacy exploratory policy"):
        DataEvidencePacket.model_validate(raw)
