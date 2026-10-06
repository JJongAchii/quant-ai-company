"""Real PostgreSQL; source/model fixtures are simulated, no Slack invocation."""

import copy
import importlib.util
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

from quant_company.company import fingerprint

from .test_quant_feed import original, publish, quant  # noqa: F401


@pytest.fixture
def repair():
    path = Path(__file__).resolve().parents[1] / "scripts/reconcile_quant_unsent.py"
    spec = importlib.util.spec_from_file_location("quant_unsent_reconciliation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.reconcile


def blocked(quant):  # noqa: F811
    original(quant)
    publish(quant)
    old_policy = quant.policy()
    channels = list(quant.company.settings.slack_allowed_channels)
    quant.company.settings.slack_allowed_channels = [*channels, "COTHER"]
    with quant.db.transaction() as conn:
        row = conn.execute("SELECT * FROM outbox").fetchone()
        assert quant.gate(conn, row) is False
    return str(row["id"]), channels, old_policy


def test_allowlist_only_requeue_revalidates_frozen_reviews_without_reissuing(quant, repair):  # noqa: F811
    identity, channels, policy = blocked(quant)
    with quant.db.transaction() as conn:
        original_calls = copy.deepcopy(conn.execute("SELECT * FROM quant_feed_calls ORDER BY id").fetchall())
    plan = repair(quant, [identity], channels, policy)
    assert plan["state"] == "planned" and plan["database_writes"] == 0
    assert plan["publications"][0]["frozen_evidence_revalidated"]
    assert repair(quant, [identity], channels, policy, apply_digest=plan["plan_digest"])["state"] == "applied"
    with quant.db.transaction() as conn:
        row = conn.execute("SELECT * FROM outbox").fetchone()
        publication = conn.execute("SELECT * FROM quant_feed_publications").fetchone()
        assert row["status"] == "pending" and row["attempts"] == 0 and row["sent_ts"] is None
        assert publication["policy_digest"] == quant.policy()
        assert quant.gate(conn, row)
        assert conn.execute("SELECT * FROM quant_feed_calls ORDER BY id").fetchall() == original_calls
    with pytest.raises(ValueError, match="not_certainly_unsent"):
        repair(quant, [identity], channels, policy, apply_digest=plan["plan_digest"])


@pytest.mark.parametrize("mutation", ["attempt", "timestamp", "started", "policy", "evidence", "critic", "plan"])
def test_reconciliation_refuses_ambiguous_material_or_invalid_changes(quant, repair, mutation):  # noqa: F811
    identity, channels, policy = blocked(quant)
    plan = repair(quant, [identity], channels, policy)
    with quant.db.transaction() as conn:
        if mutation == "attempt":
            conn.execute("UPDATE outbox SET attempts=1")
        elif mutation == "timestamp":
            conn.execute("UPDATE outbox SET sent_ts='1790802004.352479'")
        elif mutation == "started":
            conn.execute("UPDATE outbox SET started_at=now()")
        elif mutation == "policy":
            quant.company.settings.company_web_enabled = True
        elif mutation == "evidence":
            doc = conn.execute("SELECT * FROM quant_feed_documents").fetchone()
            value = {**doc["brief"], "author_results": "Unsupported changed result"}
            conn.execute("UPDATE quant_feed_documents SET brief=%s", (Jsonb(value),))
        elif mutation == "critic":
            doc = conn.execute("SELECT * FROM quant_feed_documents").fetchone()
            value = {**doc["critique"], "direct_quant_scope": False, "disposition": "hold"}
            conn.execute("UPDATE quant_feed_documents SET critique=%s", (Jsonb(value),))
    with pytest.raises(ValueError):
        repair(quant, [identity], channels, policy,
               apply_digest=fingerprint("wrong") if mutation == "plan" else plan["plan_digest"])
    with quant.db.transaction() as conn:
        assert conn.execute("SELECT status FROM outbox").fetchone()["status"] == "stale"
