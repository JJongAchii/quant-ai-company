import asyncio
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.worker import Replayer, Worker

from quant_company.staff.store import StaffStore
from quant_company.staff.workflow import StaffDevelopmentWorkflow

from .test_staff_development import oracle_answer, reply
from .test_temporal import temporal_environment  # noqa: F401


@pytest.mark.integration
async def test_staff_temporal_retry_recovers_frozen_request_without_double_grade(company, temporal_environment):  # noqa: F811
    store = StaffStore(company)
    identity = store.enqueue("data", "UHUMAN")
    seen = []

    @activity.defn(name="company_staff_tick")
    async def tick():
        ready = await asyncio.to_thread(store.prepare)
        seen.append(ready)
        if len(seen) == 1:
            # Simulate loss after reserving an immutable request, before saving its response.
            raise RuntimeError("simulated worker interruption")
        if ready["state"] == "ready":
            response = reply(ready["request"], answer=oracle_answer(company, identity))
            return await asyncio.to_thread(store.commit, identity, response)
        return ready

    client = temporal_environment.client
    queue = "staff-test-" + uuid4().hex
    async with Worker(client, task_queue=queue, workflows=[StaffDevelopmentWorkflow], activities=[tick]):
        handle = await client.start_workflow(StaffDevelopmentWorkflow.run, id=queue, task_queue=queue)
        async with asyncio.timeout(30):
            while len(seen) < 2:
                await asyncio.sleep(0.1)
            while True:
                with company.db.transaction() as conn:
                    state = conn.execute("SELECT state FROM staff_runs WHERE id=%s", (identity,)).fetchone()["state"]
                if state == "completed":
                    break
                await asyncio.sleep(0.1)
        assert seen[0]["request"] == seen[1]["request"]
        history = await handle.fetch_history()
        await Replayer(workflows=[StaffDevelopmentWorkflow]).replay_workflow(history)
        await handle.cancel()
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM staff_calls").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0
