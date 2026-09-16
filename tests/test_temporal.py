import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer

from quant_company.company import Company
from quant_company.execution import FixtureProvider, TurnExecutor
from quant_company.runtime import dispatch_once, make_worker
from quant_company.workflow import CompanyTurnWorkflow

from .conftest import queued_turns


@pytest.fixture
async def temporal_environment():
    cache = Path(".local/temporal")
    cache.mkdir(parents=True, exist_ok=True)
    async with await WorkflowEnvironment.start_local(download_dest_dir=str(cache.resolve()), ui=False) as env:
        yield env


@pytest.mark.integration
async def test_real_temporal_postgres_four_role_workflow_and_replay(company, temporal_environment, tmp_path):
    client = temporal_environment.client
    company.settings.temporal_task_queue = "test-" + uuid4().hex
    result = company.ingest(event_key="temporal-collaboration", text="Synthetic collaboration", owner="user")
    executor = TurnExecutor(company, FixtureProvider())
    async with make_worker(client, company, executor):
        async with asyncio.timeout(45):
            while True:
                await dispatch_once(client, company)
                state = await asyncio.to_thread(company.project_state, result["project_id"])
                if state["tasks"] and all(task["status"] == "completed" for task in state["tasks"]):
                    break
                assert not any(task["status"] == "blocked" for task in state["tasks"]), state
                await asyncio.sleep(0.1)
        for turn in state["turns"]:
            handle = client.get_workflow_handle("company-turn-" + turn["id"])
            await handle.result()
            history = await handle.fetch_history()
            await Replayer(workflows=[CompanyTurnWorkflow]).replay_workflow(history)
    assert len(state["tasks"]) == 4
    assert len(state["artifacts"]) == 4
    assert len(state["turns"]) == 7
    (tmp_path / "temporal-collaboration.json").write_text(json.dumps({
        "postgres": "real", "temporal": "real", "model": "fixture", "slack": "not_connected",
        "project_id": result["project_id"], "tasks": state["tasks"], "turns": state["turns"],
        "workflow_histories_replayed": len(state["turns"]),
    }, ensure_ascii=False, indent=2))


@pytest.mark.integration
async def test_temporal_timer_survives_worker_shutdown_and_restart(company, temporal_environment):
    client = temporal_environment.client
    company.settings.temporal_task_queue = "restart-" + uuid4().hex
    result = company.ingest(event_key="delayed", text="Delayed fixture", owner="user", agent="data")
    turn_id = queued_turns(company, result["project_id"])[0]
    with company.db.transaction() as conn:
        conn.execute("UPDATE turns SET due_at=now()+interval '3 seconds' WHERE id=%s", (turn_id,))
    async with make_worker(client, company, TurnExecutor(company, FixtureProvider())):
        await dispatch_once(client, company)
        handle = client.get_workflow_handle("company-turn-" + turn_id)
        async with asyncio.timeout(10):
            while True:
                history = await handle.fetch_history()
                if any(event.HasField("timer_started_event_attributes") for event in history.events):
                    break
                await asyncio.sleep(0.05)
    restarted = Company(company.settings, company.roles)
    async with make_worker(client, restarted, TurnExecutor(restarted, FixtureProvider())):
        # Dispatcher retry cannot create a duplicate workflow even after the first worker stopped.
        with restarted.db.transaction() as conn:
            conn.execute("UPDATE turns SET workflow_started=false WHERE id=%s", (turn_id,))
        await dispatch_once(client, restarted)
        outcome = await asyncio.wait_for(handle.result(), timeout=15)
    assert outcome["state"] == "completed"
    state = restarted.project_state(result["project_id"])
    assert len(state["artifacts"]) == 1
    assert state["tasks"][0]["status"] == "completed"
