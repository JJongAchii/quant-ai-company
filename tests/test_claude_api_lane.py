import asyncio
import json
import logging
import os
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from quant_company.contracts import ProviderFault, ProviderRequest
from quant_company.providers.claude_api import (
    WINDOW,
    ApiNotExecuted,
    Budget,
    body,
    call,
    cost_usd,
    max_tokens,
    read_key,
    reservation_usd,
)
from quant_company.providers.claude_runner import (
    API_FIRST,
    ClaudeConfig,
    ClaudeRequest,
    ClaudeRunner,
    runner_from_environment,
)
from quant_company.providers.codex_runner import ProcessRunner

from .newsletter_fixtures import v5_draft
from .test_claude_runtime import fake_claude  # noqa: F401 - the fake CLI fixture drives the subscription path


def test_cost_uses_all_four_token_kinds():
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
             "cache_creation_input_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000}
    assert cost_usd("claude-opus-5-5", usage) == pytest.approx(4 + 20 + 5 + 0.20)
    assert cost_usd("claude-opus-5", usage) == pytest.approx(5 + 25 + 6.25 + 0.50)


def test_max_tokens_per_contract():
    assert max_tokens("video_shorts_v1") == 128000
    for contract in ("newsletter_v1", "video_performance_v1", "video_answer_v1", "agent_decision"):
        assert max_tokens(contract) == 64000


def test_reservation_is_input_estimate_plus_full_max_tokens():
    request = ProviderRequest(request_id="video-shorts-1", model="claude-opus-5-5", prompt="가" * 1000,
                              output_contract="video_shorts_v1")
    assert reservation_usd(request) == pytest.approx((3000 // 2) * 4 / 1e6 + 128000 * 20 / 1e6)


def test_reservations_never_cross_the_cap_in_31_days(tmp_path):
    now = [1_000_000.0]
    budget = Budget(tmp_path, 10.0, clock=lambda: now[0])
    assert budget.reserve("a", "claude-opus-5-5", 6.0, "api_first")
    assert not budget.reserve("b", "claude-opus-5-5", 5.0, "api_first")  # 6 + 5 > 10
    budget.settle("a", 1.0)
    assert budget.reserve("b", "claude-opus-5-5", 5.0, "fallback") and budget.spent() == pytest.approx(6.0)
    now[0] += WINDOW + 1
    assert budget.spent() == 0 and budget.reserve("c", "claude-opus-5-5", 9.5, "api_first")


def test_release_returns_the_reservation(tmp_path):
    budget = Budget(tmp_path, 10.0)
    assert budget.reserve("a", "claude-opus-5-5", 6.0, "api_first")
    budget.release("a")
    assert budget.spent() == 0


def test_zero_cap_reserves_nothing(tmp_path):
    assert not Budget(tmp_path, 0).reserve("a", "claude-opus-5-5", 0.01, "api_first")


def test_directory_is_private(tmp_path):
    Budget(tmp_path / "lane", 10.0).reserve("a", "claude-opus-5-5", 1.0, "api_first")
    assert (tmp_path / "lane").stat().st_mode & 0o777 == 0o700


def test_corrupt_ledger_fails_closed(tmp_path):
    (tmp_path / "ledger.json").write_text("{not json")
    budget = Budget(tmp_path, 190.0)
    assert not budget.reserve("a", "claude-opus-5-5", 0.01, "api_first")
    budget.write_status(enabled=True)
    assert json.loads((tmp_path / "status.json").read_text())["state"] == "ledger_unreadable"
    assert (tmp_path / "ledger.json").read_text() == "{not json"


def entry(**changes):
    return {"request_id": "x#0", "model": "claude-opus-5-5", "route": "api_first", "at": 1000.0, "reserved": 1.0,
            "actual": None, **changes}


@pytest.mark.parametrize("broken", [
    {"entries": [{"request_id": "x"}]},
    {"entries": [], "pauses": {"key": "soon"}},
    {"entries": [entry(route="elsewhere")]},
    {"entries": [entry(request_id=7)]},
    {"entries": [entry(model=None)]},
    {"entries": [entry(at="1000")]},
    {"entries": [entry(at=float("nan"))]},
    {"entries": [entry(reserved=True)]},
    {"entries": [entry(reserved=-1.0)]},
    {"entries": [entry(actual="0.5")]},
    {"entries": [{k: v for k, v in entry().items() if k != "actual"}]},
    {"entries": ["x#0"]},
    {"entries": [], "pauses": {"uncertain": float("inf")}},
    {"entries": [], "pauses": ["key"]},
    ["entries"],
])
def test_a_ledger_of_the_wrong_shape_fails_closed(tmp_path, broken):
    raw = json.dumps(broken)
    (tmp_path / "ledger.json").write_text(raw)
    budget = Budget(tmp_path, 190.0, clock=lambda: 2000.0)
    assert budget.load() is None and budget.paused() is None and budget.spent() == 0
    assert not budget.reserve("a#0", "claude-opus-5-5", 0.01, "api_first")
    budget.settle("x#0", 0.5)
    budget.release("x#0")
    budget.pause("uncertain", 3800.0)  # held in memory; the unreadable file stays as it was
    assert budget.paused() == {"reason": "uncertain", "until": 3800.0}
    budget.write_status(enabled=True)
    assert json.loads((tmp_path / "status.json").read_text())["state"] == "ledger_unreadable"
    assert (tmp_path / "ledger.json").read_text() == raw


def test_a_well_formed_ledger_loads(tmp_path):
    kept = {"entries": [entry(), entry(request_id="y#0", route="fallback", at=1500, reserved=2, actual=0.5, extra=1)],
            "pauses": {"uncertain": 3000, "key": None}}
    (tmp_path / "ledger.json").write_text(json.dumps(kept))
    budget = Budget(tmp_path, 190.0, clock=lambda: 2000.0)
    assert budget.load() == kept and budget.spent() == pytest.approx(1.5)
    assert budget.paused() == {"reason": "key", "until": None}


def test_pauses_persist_except_key_pauses(tmp_path):
    now = [1000.0]
    budget = Budget(tmp_path, 190.0, clock=lambda: now[0])
    budget.pause("uncertain", 1000.0 + 1800)
    budget.pause("key", None, persist=False)
    assert budget.paused()["reason"] == "key"   # first matching active pause reported
    again = Budget(tmp_path, 190.0, clock=lambda: now[0])  # a restart forgets the key pause only
    assert again.paused() == {"reason": "uncertain", "until": 2800.0}
    now[0] = 2801.0
    assert again.paused() is None


def test_status_file_shows_spend_cap_pause_and_routes(tmp_path):
    budget = Budget(tmp_path, 190.0, clock=lambda: 5000.0)
    budget.reserve("a", "claude-opus-5-5", 1.0, "api_first")
    budget.settle("a", 0.5)
    budget.reserve("b", "claude-opus-5-5", 1.0, "fallback")
    budget.settle("b", 0.25)
    budget.write_status(enabled=True)
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["state"] == "on" and status["cap_usd"] == 190.0
    assert status["spent_31d_usd"] == pytest.approx(0.75)
    assert status["calls_31d"] == {"api_first": 1, "fallback": 1}
    assert status["last_fallback_at"] == 5000.0 and status["paused"] is None
    assert status["key_present"] is None and status["last_refusal"] is None  # only the runner knows these
    budget.write_status(enabled=True, key_present=False, last_refusal={"reason": "rejected", "at": 4000.0})
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["key_present"] is False and status["last_refusal"] == {"reason": "rejected", "at": 4000.0}


def test_status_states_off_and_paused(tmp_path):
    budget = Budget(tmp_path, 190.0, clock=lambda: 10.0)
    budget.write_status(enabled=False)
    assert json.loads((tmp_path / "status.json").read_text())["state"] == "off"
    budget.pause("rate_limit", 100.0)
    budget.write_status(enabled=True)
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["state"] == "paused" and status["paused"] == {"reason": "rate_limit", "until": 100.0}
    Budget(tmp_path / "z", 0).write_status(enabled=True)
    assert json.loads((tmp_path / "z" / "status.json").read_text())["state"] == "off"


SCHEMA = {"type": "object", "properties": {"say": {"type": "string"}, "n": {"type": "integer"}}}
KEY = "sk-ant-test-secret-123"


def make_request(attachments=0, effort=None, model="claude-opus-5-5"):
    frames = [{"name": f"f{i}.png", "media_type": "image/png", "data": "AAAA"} for i in range(attachments)]
    return ProviderRequest(request_id="video-x", model=model, prompt="hello", reasoning_effort=effort,
                           attachments=frames, output_contract="video_visual_review_v1")


def sse(*events):
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


def message_start(model="claude-opus-5-5"):
    return ("message_start", {"type": "message_start", "message": {"model": model, "usage": {
        "input_tokens": 11, "output_tokens": 1, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 7}}})


def stream_ok(parts=('{"say": "x"', ', "n"', ': 3}'), stop="tool_use", model="claude-opus-5-5"):
    events = [message_start(model), ("ping", {"type": "ping"}),
              ("content_block_start", {"type": "content_block_start", "index": 0,
                                       "content_block": {"type": "thinking", "thinking": ""}}),
              ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                       "delta": {"type": "thinking_delta", "thinking": "hm"}}),
              ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                       "delta": {"type": "signature_delta", "signature": "sig"}}),
              ("content_block_stop", {"type": "content_block_stop", "index": 0}),
              ("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {
                  "type": "tool_use", "id": "t1", "name": "StructuredOutput", "input": {}}})]
    events += [("content_block_delta", {"type": "content_block_delta", "index": 1,
                                        "delta": {"type": "input_json_delta", "partial_json": part}}) for part in parts]
    events += [("content_block_stop", {"type": "content_block_stop", "index": 1}),
               ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop},
                                  "usage": {"output_tokens": 1234}}),
               ("message_stop", {"type": "message_stop"})]
    return sse(*events)


