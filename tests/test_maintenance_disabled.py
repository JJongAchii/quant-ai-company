"""Real PostgreSQL: a Maintainer switched off in maintenance.json stays up, says so and does no work."""

import pytest

from quant_company.maintenance import reporting
from quant_company.maintenance.runner import Maintainer, run_maintenance
from quant_company.maintenance.store import Store

from .test_maintenance import GitHubFixture, ModelFixture, config, prepare


def off():
    return config().model_copy(update={"enabled": False})


def runtime(company):
    with company.db.transaction() as conn:
        return conn.execute("SELECT runtime FROM maintenance_control WHERE id=1").fetchone()["runtime"]


@pytest.mark.integration
async def test_disabled_maintainer_records_off_and_starts_no_job(company):
    prepare(company)  # An unobserved request the enabled Maintainer would triage.
    runner = Maintainer(company, off(), github=GitHubFixture(), provider=ModelFixture())
    runner.store.initialize()
    assert await runner.tick() == {"state": "disabled"}
    assert runtime(company)["enabled"] is False
    assert runner.provider.requests == [] and runner.github.published == 0
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM maintenance_jobs").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM maintenance_observations").fetchone()["n"] == 0


@pytest.mark.integration
def test_disabled_reporter_posts_nothing_but_keeps_its_heartbeat(company, monkeypatch):
    Store(company, off()).initialize()

    def unexpected(*args, **kwargs):
        raise AssertionError("a disabled Maintainer must not report")

    monkeypatch.setattr(reporting, "refresh", unexpected)
    monkeypatch.setattr(Store, "report_reviews", unexpected)
    assert reporting.Reporter(company, off()).report() == {"state": "disabled"}
    assert "reporter_heartbeat_at" in runtime(company)
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0


@pytest.mark.integration
async def test_disabled_maintainer_service_starts_instead_of_exiting(company, monkeypatch):
    started = []

    async def run_all(loops):
        started.extend(loop.name for loop in loops)

    monkeypatch.setattr("quant_company.loops.run_all", run_all)
    await run_maintenance(company, off())
    assert started == ["maintenance", "maintenance-report"]
