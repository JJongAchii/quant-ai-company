"""Real PostgreSQL and process tests; model inference in these tests is simulated."""

import json
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from quant_company.briefing.editor import artifact, prompt
from quant_company.briefing.execution import PHASE_SECONDS, has_runway, phase_deadline, timeout_seconds
from quant_company.briefing.planning import plan_prompt
from quant_company.briefing.quotations import source_spans
from quant_company.contracts import ProviderFault, ProviderRequest, ProviderResponse
from quant_company.providers.client import RuntimeClient
from quant_company.providers.codex_runner import output_schema, request_digest

from .test_briefing import brief, bundle, proposal, response, seed  # noqa: F401
from .test_codex_runtime import fake_codex, runner_for  # noqa: F401

IDENTITY = "news-brief-00000000-0000-0000-0000-000000000001-"


def deadline_bundle():
    return dict(deadline_budget_version=1, fact_inventory_required=True, fact_inventory_version=2,
                combined_editorial_repair=True, editorial_patch_version=2)


def request(phase="write", contract="brief_write_v1"):
    return ProviderRequest(request_id=IDENTITY+phase, model="gpt-6-astra", reasoning_effort="xhigh",
                           prompt="Synthetic original-bound briefing fixture", output_contract=contract)


@pytest.mark.parametrize("phase,contract,seconds", [
    ("plan", "brief_plan_v1", 360), ("write", "brief_write_v1", 1440),
    ("review", "brief_review_v1", 720), ("revise", "brief_source_notes_v1", 360),
    ("final_review", "brief_review_v1", 360),
    ("review", "brief_review_v2", 720), ("final_review", "brief_review_v2", 720),
    ("revise", "brief_editorial_v1", 360), ("revise", "brief_editorial_v2", 960),
])
def test_scoped_budget_preserves_other_and_legacy_turns(fake_codex, phase, contract, seconds):  # noqa: F811
    config, _, _ = fake_codex
    runner = runner_for(config)
    current = request(phase, contract)
    assert timeout_seconds(current) == seconds
    assert runner._timeout_seconds(current) == seconds
    legacy = current.model_copy(update={"output_contract": "agent_decision"})
    assert timeout_seconds(legacy) is None and runner._timeout_seconds(legacy) == config.timeout_seconds


def test_shared_deadline_lends_unused_time_and_reserves_full_review(monkeypatch):
    at = datetime(2026, 10, 9, 6, 30, tzinfo=UTC)
    due = at+timedelta(minutes=75)
    data = deadline_bundle()
    plan_end = phase_deadline('plan', data, due)
    assert (plan_end-at).total_seconds() == 600  # 85-minute window minus downstream work and send margin.
    current = request('plan', 'brief_plan_v1').model_copy(update={'brief_deadline_unix':int(plan_end.timestamp())})
    monkeypatch.setattr('quant_company.briefing.execution.wall_time', lambda: at.timestamp())
    assert timeout_seconds(current) == 600  # The former 360-second kill timer is not used.
    after_fast_plan = at+timedelta(minutes=3)
    after_slow_plan = at+timedelta(minutes=8)
    inventory_end = phase_deadline('inventory', data, due)
    assert (inventory_end-after_fast_plan).total_seconds() == 25*60
    assert (inventory_end-after_slow_plan).total_seconds() == 20*60
    assert has_runway('inventory', data, after_slow_plan, due)
    assert phase_deadline('final_review', data, due) == due+timedelta(minutes=9)
    assert phase_deadline('revise', data, due) == due-timedelta(minutes=3)
    assert phase_deadline('plan', {}, due) is None


