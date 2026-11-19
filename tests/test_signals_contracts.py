from pathlib import Path

import psycopg
import pytest
from pydantic import ValidationError

from quant_company.config import Settings
from quant_company.signals.contracts import SignalProposal
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