def transport_for(content=b"", status=200, headers=None, seen=None, payload=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        if payload is not None:
            return httpx.Response(status, json=payload, headers=headers or {})
        return httpx.Response(status, content=content, headers={"content-type": "text/event-stream", **(headers or {})})
    return httpx.MockTransport(handler)


async def test_body_carries_model_effort_tool_and_images_in_order():
    sent = body(make_request(attachments=2, effort="xhigh"), SCHEMA, "Title")
    assert sent["model"] == "claude-opus-5-5" and sent["stream"] is True and sent["max_tokens"] == 128000
    assert sent["output_config"] == {"effort": "xhigh"}
    assert sent["tools"] == [{"name": "StructuredOutput", "description": "Title", "input_schema": SCHEMA}]
    assert sent["tool_choice"] == {"type": "auto"}
    assert [block["type"] for block in sent["messages"][0]["content"]] == ["image", "image", "text"]
    assert sent["messages"][0]["content"][0]["source"] == {"type": "base64", "media_type": "image/png", "data": "AAAA"}
    assert "cache_control" not in json.dumps(sent) and "thinking" not in sent
    assert body(make_request(), SCHEMA, "T")["output_config"] == {"effort": "high"}


async def test_thinking_then_split_tool_input_is_assembled():
    seen = []
    tool_input, usage = await call(make_request(), SCHEMA, "T", KEY, transport=transport_for(stream_ok(), seen=seen))
    assert tool_input == {"say": "x", "n": 3}
    assert usage == {"input_tokens": 11, "output_tokens": 1234, "cache_creation_input_tokens": 5,
                     "cache_read_input_tokens": 7, "model": "claude-opus-5-5"}
    assert seen[0].headers["x-api-key"] == KEY and seen[0].headers["anthropic-version"] == "2023-06-01"
    assert str(seen[0].url) == "https://api.anthropic.com/v1/messages"


async def test_text_only_reply_is_invalid_output():
    content = sse(message_start(), ("content_block_start", {"type": "content_block_start", "index": 0,
                                                             "content_block": {"type": "text", "text": ""}}),
                  ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "text_delta", "text": "hi"}}),
                  ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                                     "usage": {"output_tokens": 3}}),
                  ("message_stop", {"type": "message_stop"}))
    with pytest.raises(ProviderFault) as caught:
        await call(make_request(), SCHEMA, "T", KEY, transport=transport_for(content))
    assert caught.value.code == "invalid_output"
    assert caught.value.usage["output_tokens"] == 3 and caught.value.usage["model"] == "claude-opus-5-5"


async def test_max_tokens_stop_is_invalid_output():
    with pytest.raises(ProviderFault) as caught:
        await call(make_request(), SCHEMA, "T", KEY, transport=transport_for(stream_ok(stop="max_tokens")))
    assert caught.value.code == "invalid_output"


async def test_error_event_after_200_is_uncertain():
    content = sse(message_start(), ("error", {"type": "error", "error": {"type": "overloaded_error", "message": KEY}}))
    with pytest.raises(ProviderFault) as caught:
        await call(make_request(), SCHEMA, "T", KEY, transport=transport_for(content))
    assert caught.value.code == "uncertain" and KEY not in caught.value.message


async def test_dropped_stream_is_uncertain():
    with pytest.raises(ProviderFault) as caught:
        await call(make_request(), SCHEMA, "T", KEY, transport=transport_for(stream_ok()[:-60]))
    assert caught.value.code == "uncertain"


