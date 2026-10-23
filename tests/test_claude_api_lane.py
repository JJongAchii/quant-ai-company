import json
from datetime import UTC, datetime

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
    (400, {}, {"error": {"type": "invalid_request_error"}}, "rejected", False)])
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
