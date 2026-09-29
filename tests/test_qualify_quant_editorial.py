import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from quant_company.contracts import AgentDecision, ArtifactDraft, ProviderFault, ProviderResponse
from quant_company.quant_feed.contracts import QUANT_FEED_AGENT
from tests.test_quant_feed import TEXT, brief, critique


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
    request_id = module.qualification_request(company().roles[QUANT_FEED_AGENT], "negative-critic", {},
                                              "critique", "policy-1").request_id
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


@pytest.mark.parametrize("negative_passed", [True, False])
def test_qualification_preserves_draft_and_editorial_budget_after_repair(qualification, tmp_path, negative_passed):
    path = tmp_path / "receipt.json"
    seed(path, qualification, "requested")
    receipt = json.loads(path.read_text())
    receipt["calls"] = []
    for bundle in receipt["inputs"].values():
        bundle.update(pages=[{"location": "PDF p.1", "text": TEXT}], links=[], commercial=False,
                      metadata={"publisher": "Example", "url": "https://example.org/paper"},
                      as_of="2026-09-29", prior=None, previous_critique=None)
    receipt["inputs"]["negative"]["draft"] = brief()
    path.write_text(json.dumps(receipt))
    invalid = brief(evidence=[{"claim": "invented", "location": "PDF p.1", "quote": "Invented exact quotation"}] * 2)
    replies = [critique(disposition="hold", direct_quant_scope=False, substantive_research=False),
               brief(disposition="reject" if negative_passed else "hold"), invalid, brief(),
               critique(disposition="revise", claims_supported=False, issues=["idea: qualify"]),
               brief(), critique(disposition="revise", claims_supported=False, issues=["validation: qualify"]),
               brief(), critique()]
    calls = []

    class Provider:
        async def run(self, request):
            calls.append(request)
            return ProviderResponse(request_id=request.request_id, decision=AgentDecision(
                status="complete", say="", artifacts=[ArtifactDraft(title="test", content=json.dumps(replies.pop(0)))]))

    result = asyncio.run(qualification.qualify(company(), "negative", "positive", path, provider=Provider()))
    assert result["state"] == ("passed" if negative_passed else "not_passed")
    assert result["case_results"]["positive"]["state"] == "passed"
    assert [row["stage"] for row in result["calls"]] == [
        "critique", "review", "review", "repair", "critique", "revision", "critique", "revision", "critique"]
    assert result["editorial_revisions_used"] == 2
    assert len(calls) == 9 and len({r.request_id for r in calls}) == 9
    assert result["calls"][2]["validation_issues"][0]["field"] == "evidence[0]"
    assert all(r.output_contract != "agent_decision" for r in calls)


def test_qualification_identity_binds_model_effort_contract_and_policy(qualification):
    role = company().roles[QUANT_FEED_AGENT]
    first = qualification.qualification_request(role, "case", {}, "review", "policy-1")
    assert first.request_id != qualification.qualification_request(role, "case", {}, "critique", "policy-1").request_id
    assert first.request_id != qualification.qualification_request(role, "case", {}, "review", "policy-2").request_id
    role.reasoning_effort = "max"
    assert first.request_id != qualification.qualification_request(role, "case", {}, "review", "policy-1").request_id


@pytest.mark.parametrize("already_revised", [False, True])
def test_revalidate_settled_layout_failure_without_reissuing_completed_model_calls(qualification, tmp_path, already_revised):
    seed_path, output = tmp_path / "settled.json", tmp_path / "new.json"
    seed(seed_path, qualification, "returned")
    record = json.loads(seed_path.read_text())
    for bundle in record["inputs"].values():
        bundle.update(pages=[{"location": "PDF p.1", "text": "Fixed set-\ntings limit the test. " + TEXT}],
                      links=[], commercial=False, prior=None, previous_critique=None,
                      metadata={"publisher": "Example", "url": "https://example.org/paper"}, as_of="2026-09-29")
    record["inputs"]["negative"]["draft"] = brief()
    record["state"] = "not_passed"
    record["policy"] = "prior-policy"
    record["calls"] = []
    draft = brief()
    draft["evidence"][0] = {"claim": "설정 한계", "location": "PDF p.1", "quote": "Fixed settings limit the test."}
    for index, (label, stage, value) in enumerate([
        ("negative-critic", "critique", critique(disposition="hold", direct_quant_scope=False, substantive_research=False)),
        ("negative-review", "review", brief(disposition="reject")),
        ("positive-revision" if already_revised else "positive-review-repair",
         "revision" if already_revised else "repair", draft),
    ]):
        identity = "quant-feed-old-" + str(index)
        record["calls"].append({"case": label, "stage": stage, "state": "returned", "request_id": identity,
                                "response": ProviderResponse(request_id=identity, decision=AgentDecision(
                                    status="complete", say="", artifacts=[ArtifactDraft(
                                        title="saved", content=json.dumps(value))])).model_dump(mode="json")})
    seed_path.write_text(json.dumps(record))
    called = []

    class Provider:
        async def run(self, request):
            called.append(request.request_id)
            if already_revised:
                verdict = (draft if request.output_contract == "quant_brief_v4" else
                           critique(disposition="revise", claims_supported=False, issues=["material error remains"]))
            else:
                assert request.output_contract == "quant_critique_v2"
                verdict = critique()
            return ProviderResponse(request_id=request.request_id, decision=AgentDecision(
                status="complete", say="", artifacts=[ArtifactDraft(title="test", content=json.dumps(verdict))]))

    result = asyncio.run(qualification.qualify(company(), "negative", "positive", output,
                                               provider=Provider(), reuse=seed_path))
    assert result["state"] == ("not_passed" if already_revised else "passed")
    assert len(called) == (3 if already_revised else 1)
    assert len(result["revalidated_responses"]) == 3
    assert all(row["model_reissued"] is False for row in result["revalidated_responses"])
    assert result["revalidated_responses"][-1]["source_corrections"][0]["kind"] == "pdf_line_wrap_hyphen_match"
    assert result["reuse_receipt_sha256"]
    assert seed_path.read_text() == json.dumps(record)
    record["policy"] = "policy-1"
    seed_path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="changed_policy"):
        asyncio.run(qualification.qualify(company(), "negative", "positive", tmp_path / "repeat.json",
                                         provider=Provider(), reuse=seed_path))
    assert len(called) == (3 if already_revised else 1)  # No repeated judge shopping under an unchanged policy.


@pytest.mark.parametrize("state,call_state", [("blocked", "returned"), ("not_passed", "requested")])
def test_unknown_outcomes_are_never_accepted_for_draft_revalidation(qualification, tmp_path, state, call_state):
    path = tmp_path / "prior.json"
    seed(path, qualification, call_state)
    record = json.loads(path.read_text())
    record["state"] = state
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="only_settled"):
        asyncio.run(qualification.qualify(company(), "negative", "positive", tmp_path / "new.json",
                                         provider=object(), reuse=path))