async def test_model_mismatch_is_uncertain():
    with pytest.raises(ProviderFault) as caught:
        await call(make_request(), SCHEMA, "T", KEY, transport=transport_for(stream_ok(model="claude-opus-5")))
    assert caught.value.code == "uncertain"


@pytest.mark.parametrize("status,headers,payload,reason,consumed", [
    (429, {"retry-after": "7"}, {"error": {"type": "rate_limit_error"}}, "rate_limit", False),
    (429, {}, {"error": {"type": "rate_limit_error", "details": {"error_code": "enforced_spend_limit_reached"}}},
     "spend_limit", False),
    (529, {}, {"error": {"type": "overloaded_error"}}, "overloaded", False),
    (401, {}, {"error": {"type": "authentication_error"}}, "key", False),
    (500, {}, {"error": {"type": "api_error"}}, "server", True),
    # Every other 5xx may have run as well (only 529 is a plain overload refusal): spent, never sent again.
    (502, {}, {"error": {"type": "api_error"}}, "server", True),
    (503, {}, {"error": {"type": "api_error"}}, "server", True),
    (504, {}, {"error": {"type": "api_error"}}, "server", True),
    (599, {}, {"error": {"type": "api_error"}}, "server", True),
    (400, {}, {"error": {"type": "invalid_request_error"}}, "rejected", False),
    (413, {}, {"error": {"type": "request_too_large"}}, "rejected", False)])
async def test_refusals_before_the_stream_are_not_executed(status, headers, payload, reason, consumed):
    now = datetime(2026, 10, 23, 12, tzinfo=UTC).timestamp()
    with pytest.raises(ApiNotExecuted) as caught:
        await call(make_request(), SCHEMA, "T", KEY, clock=lambda: now,
                   transport=transport_for(status=status, headers=headers, payload=payload))
    error = caught.value
    assert error.reason == reason and error.consumed is consumed
    if reason == "rate_limit":
        assert error.pause == ("rate_limit", now + 7, True)
    elif reason == "spend_limit":
        assert error.pause == ("spend_limit", datetime(2026, 11, 1, tzinfo=UTC).timestamp(), True)
    elif reason == "overloaded":
        assert error.pause == ("overloaded", now + 60, True)
    elif reason == "key":
        assert error.pause == ("key", None, False)
    else:
        assert error.pause is None


@pytest.mark.parametrize("value,wait", [("7", 7), ("0.5", 0.5), (None, 60), ("0", 60), ("-5", 60), ("inf", 60),
                                        ("-inf", 60), ("nan", 60), ("Fri, 23 Oct 2026 07:28:00 GMT", 60),
                                        ("86400", 3600), ("1e308", 3600)])
async def test_retry_after_waits_an_hour_at_most_and_a_bad_value_waits_a_minute(value, wait):
    headers = {} if value is None else {"retry-after": value}
    with pytest.raises(ApiNotExecuted) as caught:
        await call(make_request(), SCHEMA, "T", KEY, clock=lambda: 1000.0, transport=transport_for(
            status=429, headers=headers, payload={"error": {"type": "rate_limit_error"}}))
    assert caught.value.pause == ("rate_limit", 1000.0 + wait, True)


async def test_spend_limit_on_the_first_pauses_to_the_next_month_and_december_rolls_over():
    payload = {"error": {"details": {"error_code": "enforced_spend_limit_reached"}}}
    for day, until in ((datetime(2026, 11, 1, 5, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC)),
                       (datetime(2026, 12, 31, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))):
        with pytest.raises(ApiNotExecuted) as caught:
            await call(make_request(), SCHEMA, "T", KEY, clock=day.timestamp,
                       transport=transport_for(status=429, payload=payload))
        assert caught.value.pause == ("spend_limit", until.timestamp(), True)


def test_key_file_is_trimmed_and_empty_means_no_key(tmp_path):
    path = tmp_path / "key"
    path.write_text("sk-x\n")
    assert read_key(path) == "sk-x"
    path.write_text("  \n")
    assert read_key(path) is None
    path.write_bytes(b"\xff\xfe")  # not text: no key, never an exception
    assert read_key(path) is None
    assert read_key(tmp_path / "missing") is None and read_key(None) is None


async def test_key_appears_only_in_the_header():
    seen, texts = [], []
    for kwargs in ({"content": stream_ok(), "status": 200}, {"payload": {"error": {"type": KEY}}, "status": 500},
                   {"content": sse(message_start(), ("error", {"type": "error", "error": {"message": KEY}}))}):
        try:
            await call(make_request(), SCHEMA, "T", KEY, transport=transport_for(seen=seen, **kwargs))
        except (ProviderFault, ApiNotExecuted) as error:
            texts.append(str(error) + repr(error))
    assert len(seen) == 3 and all(KEY.encode() not in r.content for r in seen)
    assert all(KEY not in text for text in texts)


@pytest.mark.parametrize("error", [httpx.ConnectError("x"), httpx.ConnectTimeout("x"), httpx.PoolTimeout("x")])
async def test_connect_phase_errors_are_not_executed(error):
    def handler(request):
        raise error
    with pytest.raises(ApiNotExecuted) as caught:
        await call(make_request(), SCHEMA, "T", KEY, clock=lambda: 1000.0, transport=httpx.MockTransport(handler))
    assert caught.value.reason == "unreachable" and caught.value.consumed is False
    assert caught.value.pause == ("overloaded", 1060.0, True)


async def test_read_timeout_is_uncertain():
    def handler(request):
        raise httpx.ReadTimeout("x")
    with pytest.raises(ProviderFault) as caught:
        await call(make_request(), SCHEMA, "T", KEY, transport=httpx.MockTransport(handler))
    assert caught.value.code == "uncertain"


async def test_unreadable_error_body_still_maps_by_status():
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise httpx.ReadError("x")
            yield b""

    transport = httpx.MockTransport(lambda request: httpx.Response(529, stream=Broken()))
    with pytest.raises(ApiNotExecuted) as caught:
        await call(make_request(), SCHEMA, "T", KEY, clock=lambda: 1000.0, transport=transport)
    assert caught.value.reason == "overloaded" and caught.value.pause == ("overloaded", 1060.0, True)


# Part C: routing, receipts, fallback and the shared-slot cancel in ClaudeRunner (simulated API, fake CLI).

VIDEO_REVIEW = {"facts": True, "numbers": True, "conditions": True, "coverage": True, "readability": True,
                "concerns": []}
STREAM_USAGE = {"input_tokens": 11, "output_tokens": 1234, "cache_creation_input_tokens": 5,
                "cache_read_input_tokens": 7}
