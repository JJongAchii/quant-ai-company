"""The signal qualification script's refusal guard and shape. No real model is called."""

import asyncio
import importlib.util
from datetime import date
from pathlib import Path

import pytest

from quant_company.briefing.contracts import BRIEFER
from quant_company.contracts import AgentDecision, ArtifactDraft, ProviderResponse, Role


@pytest.fixture
def script():
    path = Path(__file__).resolve().parents[1] / "scripts/qualify_signals.py"
    spec = importlib.util.spec_from_file_location("qualify_signals_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def outbox(company):
    with company.db.transaction() as conn:
        return conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"]


def run(script, company, output, provider=None):
    return asyncio.run(script.qualify(company, {"quotes": {}, "errors": {}}, "edition", "intraday",
                                      date(2026, 11, 16), output, provider=provider))


class Boom:
    async def run(self, request):
        raise AssertionError("a refused qualification must not reach the model")


def test_qualification_refuses_live_or_fixture(script, company, tmp_path):
    output = tmp_path / "receipt.json"
    with pytest.raises(ValueError, match="qualification_requires_codex_preview"):
        run(script, company, output, Boom())  # the fixture provider
    company.settings.model_provider = "codex"
    company.settings.analyst_signals_enabled = True
    with pytest.raises(ValueError, match="qualification_requires_codex_preview"):
        run(script, company, output, Boom())
    assert not output.exists()
    assert outbox(company) == 0


def test_qualification_records_a_valid_proposal_without_the_outbox(script, company, tmp_path):
    company.settings.model_provider = "codex"
    company.roles[BRIEFER] = Role(id=BRIEFER, name="Analyst", mission="Fixture", model="gpt-6-astra",
                                  reasoning_effort="high", instructions="Role text", tools=[], can_delegate_to=[], active=True)
    source = "signal:2026-11-16:intraday:snapshot"
    content = ('{"kind":"intraday","session_day":"2026-11-16","direction":"long","confidence":55,'
               f'"reasons":[{{"text":"근거","source_ids":["{source}"]}}],"invalidation":"조건","source_ids":["{source}"]}}')

    class Answer:
        async def run(self, request):
            assert request.request_id.startswith("signal-qualify-") and request.output_contract == "agent_decision"
            return ProviderResponse(request_id=request.request_id, decision=AgentDecision(
                status="complete", say="done", artifacts=[ArtifactDraft(title="signal", content=content,
                                                                         source_ids=[])]))

    receipt = run(script, company, tmp_path / "receipt.json", Answer())
    assert receipt["state"] == "valid", receipt.get("error")
    assert receipt["real_model"] is True
    assert receipt["proposal"]["direction"] == "long" and outbox(company) == 0