def test_frozen_deadline_cannot_be_extended_by_queueing_or_replay(monkeypatch):
    current = request('plan', 'brief_plan_v1').model_copy(update={'brief_deadline_unix':2000000600})
    original_digest = request_digest(current)
    for now, expected in ((2000000000,600),(2000000480,120)):
        monkeypatch.setattr('quant_company.briefing.execution.wall_time', lambda now=now: now)
        assert timeout_seconds(current) == expected
        assert request_digest(current) == original_digest
    monkeypatch.setattr('quant_company.briefing.execution.wall_time', lambda: 2000000600)
    with pytest.raises(ProviderFault, match='deadline has passed'):
        timeout_seconds(current)
    assert request_digest(current.model_copy(update={'brief_deadline_unix':2000000900})) != original_digest


def test_absent_deadline_preserves_legacy_digest_and_rejects_other_lanes():
    import hashlib

    legacy = request()
    material = legacy.model_dump(exclude={'brief_deadline_unix','session','web_search'})
    expected = hashlib.sha256(json.dumps(material,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    assert request_digest(legacy) == expected
    with pytest.raises(ValueError, match='scoped Analyst'):
        ProviderRequest.model_validate(legacy.model_dump() | {'output_contract':'agent_decision','brief_deadline_unix':2000000000})


def test_real_postgres_plan_freezes_absolute_deadline_and_does_not_restart_it(brief):  # noqa: F811
    from psycopg.types.json import Jsonb

    from .test_briefing_planning import planning_bundle

    store, clock = brief
    store.company.settings.briefing_source_notes_enabled = True
    edition = seed(brief)
    with store.db.transaction() as conn:
        conn.execute('UPDATE brief_editions SET bundle=%s WHERE id=%s', (Jsonb(planning_bundle()),edition.id))
    first = store.prepare()['request']
    assert first['brief_deadline_unix'] == int((edition.cutoff+timedelta(minutes=10)).timestamp())
    clock['at'] += timedelta(minutes=7)
    assert store.prepare()['request'] == first


async def test_expired_absolute_deadline_starts_no_http_call(monkeypatch):
    called = []
    async def handle(req):
        called.append(req)
        raise AssertionError('Expired request must not invoke a model')
    client = RuntimeClient('http://runtime.private','synthetic-token',960,transport=httpx.MockTransport(handle))
    monkeypatch.setattr('quant_company.briefing.execution.wall_time',lambda:2000000001)
    current = request('plan','brief_plan_v1').model_copy(update={'brief_deadline_unix':2000000000})
    with pytest.raises(ProviderFault,match='deadline has passed'):
        await client.run(current)
    assert not called


@pytest.mark.parametrize("updates", [
    {"request_id": "company-turn"}, {"request_id": IDENTITY+"plan"}, {"web_search": True},
    {"request_id": "news-brief-not-a-canonical-uuid-write"},
    {"session": {"task_id": "00000000-0000-0000-0000-000000000001", "turn_index": 0}},
])
def test_direct_brief_shape_cannot_enable_tools_or_cross_phase(updates):
    with pytest.raises(ValueError):
        ProviderRequest.model_validate(request().model_dump() | updates)


async def test_direct_json_reaches_real_postgres_without_nested_strings(fake_codex, brief):  # noqa: F811
    store, _ = brief
    store.company.settings.model_provider = "codex"
    edition = seed(brief)
    prepared = ProviderRequest.model_validate(store.prepare()["request"])
    assert prepared.output_contract == "brief_write_v1"
    with store.db.transaction() as conn:
        frozen = conn.execute("SELECT bundle FROM brief_editions WHERE id=%s", (edition.id,)).fetchone()["bundle"]
    reference, exact = source_spans(frozen["documents"][0])[0]
    value = proposal().model_dump(mode="json")
    value["summary"][0]["evidence"][0]["quote"] = reference
    config, configure, calls = fake_codex
    configure(direct=value)
    runner = runner_for(config)
    actual = await runner.run(prepared)
    assert json.loads(actual.decision.artifacts[0].content) == value
    assert not actual.decision.tools and not actual.decision.messages and not actual.decision.delegations
    assert store.commit(actual)["state"] == "completed"
    with store.db.transaction() as conn:
        row = conn.execute("SELECT proposal,bundle FROM brief_editions WHERE id=%s", (edition.id,)).fetchone()
    assert row["proposal"]["summary"][0]["evidence"][0]["quote"] == exact
    assert row["bundle"]["documents"] == frozen["documents"]
    assert await runner.run(prepared) == actual and len(calls()) == 1
    assert "decision_json" not in calls()[0]["schema"]["properties"]
    assert "field decision_json" not in calls()[0]["stdin"]
    assert 'web_search="disabled"' in calls()[0]["args"]
    with pytest.raises(ProviderFault, match="different input"):
        await runner.run(prepared.model_copy(update={"reasoning_effort": "xhigh"}))


@pytest.mark.parametrize("value", [{"say": "write", "tools": []}, [], {"summary": [], "arbitrary": "field"}])
async def test_direct_transport_rejects_actions_and_unknown_fields(fake_codex, value):  # noqa: F811
    config, configure, _ = fake_codex
    configure(direct=value)
    with pytest.raises(ProviderFault, match="brief_contract:invalid_shape"):
        await runner_for(config).run(request())


def test_direct_transport_keeps_compact_quote_validation_at_original_owner():
    schema = output_schema(request())
    assert schema["$defs"]["Evidence"]["properties"]["quote"]["minLength"] == 1
    p = proposal().model_dump(mode="json")
    p["summary"][0]["evidence"][0]["quote"] = "@q:999"
    b = bundle() | {"quote_reference_version": 1}
    with pytest.raises(ValueError, match="unknown_or_wrong_source_quote_reference"):
        artifact(ProviderResponse.model_validate(response({"request_id": "fixture-write"}, proposal()).model_dump() | {
            "decision": {"status": "complete", "say": "", "artifacts": [{
                "title": "brief", "content": json.dumps(p)}]}}), type(proposal()), b)


@pytest.mark.parametrize("value", ["0", "-0", "+0", "007.50", "-0.125", ".25", "-.25", "4.", "987654321.0123456789"])
def test_brief_decimal_wire_preserves_valid_price_strings(value):
    schema = output_schema(request())
    fields = schema["$defs"]["MarketObservation"]["properties"]
    for name in ("value", "previous_value", "reported_change"):
        choices = fields[name]["anyOf"]
        string = next(choice for choice in choices if choice.get("type") == "string")
        assert "(?" not in string["pattern"].replace("(?:", "")
        assert re.fullmatch(string["pattern"], value)
        assert any(choice.get("type") == "number" for choice in choices)


@pytest.mark.parametrize("value", ["", "+", "-", ".", "+.", "NaN", "Infinity", "1,234", "12 USD", "1.2.3"])
def test_brief_decimal_wire_rejects_non_prices_before_inference(value):
    fields = output_schema(request())["$defs"]["MarketObservation"]["properties"]
    pattern = next(choice["pattern"] for choice in fields["value"]["anyOf"] if choice.get("type") == "string")
    assert not re.fullmatch(pattern, value)


@pytest.mark.parametrize("status,at,accepted", [
    ("time_unconfirmed", None, True), ("scheduled", "2026-10-08T00:00:00+09:00", True),
    ("time_unconfirmed", "2026-10-08T00:00:00+09:00", False), ("scheduled", None, False),
])
def test_calendar_wire_and_domain_agree_on_unknown_time(status, at, accepted):
    from quant_company.briefing.contracts import CalendarEvent

    event = {"id": "calendar-fixture", "title": "Synthetic calendar fixture", "at": at,
             "source_timezone": "Asia/Seoul", "status": status, "note": "Fixture only",
             "evidence": [{"source_id": "fixture", "quote": "Synthetic source quote"}]}
    branches = output_schema(request())["$defs"]["CalendarEvent"]["anyOf"]
    admitted = any(status in branch["properties"]["status"]["enum"]
                   and ((at is None) == (branch["properties"]["at"]["type"] == "null")) for branch in branches)
    assert admitted == accepted
    if accepted:
        CalendarEvent.model_validate(event)
    else:
        with pytest.raises(ValueError, match="unknown_event_time_must_be_explicit"):
            CalendarEvent.model_validate(event)


def test_write_refused_if_independent_review_cannot_finish(brief):  # noqa: F811
    store, clock = brief
    edition = seed(brief)
    clock["at"] = edition.due_at-timedelta(minutes=10)
    result = store.prepare()
    assert result == {"state": "blocked", "reason": "insufficient_review_time"}
    with store.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM brief_calls WHERE edition_id=%s", (edition.id,)).fetchone()["n"] == 0


def test_frozen_effective_model_cannot_change_between_write_and_review(brief):  # noqa: F811
    store, _ = brief
    edition = seed(brief)
    store.company.roles["market_brief"].reasoning_effort = "xhigh"
    # The previously registered policy must be discarded rather than mixed with xhigh.
    assert store.prepare()["state"] == "idle"
    with store.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM brief_calls WHERE edition_id=%s", (edition.id,)).fetchone()["n"] == 0


async def test_http_wait_budget_outlasts_the_brief_process_only():
    observed = []
    async def handle(req):
        observed.append(req.extensions["timeout"]["read"])
        value = response(json.loads(req.content)).model_dump(mode="json")
        value["provider"] = "codex"
        return httpx.Response(200, json=value)

    client = RuntimeClient("http://runtime.private", "synthetic-token", 960,
                           transport=httpx.MockTransport(handle))
    await client.run(request())
    await client.run(request().model_copy(update={"output_contract": "agent_decision"}))
    assert observed == [PHASE_SECONDS["write"]+60, 960]


def test_direct_prompt_keeps_full_originals_and_removes_obsolete_envelope():
    b = bundle()
    direct = prompt(b, "write", direct_output=True)
    data = json.loads(direct.split("BRIEF DATA JSON:\n")[1])
    assert data["documents"][0]["content"] == b["documents"][0]["content"]
    assert "AgentDecision(" not in direct and "\nSCHEMA:\n" not in direct
    assert "BriefProposal JSON object directly" in direct
    plan = plan_prompt(b | {"candidate_documents": b["documents"]}, direct_output=True)
    assert "SourcePlan JSON object directly" in plan and "AgentDecision(" not in plan


def test_new_editions_reserve_one_correction_and_two_full_reviews_before_deadline():
    from quant_company.briefing.execution import output_contract, remaining_seconds
    from quant_company.briefing.schedule import PREPARATION_MINUTES

    b = {'combined_editorial_repair': True, 'fact_inventory_required': True, 'fact_inventory_version': 2}
    assert remaining_seconds('plan', b) == 70*60
    assert remaining_seconds('review', b) == 30*60
    assert remaining_seconds('revise', b) == 18*60
    assert remaining_seconds('final_review', b) == 12*60
    assert remaining_seconds('plan', b)+60 < (PREPARATION_MINUTES+10)*60
    assert output_contract('final_review', b) == 'brief_review_v2'
    assert output_contract('final_review', {}) == 'brief_review_v1'

    # Full editorial correction reads the same originals as composition; the
    # old six-minute budget remains frozen only for prior v1 requests.
    b['editorial_patch_version'] = 2
    assert remaining_seconds('plan', b) == 80*60
    assert remaining_seconds('revise', b) == 28*60
    assert remaining_seconds('plan', b)+60 < (PREPARATION_MINUTES+10)*60
    b['revision_feedback'] = {'repair_mode': 'editorial_patch'}
    assert output_contract('revise', b) == 'brief_editorial_v2'
    del b['editorial_patch_version']
    assert output_contract('revise', b) == 'brief_editorial_v1'