STREAM_COST = cost_usd("claude-opus-5-5", STREAM_USAGE)


def email(n=1):
    return ClaudeRequest(request_id=f"newsletter-lane-{n}", model="claude-opus-5-5", reasoning_effort="high",
                         prompt="DATA: fixture", output_contract="newsletter_v1")


def video(n=1):
    return ProviderRequest(request_id=f"video-lane-{n}-review", model="claude-opus-5-5", reasoning_effort="max",
                           prompt="Plan: fixture", output_contract="video_review_v1")


def ok(tool_input, **changes):
    content = stream_ok(parts=(json.dumps(tool_input)[:7], json.dumps(tool_input)[7:]), **changes)
    return lambda: httpx.Response(200, content=content, headers={"content-type": "text/event-stream"})


def refusal(status, payload=None):
    return lambda: httpx.Response(status, json=payload or {"error": {"type": "api_error"}})


def broken():
    content = sse(message_start(), ("error", {"type": "error", "error": {"type": "overloaded_error"}}))
    return lambda: httpx.Response(200, content=content, headers={"content-type": "text/event-stream"})


class Api:
    """A simulated Messages endpoint: answers in order (the last answer repeats); a gate holds calls open."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.seen = []
        self.gate = None

    async def handler(self, request):
        self.seen.append(request)
        if self.gate is not None:
            await self.gate.wait()
        return (self.answers.pop(0) if len(self.answers) > 1 else self.answers[0])()

    def transport(self):
        return httpx.MockTransport(self.handler)


@pytest.fixture
def lane(fake_claude, tmp_path):  # noqa: F811 - pytest injects the imported fixture
    config, configure, calls = fake_claude
    key = tmp_path / "claude_api_key"
    key.write_text(KEY + "\n")
    return replace(config, api_enabled=True, api_cap_usd=190, api_key_file=key), configure, calls


def make_runner(config, api, clock=time.time):
    return ClaudeRunner(config, source_environment={"PATH": os.environ["PATH"]}, api_transport=api.transport(),
                        clock=clock)


def receipt(config, request):
    return json.loads((config.jobs_dir / f"{request.request_id}.json").read_text())


def tried(config, request):
    return [(a["backend"], a["outcome"]) for a in receipt(config, request)["attempts"]]


def ledger(config):
    return json.loads((config.jobs_dir / ".api" / "ledger.json").read_text())


def status(config):
    return json.loads((config.jobs_dir / ".api" / "status.json").read_text())


def cli_email(configure):
    configure(model="claude-opus-5-5", used_model="claude-opus-5-5", review=v5_draft().model_dump(mode="json"))


def cli_video(configure, **changes):
    configure(model="claude-opus-5-5", used_model="claude-opus-5-5", review=VIDEO_REVIEW, **changes)


async def until(condition):
    for _ in range(500):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


def test_routing_table_is_the_approved_list():
    assert API_FIRST == frozenset({"video_shorts_v1", "newsletter_v1", "video_performance_v1", "video_weekly_v1",
                                   "video_answer_v1", "agent_decision", "video_screen_fix_v1"})


def test_lane_configuration_is_validated_and_read_from_the_environment(tmp_path, monkeypatch):
    for cap in (-1, 1001, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            ClaudeConfig(tmp_path / "a", tmp_path / "j", api_cap_usd=cap)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "auth"))
    monkeypatch.setenv("CLAUDE_JOBS_DIR", str(tmp_path / "jobs"))
    for name in ("CLAUDE_API_ENABLED", "CLAUDE_API_CAP_USD", "CLAUDE_API_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    config = runner_from_environment().config
    assert (config.api_enabled, config.api_cap_usd, config.api_key_file) == (False, 0.0, None)
    monkeypatch.setenv("CLAUDE_API_ENABLED", "true")
    monkeypatch.setenv("CLAUDE_API_CAP_USD", "190")
    monkeypatch.setenv("CLAUDE_API_KEY_FILE", "/run/secrets/claude_api_key")
    config = runner_from_environment().config
    assert (config.api_enabled, config.api_cap_usd) == (True, 190.0)
    assert config.api_key_file == Path("/run/secrets/claude_api_key")
    monkeypatch.setenv("CLAUDE_API_CAP_USD", "lots")
    with pytest.raises(ProviderFault) as fault:
        runner_from_environment()
    assert fault.value.code == "unavailable"


async def test_api_first_contract_runs_on_the_api_and_receipt_says_so(lane):
    from quant_company.newsletter.contracts import NewsletterDraft

    config, _, calls = lane
    api = Api(ok(v5_draft().model_dump(mode="json")))
    runner = make_runner(config, api)
    result = await runner.run(email())
    assert len(api.seen) == 1 and calls() == []
    sent = json.loads(api.seen[0].content)
    assert sent["tools"][0]["input_schema"] == NewsletterDraft.model_json_schema()
    assert sent["output_config"] == {"effort": "high"} and sent["max_tokens"] == 64000
    assert NewsletterDraft.model_validate_json(result.decision.artifacts[0].content) == v5_draft()
    assert result.decision.artifacts[0].title == "Evening brief email" and result.provider == "claude"
    usage = result.usage
    assert {name: usage[name] for name in STREAM_USAGE} == STREAM_USAGE
    assert usage["billing_mode"] == "api_credit" and usage["backend"] == "api"
    assert usage["actual_model"] == "claude-opus-5-5" and usage["effort"] == "high"
    assert usage["effort_evidence"] == "requested_api_parameter" and usage["api_version"] == "2023-06-01"
    assert usage["cost_usd"] == round(STREAM_COST, 6)
    saved = receipt(config, email())
    assert saved["backend"] == "api" and saved["state"] == "complete" and tried(config, email()) == [
        ("api", "complete")]
    assert await runner.run(email()) == result and await make_runner(config, api).run(email()) == result
    assert len(api.seen) == 1 and calls() == []
    entries = ledger(config)["entries"]
    assert [(e["request_id"], e["route"]) for e in entries] == [("newsletter-lane-1#0", "api_first")]
    assert entries[0]["actual"] == pytest.approx(STREAM_COST)
    assert status(config)["state"] == "on" and status(config)["calls_31d"] == {"api_first": 1}


async def test_subscription_first_contract_stays_on_the_cli_while_it_works(lane):
    config, configure, calls = lane
    cli_video(configure)
    api = Api(ok(VIDEO_REVIEW))
    result = await make_runner(config, api).run(video())
    assert not api.seen and len(calls()) == 1
    assert result.usage["billing_mode"] == "subscription" and "backend" not in result.usage
    assert receipt(config, video())["backend"] == "subscription"
    assert tried(config, video()) == [("subscription", "complete")]


@pytest.mark.parametrize("change,outcome,cli_runs", [
    ({"error": "rate_limit"}, "quota", 1),
    ({"auth": {"loggedIn": False}}, "auth", 0),
])
async def test_cli_quota_or_auth_falls_back_to_the_api(lane, change, outcome, cli_runs):
    config, configure, calls = lane
    cli_video(configure, **change)
    api = Api(ok(VIDEO_REVIEW))
    result = await make_runner(config, api).run(video())
    assert len(api.seen) == 1 and len(calls()) == cli_runs
    assert result.usage["billing_mode"] == "api_credit"
    assert result.decision.artifacts[0].title == "Video adaptation review"
    assert tried(config, video()) == [("subscription", outcome), ("api", "complete")]
    assert status(config)["calls_31d"] == {"fallback": 1} and status(config)["last_fallback_at"]


@pytest.mark.parametrize("change", [{"crash": True}, {"incomplete": "error_max_turns"}, {"result_is_error": True}])
async def test_cli_uncertain_or_invalid_never_reaches_the_api(lane, change):
    config, configure, calls = lane
    cli_video(configure, **change)
    api = Api(ok(VIDEO_REVIEW))
    with pytest.raises(ProviderFault) as fault:
        await make_runner(config, api).run(video())
    assert fault.value.code in {"uncertain", "invalid_output"}
    with pytest.raises(ProviderFault):
        await make_runner(config, api).run(video())
    assert not api.seen and len(calls()) == 1


@pytest.mark.parametrize("code,outcome,pause", [(529, "not_executed", "overloaded"), (401, "not_executed", "key"),
                                               (500, "consumed", None), (503, "consumed", None)])
async def test_api_refusal_sends_api_first_work_to_the_subscription(lane, code, outcome, pause):
    config, configure, calls = lane
    cli_email(configure)
    api = Api(refusal(code))
    runner = make_runner(config, api)
    result = await runner.run(email())
    assert result.usage["billing_mode"] == "subscription" and len(api.seen) == 1 and len(calls()) == 1
    saved = receipt(config, email())
    assert saved["backend"] == "subscription" and saved["state"] == "complete"
    assert tried(config, email()) == [("api", outcome), ("subscription", "complete")]
    assert saved["attempts"][0]["status"] == code
    entries = ledger(config)["entries"]
    if outcome == "consumed":  # a 5xx other than 529 may have run: its whole reservation counts
        assert [e["actual"] for e in entries] == [pytest.approx(reservation_usd(email()))]
    else:
        assert entries == []
    assert (status(config)["paused"] or {}).get("reason") == pause
    assert await runner.run(email()) == result and len(api.seen) == 1


async def test_api_uncertain_is_never_replayed_and_pauses_the_lane(lane):
    config, configure, calls = lane
    now = [1_000_000.0]
    api = Api(broken(), ok(v5_draft().model_dump(mode="json")))
    runner = make_runner(config, api, clock=lambda: now[0])
    with pytest.raises(ProviderFault) as fault:
        await runner.run(email())
    assert fault.value.code == "uncertain"
    saved = receipt(config, email())
    assert saved["state"] == "failed" and saved["fault"] == "uncertain" and tried(config, email()) == [
        ("api", "uncertain")]
    assert ledger(config)["entries"][0]["actual"] == pytest.approx(reservation_usd(email()))
    assert status(config)["paused"] == {"reason": "uncertain", "until": now[0] + 1800}
    cli_email(configure)
    for again in (runner, make_runner(config, api, clock=lambda: now[0])):
        with pytest.raises(ProviderFault) as replay:
            await again.run(email())
        assert replay.value.code == "uncertain"
    assert len(api.seen) == 1 and calls() == []
    now[0] += 1799  # still paused: another email goes to the subscription
    assert (await runner.run(email(2))).usage["billing_mode"] == "subscription"
    assert len(api.seen) == 1 and len(calls()) == 1
    now[0] += 2
    assert (await runner.run(email(3))).usage["billing_mode"] == "api_credit"
    assert len(api.seen) == 2


@pytest.mark.parametrize("change", ["disabled", "zero_cap", "no_key_file", "missing_key", "empty_key"])
async def test_disabled_lane_or_zero_cap_or_missing_key_changes_nothing(lane, change, tmp_path):
    config, configure, calls = lane
    config = {"disabled": replace(config, api_enabled=False), "zero_cap": replace(config, api_cap_usd=0),
              "no_key_file": replace(config, api_key_file=None),
              "missing_key": replace(config, api_key_file=tmp_path / "absent"),
              "empty_key": config}[change]
    if change == "empty_key":
        config.api_key_file.write_text("\n")
    cli_email(configure)
    api = Api(ok(v5_draft().model_dump(mode="json")))
    result = await make_runner(config, api).run(email())
    assert result.usage["billing_mode"] == "subscription" and not api.seen and len(calls()) == 1
    assert tried(config, email()) == [("subscription", "complete")]
    if change in {"disabled", "zero_cap"}:
        assert not (config.jobs_dir / ".api").exists()


async def test_cap_reached_sends_api_first_work_to_the_subscription(lane):
    config, configure, calls = lane
    config = replace(config, api_cap_usd=1.0)
    assert reservation_usd(email()) > 1.0
    cli_email(configure)
    api = Api(ok(v5_draft().model_dump(mode="json")))
    result = await make_runner(config, api).run(email())
    assert result.usage["billing_mode"] == "subscription" and not api.seen and len(calls()) == 1
    assert not (config.jobs_dir / ".api" / "ledger.json").exists() or ledger(config)["entries"] == []
    assert status(config)["spent_31d_usd"] == 0


async def test_second_ask_during_api_call_is_pending_not_a_second_call(lane):
    config, _, calls = lane
    api = Api(ok(v5_draft().model_dump(mode="json")))
    api.gate = asyncio.Event()
    runner = make_runner(config, api)
    task = asyncio.create_task(runner.run(email()))
    await until(lambda: api.seen)
    for _ in range(2):
        with pytest.raises(ProviderFault) as pending:
            await runner.run(email())
        assert pending.value.code == "pending" and pending.value.retry_after_seconds == 20
    api.gate.set()
    result = await task
    assert result.usage["billing_mode"] == "api_credit"
    assert await runner.run(email()) == result
    assert len(api.seen) == 1 and calls() == []


async def test_restart_during_api_call_is_uncertain_and_keeps_reservation(lane):
    config, _, calls = lane
    api = Api(ok(v5_draft().model_dump(mode="json")))
    api.gate = asyncio.Event()
    task = asyncio.create_task(make_runner(config, api).run(email()))
    await until(lambda: api.seen)
    saved = receipt(config, email())
    assert saved["state"] == "running" and saved["backend"] == "api"
    restarted = Api(ok(v5_draft().model_dump(mode="json")))
    with pytest.raises(ProviderFault) as fault:
        await make_runner(config, restarted).run(email())
    assert fault.value.code == "uncertain" and not restarted.seen and calls() == []
    assert Budget(config.jobs_dir / ".api", 190).spent() == pytest.approx(reservation_usd(email()))
    # The interrupted call itself: cancelled mid-stream it counts in full; a deliberate cancel pauses nothing.
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert receipt(config, email())["fault"] == "uncertain"
    assert Budget(config.jobs_dir / ".api", 190).spent() == pytest.approx(reservation_usd(email()))
    assert status(config)["paused"] is None
    assert len(api.seen) == 1


async def test_api_call_runs_while_the_cli_slot_is_busy(lane):
    config, configure, calls = lane
    cli_video(configure, delay=10)
    api = Api(ok(v5_draft().model_dump(mode="json")))
    runner = make_runner(config, api)
    held = asyncio.create_task(runner.run(video()))
    await until(calls)
    with pytest.raises(ProviderFault) as busy:
        await make_runner(config, api).run(video(2))
    assert busy.value.code == "busy"  # the shared CLI slot is taken
    result = await runner.run(email())
    assert result.usage["billing_mode"] == "api_credit" and not held.done()
    assert len(calls()) == 1
    await runner.cancel(video().request_id)
    assert held.cancelled()


async def test_shared_only_cancel_keeps_an_api_turn_and_cancels_a_cli_turn(lane):
    config, configure, calls = lane
    api = Api(ok(v5_draft().model_dump(mode="json")))
    api.gate = asyncio.Event()
    runner = make_runner(config, api)
    api_turn = asyncio.create_task(runner.run(email()))
    await until(lambda: api.seen)
    assert await runner.cancel(email().request_id, shared_only=True) == "kept"
    api.gate.set()
    assert (await api_turn).usage["billing_mode"] == "api_credit"
    assert receipt(config, email())["state"] == "complete"
    cli_video(configure, delay=10)
    cli_turn = asyncio.create_task(runner.run(video()))
    await until(calls)
    # As a plain cancel answers for a CLI turn that already started: it is stopped, its outcome unknown.
    assert await runner.cancel(video().request_id, shared_only=True) == "uncertain"
    assert cli_turn.cancelled() and len(api.seen) == 1


async def test_key_never_reaches_the_cli_environment_or_receipts(lane):
    config, configure, calls = lane
    faults = []
    cli_email(configure)
    runner = make_runner(config, Api(ok(v5_draft().model_dump(mode="json")), refusal(529)))
    await runner.run(email())
    await runner.run(email(2))  # refused with 529 -> the subscription
    cli_video(configure, error="rate_limit")
    now = [time.time() + 120]  # past the 529 pause
    runner = make_runner(config, Api(broken()), clock=lambda: now[0])
    with pytest.raises(ProviderFault) as fault:
        await runner.run(video())
    faults.append(str(fault.value) + repr(fault.value))
    assert len(calls()) == 2
    assert all(KEY not in json.dumps(call) for call in calls())
    files = [path for path in config.jobs_dir.rglob("*") if path.is_file()]
    assert len(files) >= 5 and all(KEY not in path.read_text() for path in files)
    assert all(KEY not in text for text in faults)


@pytest.mark.parametrize("answer", [ok({"headline": "x"}), ok(v5_draft().model_dump(mode="json"), stop="max_tokens")])
async def test_invalid_api_output_is_definitive_and_costs_what_it_used(lane, answer):
    config, _, calls = lane
    api = Api(answer)
    runner = make_runner(config, api)
    with pytest.raises(ProviderFault) as fault:
        await runner.run(email())
    assert fault.value.code == "invalid_output"
    with pytest.raises(ProviderFault) as again:
        await make_runner(config, api).run(email())
    assert again.value.code == "invalid_output" and again.value.message == fault.value.message
    assert len(api.seen) == 1 and calls() == []
    assert receipt(config, email())["state"] == "failed" and tried(config, email()) == [("api", "invalid_output")]
    assert ledger(config)["entries"][0]["actual"] == pytest.approx(STREAM_COST)
    assert status(config)["paused"] is None


async def test_deferred_receipt_goes_to_the_api_when_the_lane_is_open(lane):
    config, configure, calls = lane
    now = [time.time()]
    Budget(config.jobs_dir / ".api", 190, clock=lambda: now[0]).pause("rate_limit", now[0] + 60)
    cli_video(configure, error="rate_limit")
    api = Api(ok(VIDEO_REVIEW))
    runner = make_runner(config, api, clock=lambda: now[0])
    with pytest.raises(ProviderFault) as fault:
        await runner.run(video())
    assert fault.value.code == "quota" and receipt(config, video())["state"] == "deferred"
    with pytest.raises(ProviderFault) as still:
        await runner.run(video())
    assert still.value.code == "quota" and not api.seen and len(calls()) == 1
    now[0] += 61
    result = await runner.run(video())
    assert result.usage["billing_mode"] == "api_credit" and len(api.seen) == 1 and len(calls()) == 1
    assert tried(config, video()) == [("subscription", "quota"), ("api", "complete")]


async def test_api_timeout_counts_in_full_pauses_and_is_never_retried(lane):
    config, configure, calls = lane
    config = replace(config, newsletter_timeout_seconds=0.2)
    api = Api(ok(v5_draft().model_dump(mode="json")))
    api.gate = asyncio.Event()
    runner = make_runner(config, api)
    with pytest.raises(ProviderFault) as fault:
        await runner.run(email())
    assert fault.value.code == "timeout"
    saved = receipt(config, email())
    assert saved["state"] == "failed" and saved["fault"] == "timeout" and tried(config, email()) == [
        ("api", "timeout")]
    assert ledger(config)["entries"][0]["actual"] == pytest.approx(reservation_usd(email()))
    assert status(config)["paused"]["reason"] == "uncertain"
    cli_email(configure)
    with pytest.raises(ProviderFault) as again:
        await make_runner(config, api).run(email())
    assert again.value.code == "timeout" and len(api.seen) == 1 and calls() == []


async def test_a_server_error_keeps_the_id_off_the_api_for_good(lane):
    config, configure, calls = lane
    cli_video(configure, error="rate_limit")
    api = Api(refusal(504), ok(VIDEO_REVIEW))
    runner = make_runner(config, api)
    with pytest.raises(ProviderFault) as fault:
        await runner.run(video())
    assert fault.value.code == "quota"  # the CLI's own refusal stands, with its deferred receipt
    saved = receipt(config, video())
    assert saved["state"] == "deferred" and tried(config, video()) == [("subscription", "quota"), ("api", "consumed")]
    for again in range(2):
        if again:
            path = config.jobs_dir / f"{video().request_id}.json"
            path.write_text(json.dumps({**json.loads(path.read_text()), "retry_at": time.time() - 1}))
        with pytest.raises(ProviderFault) as wait:
            await runner.run(video())
        assert wait.value.code == "quota"
    assert len(api.seen) == 1 and len(calls()) == 2


async def test_preflight_auth_with_the_api_refusing_stays_retryable(lane):
    config, configure, calls = lane
    cli_video(configure, auth={"loggedIn": False})
    api = Api(refusal(529))
    runner = make_runner(config, api)
    with pytest.raises(ProviderFault) as fault:
        await runner.run(video())
    assert fault.value.code == "auth" and not calls()
    configure(auth={"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max"})
    result = await runner.run(video())  # the owner logged in again: the same ID runs on the subscription
    assert result.usage["billing_mode"] == "subscription" and len(api.seen) == 1 and len(calls()) == 1
    assert tried(config, video()) == [("subscription", "auth"), ("api", "not_executed"), ("subscription", "complete")]


async def test_at_most_two_api_calls_run_at_once(lane):
    config, _, _ = lane
    api = Api(ok(v5_draft().model_dump(mode="json")))
    api.gate = asyncio.Event()
    runner = make_runner(config, api)
    turns = [asyncio.create_task(runner.run(email(n))) for n in (1, 2, 3)]
    await until(lambda: len(api.seen) == 2)
    await asyncio.sleep(0.05)
    assert len(api.seen) == 2
    with pytest.raises(ProviderFault) as pending:
        await runner.run(email(3))  # waiting for a slot is still this request's turn
    assert pending.value.code == "pending"
    api.gate.set()
    assert all(r.usage["billing_mode"] == "api_credit" for r in await asyncio.gather(*turns))
    assert len(api.seen) == 3


class GatedPreflight(ProcessRunner):
    """The real process boundary, holding `claude --version` (the CLI preflight) until the gate opens."""

    def __init__(self):
        super().__init__()
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()

    async def run(self, argv, **options):
        if argv[1:] == ["--version"] and not self.gate.is_set():
            self.entered.set()
            await self.gate.wait()
        return await super().run(argv, **options)


@pytest.mark.parametrize("second", ["complete", "consumed"])
async def test_a_same_id_ask_during_the_cli_preflight_is_pending_and_never_loses_an_api_record(lane, second):
    config, configure, calls = lane
    cli_email(configure)
    if second == "consumed":
        configure(error="rate_limit")
    # A 400 refuses without a pause, so the lane stays open while the first ask waits in the CLI preflight.
    api = Api(refusal(400), ok(v5_draft().model_dump(mode="json")) if second == "complete" else refusal(500))
    process = GatedPreflight()
    first = ClaudeRunner(config, process_runner=process, source_environment={"PATH": os.environ["PATH"]},
                         api_transport=api.transport())
    task = asyncio.create_task(first.run(email()))
    await process.entered.wait()
    assert tried(config, email()) == [("api", "not_executed")]
    for _ in range(2):
        with pytest.raises(ProviderFault) as pending:
            await first.run(email())
        assert pending.value.code == "pending" and pending.value.retry_after_seconds == 20
    assert len(api.seen) == 1
    # Another runner on the same jobs directory may take the API turn meanwhile; the CLI must not erase it.
    if second == "complete":
        result = await make_runner(config, api).run(email())
    else:
        with pytest.raises(ProviderFault) as busy:
            await make_runner(config, api).run(email())
        assert busy.value.code == "busy"
    process.gate.set()
    if second == "complete":
        assert await task == result and calls() == []
        assert tried(config, email()) == [("api", "not_executed"), ("api", "complete")]
    else:
        with pytest.raises(ProviderFault) as quota:
            await task
        assert quota.value.code == "quota"
        assert tried(config, email()) == [("api", "not_executed"), ("api", "consumed"), ("subscription", "quota")]
        path = config.jobs_dir / f"{email().request_id}.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), "retry_at": time.time() - 1}))
        with pytest.raises(ProviderFault) as again:
            await first.run(email())
        assert again.value.code == "quota" and len(calls()) == 2
    assert len(api.seen) == 2  # the unpaid 400 and exactly one paid call


async def test_cancel_of_a_started_api_turn_is_uncertain_and_a_waiting_one_is_cancelled(lane):
    config, _, calls = lane
    api = Api(ok(v5_draft().model_dump(mode="json")))
    api.gate = asyncio.Event()
    runner = make_runner(config, api)
    turns = [asyncio.create_task(runner.run(email(n))) for n in (1, 2, 3)]
    await until(lambda: len(api.seen) == 2)
    # The third waits for a slot: nothing was sent, so it is released and simply cancelled.
    assert await runner.cancel(email(3).request_id) == "cancelled"
    assert turns[2].cancelled() and receipt(config, email(3))["state"] == "cancelled"
    assert tried(config, email(3)) == [("api", "not_executed")]
    # The first is in its call: it may have been charged, so the answer is uncertain and the receipt stays failed.
    assert await runner.cancel(email(1).request_id) == "uncertain"
    assert turns[0].cancelled()
    saved = receipt(config, email(1))
    assert saved["state"] == "failed" and saved["fault"] == "uncertain" and tried(config, email(1)) == [
        ("api", "uncertain")]
    entries = {e["request_id"]: e for e in ledger(config)["entries"]}
    assert set(entries) == {"newsletter-lane-1#0", "newsletter-lane-2#0"}
    assert entries["newsletter-lane-1#0"]["actual"] == pytest.approx(reservation_usd(email(1)))
    assert status(config)["paused"] is None  # a deliberate cancel is not an API fault: the lane stays open
    api.gate.set()
    assert (await turns[1]).usage["billing_mode"] == "api_credit"
    with pytest.raises(ProviderFault) as again:
        await runner.run(email(1))
    assert again.value.code == "uncertain" and len(api.seen) == 2 and calls() == []
    assert await runner.cancel(email(2).request_id, shared_only=True) == "completed"


async def test_a_turn_cancelled_during_the_cli_preflight_never_falls_back_to_the_api(lane):
    config, configure, calls = lane
    cli_video(configure, auth={"loggedIn": False})
    api = Api(ok(VIDEO_REVIEW))
    process = GatedPreflight()
    runner = ClaudeRunner(config, process_runner=process, source_environment={"PATH": os.environ["PATH"]},
                          api_transport=api.transport())
    task = asyncio.create_task(runner.run(video()))
    await process.entered.wait()
    assert await runner.cancel(video().request_id) == "cancelled"
    process.gate.set()
    with pytest.raises(ProviderFault) as fault:
        await task
    assert fault.value.code == "auth" and not api.seen and not calls()
    assert receipt(config, video())["state"] == "cancelled"


# Part D: a broken ledger, budget or status file closes the lane; it never costs a turn, a receipt or a result.

def failing(self, *args, **kwargs):
    raise KeyError("SECRET-DETAIL")


@pytest.mark.parametrize("broken", [{"entries": [{"request_id": "x"}]}, {"entries": [], "pauses": {"key": "soon"}}])
async def test_a_malformed_ledger_sends_api_first_work_to_the_subscription(lane, broken):
    config, configure, calls = lane
    (config.jobs_dir / ".api").mkdir(parents=True)
    (config.jobs_dir / ".api" / "ledger.json").write_text(json.dumps(broken))
    cli_email(configure)
    api = Api(ok(v5_draft().model_dump(mode="json")))
    result = await make_runner(config, api).run(email())
    assert result.usage["billing_mode"] == "subscription" and not api.seen and len(calls()) == 1
    assert tried(config, email()) == [("subscription", "complete")]
    assert status(config)["state"] == "ledger_unreadable" and ledger(config) == broken


async def test_an_endless_retry_after_sends_the_work_to_the_subscription_and_pauses_an_hour_at_most(lane):
    config, configure, calls = lane
    now = [1_000_000.0]
    cli_email(configure)
    api = Api(lambda: httpx.Response(429, headers={"retry-after": "inf"}, json={"error": {"type": "rate_limit_error"}}))
    runner = make_runner(config, api, clock=lambda: now[0])
    result = await runner.run(email())
    assert result.usage["billing_mode"] == "subscription" and len(api.seen) == 1 and len(calls()) == 1
    assert tried(config, email()) == [("api", "not_executed"), ("subscription", "complete")]
    pause = status(config)["paused"]
    assert pause["reason"] == "rate_limit" and 0 < pause["until"] - now[0] <= 3600
    assert ledger(config)["pauses"]["rate_limit"] == pause["until"]


async def test_a_failing_status_write_never_costs_a_turn(lane, monkeypatch, caplog):
    config, configure, calls = lane
    monkeypatch.setattr(Budget, "write_status", failing)
    cli_video(configure)
    api = Api(ok(v5_draft().model_dump(mode="json")))
    runner = make_runner(config, api)
    with caplog.at_level(logging.WARNING, logger="quant_company.providers.claude_runner"):
        # The first turn after a start refreshes the status; a CLI turn and an API turn both go on.
        assert (await runner.run(video())).usage["billing_mode"] == "subscription"
        assert (await runner.run(email())).usage["billing_mode"] == "api_credit"
    assert len(api.seen) == 1 and len(calls()) == 1
    assert ledger(config)["entries"][0]["actual"] == pytest.approx(STREAM_COST)
    messages = [record.getMessage() for record in caplog.records]
    assert messages and all("KeyError" in text and "SECRET-DETAIL" not in text for text in messages)


@pytest.mark.parametrize("operation", ["paused", "reserve"])
async def test_a_failing_budget_check_closes_the_lane(lane, monkeypatch, operation):
    config, configure, calls = lane
    monkeypatch.setattr(Budget, operation, failing)
    cli_email(configure)
    api = Api(ok(v5_draft().model_dump(mode="json")))
    runner = make_runner(config, api)
    assert (await runner.run(email())).usage["billing_mode"] == "subscription"  # API-first: the subscription
    cli_video(configure, error="rate_limit")
    with pytest.raises(ProviderFault) as fault:
        await runner.run(video())
    assert fault.value.code == "quota"  # a fallback: the CLI's own refusal stands
    assert not api.seen and len(calls()) == 2


@pytest.mark.parametrize("answer,outcome", [(None, "complete"), (broken(), "uncertain"),
                                            (ok({"headline": "x"}), "invalid_output")])
async def test_a_failing_settle_keeps_the_receipt_and_the_result(lane, monkeypatch, answer, outcome):
    config, _, calls = lane
    monkeypatch.setattr(Budget, "settle", failing)
    api = Api(answer or ok(v5_draft().model_dump(mode="json")))
    runner = make_runner(config, api)
    if outcome == "complete":
        result = await runner.run(email())
        assert result.usage["billing_mode"] == "api_credit"
        assert await make_runner(config, api).run(email()) == result
    else:
        for again in (runner, make_runner(config, api)):
            with pytest.raises(ProviderFault) as fault:
                await again.run(email())
            assert fault.value.code == outcome
    assert tried(config, email()) == [("api", outcome)]
    assert ledger(config)["entries"][0]["actual"] is None  # unsettled: the whole reservation still counts
    assert len(api.seen) == 1 and calls() == []


@pytest.mark.parametrize("operation", ["release", "pause"])
async def test_a_failing_ledger_write_after_a_refusal_still_sends_the_work_to_the_subscription(lane, monkeypatch,
                                                                                               operation):
    config, configure, calls = lane
    monkeypatch.setattr(Budget, operation, failing)
    cli_email(configure)
    api = Api(refusal(529))
    runner = make_runner(config, api)
    assert (await runner.run(email())).usage["billing_mode"] == "subscription"
    assert tried(config, email()) == [("api", "not_executed"), ("subscription", "complete")]
    # The 529 pause holds even when the ledger cannot record it (in memory, for this runtime).
    assert (await runner.run(email(2))).usage["billing_mode"] == "subscription"
    assert len(api.seen) == 1 and len(calls()) == 2


async def test_status_says_whether_the_key_is_there_and_the_latest_refusal(lane):
    config, configure, calls = lane
    now = [1_000_000.0]
    cli_email(configure)
    api = Api(refusal(400, {"error": {"type": "invalid_request_error"}}))
    runner = make_runner(config, api, clock=lambda: now[0])
    assert (await runner.run(email())).usage["billing_mode"] == "subscription"
    saved = status(config)
    assert saved["state"] == "on" and saved["key_present"] is True
    assert saved["last_refusal"] == {"reason": "rejected", "at": now[0]}
    config.api_key_file.write_text("\n")  # emptied: the lane does nothing, and the status file says so
    now[0] += 60
    assert (await runner.run(email(2))).usage["billing_mode"] == "subscription" and len(api.seen) == 1
    saved = status(config)
    assert saved["state"] == "on" and saved["key_present"] is False
    assert saved["last_refusal"] == {"reason": "rejected", "at": now[0] - 60}
    assert KEY not in json.dumps(saved) and len(calls()) == 2
