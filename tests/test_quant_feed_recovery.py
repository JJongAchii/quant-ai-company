"""Real PostgreSQL private-qualification checks; the model is explicitly simulated."""

import importlib.util
import json
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

from quant_company.contracts import ProviderFault, WebSearchEvent

from .test_quant_feed import brief, critique, original, quant, response  # noqa: F401


@pytest.mark.parametrize("fault", [None, "uncertain"])
async def test_private_recovery_qualification_is_read_only_and_does_not_replay_faults(quant, monkeypatch, tmp_path, fault):  # noqa: F811
    path = Path(__file__).resolve().parents[1] / "scripts/qualify_quant_recovery.py"
    spec = importlib.util.spec_from_file_location("quant_recovery_qualification", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ids = [original(quant, suffix)["document_id"] for suffix in ("-stalled", "-negative", "-positive")]
    with quant.db.transaction() as conn:
        conn.execute("UPDATE quant_feed_documents SET brief=%s", (Jsonb(brief()),))
    for name, identity in zip(("STALLED", "NEGATIVE", "POSITIVE"), ids, strict=True):
        monkeypatch.setattr(module, name, identity)
    quant.company.settings.model_provider = "codex"
    monkeypatch.setattr(module, "Company", lambda _: quant.company)
    monkeypatch.setattr(module, "Settings", lambda: None)
    calls = []

    class Provider:
        async def run(self, request):
            calls.append(request)
            if fault:
                raise ProviderFault(fault, "Simulated ambiguous invocation")
            if request.output_contract == "quant_search_v1":
                result = response({"request": request.model_dump()}, {"results": []})
                result.web_searches = [WebSearchEvent(id="search", action={"type": "search"})]
                return result
            return response({"request": request.model_dump()}, critique(
                disposition="hold", direct_quant_scope=False, substantive_research=False
            ) if len(calls) == 2 else critique())

    monkeypatch.setattr(module, "provider_for", lambda _: Provider())
    output = tmp_path / "private-qualification.json"
    if fault:
        with pytest.raises(SystemExit):
            await module.qualify(output)
    else:
        await module.qualify(output)
    receipt = json.loads(output.read_text())
    assert receipt["state"] == ("blocked" if fault else "passed")
    assert receipt["slack_writes"] is False and receipt["document_writes"] is False
    assert receipt["account_ledger_writes"] is False  # This fixture has no live account router.
    assert len(calls) == (1 if fault else 3)
    count = len(calls)
    with pytest.raises(ValueError, match="existing_qualification_requires_reconciliation"):
        await module.qualify(output)
    assert len(calls) == count
    with quant.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM quant_feed_calls").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM quant_feed_documents WHERE state='ready'").fetchone()["n"] == 3
