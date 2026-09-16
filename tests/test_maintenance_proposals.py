import json

import pytest

from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.maintenance.policy import Triage
from quant_company.maintenance.runner import Maintainer


async def proposal(monkeypatch, payload, sources, *, status="complete"):
    runner = object.__new__(Maintainer)

    async def response(*args, **kwargs):
        return ProviderResponse(request_id="fixture", provider="fixture", decision=AgentDecision(
            say="fixture", status=status, artifacts=[{
                "title": "fixture", "source_ids": sources,
                "content": json.dumps({"finding": None, "reason": "No repair justified by the fixture."}),
            }]))

    monkeypatch.setattr(runner, "response", response)
    return await runner.propose({}, "triage", payload, Triage)


@pytest.mark.parametrize("payload,sources", [
    ({}, []),
    ({"observations": [{"key": "message:observed"}]}, ["message:observed"]),
    ({"history": {"evidence": [{"key": "turn:recorded"}]}}, ["turn:recorded"]),
    ({"evidence_references": ["message:patch-context"]}, ["message:patch-context"]),
])
async def test_proposal_accepts_only_supplied_evidence_on_artifact_envelope(monkeypatch, payload, sources):
    assert (await proposal(monkeypatch, payload, sources)).finding is None


@pytest.mark.parametrize("payload,sources", [
    ({"observations": [{"key": "message:observed"}]}, ["message:invented"]),
    ({"observations": [{"key": "message:redacted", "omitted": True}]}, ["message:redacted"]),
    ({"history": {"evidence": [{"key": "turn:redacted", "omitted": True}]}}, ["turn:redacted"]),
])
async def test_proposal_rejects_unknown_or_omitted_envelope_sources(monkeypatch, payload, sources):
    with pytest.raises(ValueError, match="unknown_maintenance_artifact_source"):
        await proposal(monkeypatch, payload, sources)


async def test_valid_envelope_citation_does_not_bypass_decision_contract(monkeypatch):
    with pytest.raises(ValueError, match="invalid_maintenance_proposal"):
        await proposal(monkeypatch, {"observations": [{"key": "message:observed"}]},
                       ["message:observed"], status="continue")
