"""Bounded cloud transport/recovery check, with no database, Slack, or model calls."""

import argparse
import asyncio
import hashlib
import json
import signal
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import SecretStr
from temporalio import activity
from temporalio.worker import Replayer, Worker

from quant_company.config import Settings
from quant_company.runtime import connect
from quant_company.workflow import CompanyTurnWorkflow


class ProbeActivities:
    def __init__(self, resume: bool):
        self.resume = resume

    @activity.defn(name="company_execute_turn")
    async def execute(self, turn_id: str) -> dict:
        if not self.resume:
            return {"state": "defer", "seconds": 10}
        return {"state": "completed", "probe": turn_id}

    @activity.defn(name="company_block_turn")
    async def block(self, turn_id: str) -> None:
        raise RuntimeError("Cloud probe activity failed")


async def cloud_client(args):
    if args.api_key_file.stat().st_mode & 0o077:
        raise ValueError("API key file must be private to its owner (chmod 600)")
    settings = Settings(
        temporal_address=args.address,
        temporal_namespace=args.namespace,
        temporal_tls=True,
        temporal_api_key=SecretStr(args.api_key_file.read_text().strip()),
    )
    if not settings.temporal_api_key.get_secret_value():
        raise ValueError("Empty API key file")
    return await connect(settings)


async def worker_main(args):
    client = await cloud_client(args)
    probe = ProbeActivities(resume=args.worker_phase == "resume")
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    async with Worker(
        client, task_queue=args.task_queue, workflows=[CompanyTurnWorkflow],
        activities=[probe.execute, probe.block], max_concurrent_activities=1,
        graceful_shutdown_timeout=timedelta(seconds=2),
    ):
        await stop.wait()


@asynccontextmanager
async def worker_process(phase, task_queue):
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(Path(__file__).resolve()), *sys.argv[1:],
        "--worker-phase", phase, "--task-queue", task_queue,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        yield process.pid
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                process.kill()
                await process.wait()


async def check(args) -> dict:
    client = await cloud_client(args)
    probe_id = "connection-check-" + uuid4().hex

    handle = None
    completed = False
    try:
        async with worker_process("first", probe_id) as first_pid:
            handle = await client.start_workflow(
                CompanyTurnWorkflow.run, probe_id, id=probe_id, task_queue=probe_id,
                execution_timeout=timedelta(seconds=90),
            )
            async with asyncio.timeout(30):
                while True:
                    history = await handle.fetch_history()
                    if any(e.HasField("timer_started_event_attributes") for e in history.events):
                        break
                    await asyncio.sleep(0.5)
        # The first process has exited. A new process recovers from cloud history.
        async with worker_process("resume", probe_id) as second_pid:
            result = await asyncio.wait_for(handle.result(), timeout=45)
        history = await handle.fetch_history()
        activity_calls = sum(e.HasField("activity_task_completed_event_attributes") for e in history.events)
        if result != {"state": "completed", "probe": probe_id} or activity_calls != 2:
            raise RuntimeError("Unexpected cloud probe outcome")
        await Replayer(workflows=[CompanyTurnWorkflow]).replay_workflow(history)
        completed = True
        return {
            "status": "passed", "namespace": args.namespace, "address": args.address,
            "tls": True, "workflowId": probe_id, "runId": handle.first_execution_run_id,
            "workflowType": "CompanyTurnWorkflow", "activityCalls": activity_calls,
            "timerRecorded": True, "workerRestart": "passed", "historyReplay": "passed",
            "workerProcessIds": [first_pid, second_pid], "processRestart": "passed",
            "executionHost": "local-mac", "modelCalls": 0, "slackMessages": 0,
            "databaseUsed": False, "awsHostDeployment": "not_run",
            "macOffAcceptance": "not_run",
        }
    finally:
        if handle and not completed:
            try:
                await asyncio.wait_for(handle.terminate("Connection check did not complete"), timeout=10)
            except Exception:
                pass  # The 90-second execution timeout also bounds this synthetic workflow.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--api-key-file", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--worker-phase", choices=["first", "resume"], help=argparse.SUPPRESS)
    parser.add_argument("--task-queue", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_phase:
        if not args.task_queue:
            parser.error("Worker requires a task queue")
        asyncio.run(worker_main(args))
        return
    receipt = {
        "checkedAt": datetime.now(UTC).isoformat(),
        "baseCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "scriptSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "workflowSha256": hashlib.sha256(Path("src/quant_company/workflow.py").read_bytes()).hexdigest(),
    }
    try:
        receipt.update(asyncio.run(check(args)))
    except Exception as exc:
        # Never echo credentials, connection headers, or external error payloads.
        receipt.update(status="not_passed", errorType=type(exc).__name__)
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    raise SystemExit(0 if receipt["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
