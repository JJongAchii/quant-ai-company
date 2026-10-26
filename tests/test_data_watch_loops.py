"""Data watch loop (ADR 0049): one committed summary across a retry; it runs without any model capacity."""

from types import SimpleNamespace

import pytest

from quant_company.data_watch.runner import DataWatchRunner
from quant_company.loops import attempt
from quant_company.runtime import data_watch_loops

from .test_data_watch import catalog, descriptor, rows, tick, watch  # noqa: F401


def test_a_disabled_data_watch_has_no_loop():
    assert data_watch_loops(SimpleNamespace(settings=SimpleNamespace(data_watch_enabled=False))) == []


@pytest.mark.integration
async def test_data_watch_retry_keeps_one_committed_summary(watch):  # noqa: F811
    calls = []

    async def collect():
        calls.append(1)
        await tick(watch)
        if len(calls) == 1:
            raise RuntimeError("Synthetic worker loss after database commit")
        return {"state": "reported"}

    [loop] = data_watch_loops(watch.company)
    result = await attempt(loop.name, collect, timeout=loop.timeout, attempts=loop.attempts, retry_seconds=0)
    assert (loop.name, loop.timeout, loop.attempts, loop.wait(result)) == ("data-watch", 120, 3, 60)
    assert len(calls) == 2
    assert len(rows(watch, "outbox")) == len(rows(watch, "data_watch_inventory")) == 1
    assert not rows(watch, "turns")


@pytest.mark.integration
async def test_data_watch_reports_without_model_capacity(watch, monkeypatch):  # noqa: F811
    monkeypatch.setattr("quant_company.execution.provider_for", lambda *_: pytest.fail("model called"))
    data = catalog(watch)
    runner = DataWatchRunner(watch.company, lambda _, name, args:
                             data if name == "lake_catalog" else descriptor(data["data"]["datasets"][0]))
    [loop] = data_watch_loops(watch.company, runner)
    for _ in range(5):
        if rows(watch, "outbox"):
            break
        assert await attempt(loop.name, loop.tick, timeout=loop.timeout) is not None
    assert rows(watch, "outbox") and not rows(watch, "turns")
