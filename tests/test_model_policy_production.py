"""Optional compatibility checks for separately deployed company producers."""

import importlib.util
from datetime import UTC, date, datetime, timedelta

import pytest
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.skipif(importlib.util.find_spec("quant_company.briefing") is None,
                                reason="Briefing producer exists only in the current production source cohort")


def test_briefing_producer_uses_assignment_and_preserves_frozen_retry(company, monkeypatch):
    from quant_company.briefing import schedule
    from quant_company.briefing.contracts import BRIEFER, BriefEdition, SourceDocument
    from quant_company.briefing.store import BriefStore

    from quant_company.company import load_roles, stable

    settings = company.settings
    settings.model_accounts_enabled = True
    settings.model_assignments_enabled = True
    settings.briefing_enabled = True
    settings.briefing_publish_enabled = False
    settings.briefing_search_enabled = False
    settings.briefing_channel_id = "CQUANT"
    settings.briefing_owner_user = "UHUMAN"
    company.roles[BRIEFER] = load_roles(settings)[BRIEFER]
    cutoff = datetime(2026, 9, 21, 22, 0, tzinfo=UTC)
    edition = BriefEdition(id=stable("assignment-synthetic-briefing"), day=date(2026, 9, 22), kind="am",
                           due_at=cutoff+timedelta(minutes=45), cutoff=cutoff,
                           starts_at=cutoff-timedelta(minutes=20), expires_at=cutoff+timedelta(hours=2),
                           us_session=date(2026, 9, 21), kr_session=date(2026, 9, 22),
                           previous_us_session=date(2026, 9, 18), previous_kr_session=date(2026, 9, 21))
    monkeypatch.setattr(schedule, "scheduled", lambda settings, at=None: [edition])
    monkeypatch.setattr(schedule, "close", lambda market, day, changes=None: cutoff-timedelta(hours=2))
    clock = {"at": edition.cutoff - timedelta(minutes=1)}
    monkeypatch.setattr(schedule, "utcnow", lambda: clock["at"])
    store = BriefStore(company)
    store.register()
    claimed = store.claim_collection()
    assert str(claimed["id"]) == edition.id
    doc = SourceDocument(id="assignment-fixture", url="https://www.cnbc.com/assignment-fixture.html",
                         title="Synthetic model selection fixture", publisher="Synthetic fixture", kind="media",
                         content="Synthetic integration fixture. No real market data or investment results.",
                         published_at=edition.cutoff-timedelta(hours=1),
                         retrieved_at=edition.cutoff-timedelta(minutes=2), sha256="a"*64,
                         registration="fixture", receipt={"synthetic": True})
    store.save_collection(claimed, {"edition": edition.model_dump(mode="json"),
                                   "documents": [doc.model_dump(mode="json")],
                                   "collection_errors": [], "morning_watchpoints": []})
    clock["at"] = edition.cutoff

    def assign(model):
        with company.db.transaction() as conn:
            conn.execute("UPDATE model_assignment_policy SET revision=revision+1,bindings=%s WHERE id=1",
                         (Jsonb({BRIEFER: {"model": model, "reasoning_effort": "high", "command_id": "fixture"}}),))

    assign("brief-candidate-a")
    ready = store.prepare()
    assert ready["state"] == "ready"
    assert ready["request"]["model"] == "brief-candidate-a"
    assert ready["request"]["reasoning_effort"] == "high"
    with company.db.transaction() as conn:
        row = conn.execute("SELECT target,selection FROM model_execution_bindings WHERE request_id=%s",
                           (ready["request"]["request_id"],)).fetchone()
        assert row["target"] == BRIEFER and row["selection"]["source"] == "pinned"
    assign("brief-candidate-b")
    assert store.prepare()["request"] == ready["request"]
