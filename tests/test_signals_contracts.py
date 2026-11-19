from datetime import UTC, date, datetime
from pathlib import Path

import psycopg
import pytest
from pydantic import ValidationError

from quant_company.config import Settings
from quant_company.signals.contracts import SignalProposal
from quant_company.signals.instructions import final_note, instruction
from quant_company.signals.render import analyst_post, fallback_post, scorecard_post, unparsed_post, window
from quant_company.signals.schedule import slots_for
from quant_company.signals.snapshot import snapshot_text
from tests.test_briefing import brief  # noqa: F401


def test_flag_requires_publishing_briefing():
    with pytest.raises(ValidationError, match="Analyst signals require an enabled publishing briefing"):
        Settings(analyst_signals_enabled=True)
    assert Settings(analyst_signals_enabled=True, briefing_enabled=True,
                    briefing_publish_enabled=True).analyst_signals_enabled


def test_flag_does_not_change_briefing_policy(brief):  # noqa: F811
    store, _ = brief
    before = store.policy()
    store.company.settings.analyst_signals_enabled = True
    assert store.policy() == before


def test_compose_passes_flag():
    compose = Path(__file__).resolve().parents[1] / "deploy" / "compose.yaml"
    assert "ANALYST_SIGNALS_ENABLED: ${ANALYST_SIGNALS_ENABLED:-false}" in compose.read_text()


def proposal(**updates):
    value = {"kind": "intraday", "session_day": "2026-11-20", "direction": "long", "confidence": 60,
             "reasons": [{"text": "미국 장 강세", "source_ids": ["a"]}],
             "invalidation": "갭 하락", "source_ids": ["a"]}
    return {**value, **updates}


def test_proposal_bounds():
    assert SignalProposal.model_validate(proposal()).confidence == 60
    reason = {"text": "x", "source_ids": ["a"]}
    for bad in ({"confidence": 49}, {"confidence": 91}, {"confidence": "60"},
                {"reasons": [reason] * 4}, {"reasons": [{"text": "x" * 201, "source_ids": ["a"]}]},
                {"direction": "롱"}, {"extra": 1}):
        with pytest.raises(ValidationError):
            SignalProposal.model_validate(proposal(**bad))


def test_migration_creates_signal_tables(company):
    company.db.migrate()
    with company.db.transaction() as conn:
        for table in ("trading_signals", "trading_signal_returns", "trading_signal_reports"):
            assert conn.execute("SELECT to_regclass(%s) t", (table,)).fetchone()["t"]
        row = ("11111111-1111-1111-1111-11111111111%s", "2026-11-20", "intraday", "C", "U")
        sql = ("INSERT INTO trading_signals(id,session_day,kind,channel,owner_user,snapshot_at,snapshot_latest,"
               "tools_until,fallback_at,send_at,entry_at,exit_at,exit_day) VALUES (%s,%s,%s,%s,%s,now(),now(),"
               "now(),now(),now(),now(),now(),%s)")
        conn.execute(sql, (row[0] % 1, *row[1:], "2026-11-20"))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(sql, (row[0] % 2, *row[1:], "2026-11-20"))


def _quote(symbol, value, at, change):
    return {"symbol": symbol, "value": value, "source_time": at, "session_day": "2026-11-19",
            "session_open": None, "previous_close": None, "change_pct": change, "delayed": False}


SNAPSHOT = {
    "kind": "intraday", "observed_at": "2026-11-19T22:30:00+00:00",
    "quotes": {
        "es": _quote("ES=F", "7374.5", "2026-11-19T22:00:00+00:00", "1.0"),
        "usdkrw": _quote("KRW=X", "1331.0", "2026-11-19T22:20:00+00:00", "0.1"),
        "ewy": _quote("EWY", "80", "2026-11-19T21:00:00+00:00", "0.8"),
    },
    "errors": {}, "receipts": {}, "raw": {},
}


def slot_proposal(slot, direction="long"):
    return SignalProposal(kind=slot.kind, session_day=slot.day, direction=direction, confidence=58,
                          reasons=[{"text": "선물 강세", "source_ids": ["s"]}, {"text": "환율 안정", "source_ids": ["s"]}],
                          invalidation="시가 갭이 크게 줄면", source_ids=["s"])


def day_slots():
    return slots_for(date(2026, 11, 20))


