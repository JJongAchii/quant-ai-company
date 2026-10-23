import json

import pytest

from quant_company.contracts import ProviderRequest
from quant_company.providers.claude_api import WINDOW, Budget, cost_usd, max_tokens, reservation_usd


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
