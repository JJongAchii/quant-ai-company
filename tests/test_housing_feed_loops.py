"""Housing feed loop (ADR 0049): a committed publication survives a retry; the next look is five minutes later."""

from types import SimpleNamespace

import pytest

from quant_company.loops import attempt
from quant_company.runtime import housing_feed_loops

from .test_housing_feed import collect, housing, outgoing  # noqa: F401


def test_a_disabled_housing_feed_has_no_loop():
    assert housing_feed_loops(SimpleNamespace(settings=SimpleNamespace(housing_feed_enabled=False))) == []


@pytest.mark.integration
async def test_housing_commit_survives_a_retry_after_process_loss(housing):  # noqa: F811
    calls = []

    async def tick():
        collect(housing)
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("Simulated process loss after committed publication")
        return {"state": "collected"}

    [loop] = housing_feed_loops(housing.company)
    result = await attempt(loop.name, tick, timeout=loop.timeout, attempts=loop.attempts, retry_seconds=0)
    assert (loop.name, loop.timeout, loop.attempts, loop.wait(result)) == ("housing-feed", 720, 3, 300)
    assert len(calls) == 2 and len(outgoing(housing)) == 1