def test_instruction_has_every_required_element():
    intraday, overnight = day_slots()
    text = snapshot_text(SNAPSHOT)
    out = instruction(intraday, "signal:2026-11-20:intraday:snapshot", text)
    for part in ("09:00", "15:30", "08:30", "08:15", "signal:2026-11-20:intraday:snapshot", text, "48.6", "49.9",
                 "47.1", "47.7", "75.5", "62.9", "59.4", "밤사이 정보는 대부분 시초가에 반영",
                 "롱 또는 숏", "주문이 아니", "SignalProposal", "artifacts"):
        assert part in out, part
    assert len(out) < 4000
    night = instruction(overnight, "signal:2026-11-20:overnight:snapshot", text)
    assert "밤사이 정보는 대부분 시초가에 반영" not in night
    assert "11월 23일" in night and "59.4" in night and len(night) < 4000
    assert "도구 없이 지금 최종 답" in final_note("시간")


def test_analyst_post_matches_spec_sketch():
    intraday, overnight = day_slots()
    lines = analyst_post(intraday, slot_proposal(intraday), SNAPSHOT).split("\n")
    assert lines[0] == "📈 장중 신호 · 11월 20일(금)  시가 09:00 진입 → 종가 15:30 청산"
    assert lines[1] == "롱 · 확신도 58%"
    post = "\n".join(lines)
    assert "근거" in post and "1. 선물 강세" in post and "틀렸다고 볼 조건: " in post
    assert "기준 시세 07:30 · S&P 500 선물 7,374.5(07:00)" in lines[-1]
    assert "누적" not in post
    assert window(overnight) == "종가 15:30 진입 → 11월 23일(월) 시가 09:00 청산"


def test_late_post_header():
    intraday, _ = day_slots()
    late = datetime(2026, 11, 19, 23, 41, tzinfo=UTC)
    lines = analyst_post(intraday, slot_proposal(intraday, "short"), SNAPSHOT, late_at=late, fallback="long").split("\n")
    assert lines[0] == "📈 장중 신호 · 늦음 08:41 · 임시 신호와 반대"
    assert lines[1] == window(intraday)
    assert "임시 신호와 같음" in analyst_post(intraday, slot_proposal(intraday), SNAPSHOT, late_at=late, fallback="long")


def test_late_post_without_interim_has_no_interim_part():
    intraday, _ = day_slots()
    late = datetime(2026, 11, 19, 23, 41, tzinfo=UTC)
    first = analyst_post(intraday, slot_proposal(intraday), SNAPSHOT, late_at=late).split("\n")[0]
    assert first == "📈 장중 신호 · 늦음 08:41"


def test_instruction_example_is_a_valid_proposal():
    import json
    for slot in day_slots():
        sid = f"signal:{slot.day}:{slot.kind}:snapshot"
        out = instruction(slot, sid, snapshot_text(SNAPSHOT))
        assert len(out) < 4000
        line = next(x for x in out.split("\n") if x.startswith("{") and '"kind"' in x)
        parsed = SignalProposal.model_validate(json.loads(line))
        assert (parsed.kind, parsed.session_day) == (slot.kind, slot.day)
        assert parsed.reasons[0].source_ids == [sid]


def test_fallback_post_copy():
    intraday, overnight = day_slots()
    lines = fallback_post(intraday, "앞선 질문에 답하는 중").split("\n")
    assert lines[0] == "⏱️ 임시 신호 · 11월 20일(금) 장중  시가 09:00 진입 → 종가 15:30 청산"
    assert lines[1] == "롱 · Analyst 판단 아님 · 규칙 신호(항상 롱, 과거 적중 약 50%)"
    assert lines[2] == "Analyst 신호가 늦어지고 있습니다(사유: 앞선 질문에 답하는 중). 나오는 대로 이어서 올립니다."
    assert "약 59%" in fallback_post(overnight, "x").split("\n")[1]


def test_unparsed_post_header_and_scorecard_footer():
    _, overnight = day_slots()
    assert unparsed_post(overnight, "say").split("\n")[0] == "🌙 오버나잇 신호 · 형식 확인 실패 · 채점 제외"
    assert scorecard_post(date(2026, 11, 20), ["a"], {}).split("\n") == [
        "a", "수수료·세금·호가 차이 제외. 숏은 KODEX 200 반대 방향으로 계산"]
