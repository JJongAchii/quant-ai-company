import asyncio
import json
import os
import subprocess
from datetime import UTC, datetime
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


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("REAL_CODEX_COMPANY") != "1", reason="Opt-in real Codex subscription test")
async def test_live_codex_company_producer_consumer(company, temporal_environment):
    import httpx

    from quant_company.company import load_roles, now
    from quant_company.providers.client import RuntimeClient
    from quant_company.providers.codex_runner import CodexRunner, RunnerConfig
    from quant_company.providers.codex_runtime import create_app

    # Production role instructions with a small common model for this transport qualification.
    # This does not qualify the production role/model choices or financial expertise.
    company.roles = {key: role.model_copy(update={"model": "gpt-5.6-luna"})
                     for key, role in load_roles(company.settings).items()}
    company.settings.company_max_daily_turns = 12
    company.settings.company_max_project_tasks = 6
    company.settings.company_max_task_turns = 4
    company.settings.temporal_task_queue = "live-smoke-" + uuid4().hex
    company.put_source(source_id="smoke-source", title="Synthetic acceptance fixture", uri="fixture://company-smoke",
                        content="Synthetic fixture only: currency KRW, principal 100, simple annual rate 0.05, "
                                "term 1 year. No real market observation or investment recommendation.",
                        available_at=now(), approved=True, synthetic=True)
    result = company.ingest(event_key="live-smoke-" + uuid4().hex, owner="local-smoke", text=(
        "실제 연구가 아닌 4명 협업 통신 인수용 합성 과제입니다. 총괄은 financial_strategist와 researcher_kr에게 "
        "각각 1회 위임하세요. 금융전략은 calculate로 100*(1+0.05)를 계산하고 반환값을 확인해 합성 예제라고 "
        "명시한 짧은 산출물을 남기세요. 국내연구는 data에게 smoke-source를 read_source로 읽고 가용 시점과 "
        "합성 여부를 확인하도록 1회 직접 위임한 뒤 결과를 짧은 산출물로 정리하세요. 데이터는 실제 도구 결과를 "
        "보고 산출물을 남기세요. 총괄은 자식 두 업무가 끝나면 한 번에 취합하고 완료하세요. 추가 위임, "
        "예약이나 실제 연구는 필요 없습니다. 각 담당자는 산출물을 1개씩 남기고 메시지는 3문장 이내로 쓰세요."
    ))
    runner = CodexRunner(RunnerConfig(codex_home=Path(os.environ["CODEX_HOME"]),
                                     jobs_dir=Path(".local/live-company-jobs").resolve(), timeout_seconds=120))
    token = "local-live-smoke-not-a-production-token"
    client = RuntimeClient("http://codex.test", token, timeout_seconds=180,
                           transport=httpx.ASGITransport(app=create_app(runner=runner, token=token)))
    temporal = temporal_environment.client
    state = None
    evidence = {"checked_at": datetime.now(UTC).isoformat(),
                "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "postgres": "real", "temporal": "real-local", "provider": "real-codex-subscription",
                "model_override": "gpt-5.6-luna", "max_model_turns": 12,
                "slack": "not-connected", "aws": "not-deployed", "financial_qualification": False}
    try:
        async with make_worker(temporal, company, TurnExecutor(company, client)):
            async with asyncio.timeout(300):
                while True:
                    await dispatch_once(temporal, company)
                    state = await asyncio.to_thread(company.project_state, result["project_id"])
                    assert not any(task["status"] == "blocked" for task in state["tasks"]), state["turns"]
                    if state["tasks"] and all(task["status"] == "completed" for task in state["tasks"]):
                        break
                    await asyncio.sleep(0.3)
            for turn in state["turns"]:
                await temporal.get_workflow_handle("company-turn-" + turn["id"]).result()
        assert {task["agent"] for task in state["tasks"]} == {
            "director", "financial_strategist", "researcher_kr", "data"}
        assert any(m["author"] == "researcher_kr" and m["recipient"] == "data" and m["kind"] == "delegation"
                   for m in state["messages"])
        assert any(m["author"] == "tool:calculate" and '"result": "105.00"' in m["text"] for m in state["messages"])
        assert any(m["author"] == "tool:read_source" for m in state["messages"])
        assert len(state["artifacts"]) >= 4
        evidence["status"] = "passed"
    except BaseException:
        evidence["status"] = "not_passed"
        raise
    finally:
        await runner.close()
        evidence["state"] = state
        Path(".local/live-company.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
