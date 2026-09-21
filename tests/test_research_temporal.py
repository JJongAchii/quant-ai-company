import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer

from quant_company.company import Company
from quant_company.research.workflow import ResearchWorkflow
from quant_company.runtime import make_research_worker

from .test_research_runner import returned
from .test_research_store import row_for


@pytest.mark.integration
async def test_real_temporal_research_report_survives_worker_restart_and_replays(
    research, credentials, monkeypatch, tmp_path,
):
    row, _ = returned(research, credentials, monkeypatch, tmp_path)
    cache = Path(".local/temporal").resolve()
    cache.mkdir(parents=True, exist_ok=True)
    research.settings.temporal_task_queue = "research-test-" + uuid4().hex
    async with await WorkflowEnvironment.start_local(download_dest_dir=str(cache), ui=False) as environment:
        client = environment.client
        async with make_research_worker(client, research):
            handle = await client.start_workflow(ResearchWorkflow.run, id="research-" + uuid4().hex,
                                                 task_queue=research.settings.temporal_task_queue + "-research")
            async with asyncio.timeout(20):
                while row_for(research, row["id"])["state"] != "completed":
                    await asyncio.sleep(0.05)
        restarted = Company(research.settings, research.roles)
        async with make_research_worker(client, restarted):
            async with asyncio.timeout(10):
                while True:
                    history = await handle.fetch_history()
                    completions = [event for event in history.events if event.HasField("activity_task_completed_event_attributes")]
                    if len(completions) >= 2:
                        break
                    await asyncio.sleep(0.05)
            await Replayer(workflows=[ResearchWorkflow]).replay_workflow(history)
            with restarted.db.transaction() as conn:
                assert conn.execute("SELECT count(*) AS n FROM artifacts").fetchone()["n"] == 1
                assert conn.execute("SELECT count(*) AS n FROM events WHERE kind='research_report_ready'").fetchone()["n"] == 1
            await handle.terminate("Synthetic integration test finished")
