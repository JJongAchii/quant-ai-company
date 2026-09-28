import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from quant_company.company import fingerprint
from quant_company.contracts import AgentDecision, ArtifactDraft, ProviderFault, ProviderResponse
from quant_company.quant_feed.contracts import QUANT_FEED_AGENT


@pytest.fixture
def qualification(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/qualify_quant_editorial.py"
    spec = importlib.util.spec_from_file_location("qualify_quant_editorial_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Store:
        def __init__(self, company):
            pass

        def authorized(self):
            return True

        def policy(self):
            return "policy-1"

    monkeypatch.setattr(module, "QuantFeedStore", Store)
    monkeypatch.setattr(module, "prompt", lambda bundle, stage: "fixed prompt")
    return module


def company():
    settings = SimpleNamespace(quant_feed_publish_enabled=False, model_provider="codex")
    role = SimpleNamespace(model="model", reasoning_effort="high")
    return SimpleNamespace(settings=settings, roles={QUANT_FEED_AGENT: role})


def seed(path, module, state):
    request_id = "quant-feed-qualify-" + fingerprint(["negative-critic", "fixed prompt"])[:40]
    negative = {
        "document_id": "negative",
        "original_sha256": "negative-hash",
        "draft": {"disposition": "publish"},
    }
    positive = {"document_id": "positive", "original_sha256": "positive-hash", "draft": None}
    call = {
        "case": "negative-critic",
        "stage": "critique",
        "request_id": request_id,
        "document_id": "negative",
        "state": state,
    }
    if state == "returned":
        call["response"] = ProviderResponse(
            request_id=request_id,
            decision=AgentDecision(
                status="complete", say="", artifacts=[ArtifactDraft(title="test", content="{}")]
            ),
        ).model_dump(mode="json")
    path.write_text(
        json.dumps(
            {
                "state": "running",
                "policy": "policy-1",
                "inputs": {"negative": negative, "positive": positive},
                "calls": [call],
            }
        )
    )
    return request_id


def test_uncertain_recorded_call_is_never_reissued(qualification, tmp_path):
    path = tmp_path / "receipt.json"
    seed(path, qualification, "requested")

    class Provider:
        async def run(self, request):
            raise AssertionError("a previously requested call must not be issued again")

    result = asyncio.run(qualification.qualify(company(), "negative", "positive", path, provider=Provider()))
    assert result["state"] == "blocked" and result["fault"] == "uncertain"
    assert (
        asyncio.run(qualification.qualify(company(), "negative", "positive", path, provider=Provider()))
        == result
    )


def test_saved_response_is_validated_without_second_model_call(qualification, monkeypatch, tmp_path):
    path = tmp_path / "receipt.json"
    prior = seed(path, qualification, "returned")
    monkeypatch.setattr(
        qualification,
        "validate",
        lambda response, bundle, stage, *, audit=None: SimpleNamespace(
            disposition="hold", direct_quant_scope=False, substantive_research=False
        ),
    )
    calls = []

    class Provider:
        async def run(self, request):
            calls.append(request.request_id)
            raise ProviderFault("unavailable", "later call unavailable")

    result = asyncio.run(qualification.qualify(company(), "negative", "positive", path, provider=Provider()))
    assert result["state"] == "blocked" and result["fault"] == "unavailable"
    assert result["calls"][0]["state"] == "validated"
    assert calls and prior not in calls
