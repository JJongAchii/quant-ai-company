"""Explicit small, real subscription check. No financial experiment or external message."""

import argparse
import asyncio
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx

from quant_company.contracts import ProviderFault, ProviderRequest
from quant_company.providers.client import RuntimeClient
from quant_company.providers.codex_runner import CodexRunner, RunnerConfig
from quant_company.providers.codex_runtime import create_app


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--output", type=Path, default=Path(".local/codex-smoke.json"))
    args = parser.parse_args()
    runner = CodexRunner(RunnerConfig(codex_home=Path(os.environ["CODEX_HOME"]),
                                     jobs_dir=Path(".local/codex-jobs").resolve(), timeout_seconds=120))
    token = "local-smoke-token-not-a-production-secret"
    app = create_app(runner=runner, token=token)
    client = RuntimeClient("http://model.test", token, timeout_seconds=130,
                           transport=httpx.ASGITransport(app=app))
    request = ProviderRequest(request_id=args.request_id, model=args.model,
                              prompt='Return a complete AgentDecision with say exactly "연결 확인 완료". '
                                     'All action lists must be empty and follow_up null. Do not call any tools.')
    evidence = {"checked_at": datetime.now(UTC).isoformat(), "model": args.model,
                "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "request_id": args.request_id, "real_subscription_call": True,
                "slack_connected": False, "remote_server_tested": False}
    try:
        result = await client.run(request)
        replay = await client.run(request)
        assert result == replay
        assert result.decision.say == "연결 확인 완료"
        evidence.update(status="passed", result=result.model_dump(mode="json"), replay_equal=True)
    except ProviderFault as exc:
        evidence.update(status="not_passed", fault_code=exc.code, message=exc.message,
                        retry_after_seconds=exc.retry_after_seconds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    if evidence["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
