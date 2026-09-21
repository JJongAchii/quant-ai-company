import asyncio
import contextlib
import json

from pydantic import ValidationError
from temporalio import activity

from .company import Company, PolicyError
from .contracts import AgentDecision, ProviderFault, ProviderRequest, ProviderResponse


class FixtureProvider:
    """Explicit simulation only; proves plumbing, never agent or market competence."""

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        data = json.loads(request.prompt.split("TASK DATA JSON:\n", 1)[1])
        agent = data["task"]["agent"]
        children = data["child_results"]
        if agent == "director" and not children:
            decision = AgentDecision(say="[시뮬레이션] 금융 가정과 데이터 준비를 동료에게 확인합니다.", status="wait",
                                     delegations=[{"agent": "financial_strategist", "instruction": "예제의 금융 가정을 검토하세요."},
                                                  {"agent": "researcher_kr", "instruction": "예제 자료와 검증 계획을 확인하세요."}])
        elif agent == "researcher_kr" and not children:
            decision = AgentDecision(say="[시뮬레이션] 데이터 담당자에게 직접 확인을 요청합니다.", status="wait",
                                     delegations=[{"agent": "data", "instruction": "공개 테스트 fixture의 출처를 확인하세요."}])
        elif agent == "financial_strategist" and not any(
                m["kind"] == "tool" and m["recipient"] == agent for m in data["messages"]):
            decision = AgentDecision(say="[시뮬레이션] 단리 예제의 계산을 확인합니다.", status="continue",
                                     tools=[{"name": "calculate", "arguments": {"expression": "100 * (1 + 0.05)"}}])
        else:
            decision = AgentDecision(say=f"[시뮬레이션] {agent}의 예제 업무를 완료했습니다. 실제 시장 연구 결과는 아닙니다.",
                                     status="complete", artifacts=[{"title": f"{agent} 테스트 기록",
                                     "content": "합성 fixture를 사용한 협업 경로 검증. 금융 전문성이나 전략 성과를 의미하지 않습니다.",
                                     "source_ids": []}])
        return ProviderResponse(request_id=request.request_id, decision=decision, provider="fixture")

    async def cancel(self, request_id):
        return "cancelled"


def provider_for(company: Company):
    if company.settings.model_provider == "fixture":
        return FixtureProvider()
    from .providers.client import RuntimeClient

    return RuntimeClient(company.settings.model_runtime_url,
                         company.settings.model_runtime_token.get_secret_value(),
                         timeout_seconds=company.settings.company_model_timeout_seconds)


class TurnExecutor:
    def __init__(self, company: Company, provider=None):
        self.company = company
        self.provider = provider if provider is not None else provider_for(company)

    async def execute(self, turn_id: str, heartbeat=False) -> dict:
        prepared = await asyncio.to_thread(self.company.prepare_turn, turn_id)
        if prepared["state"] != "ready":
            return prepared
        request = ProviderRequest.model_validate(prepared["request"])
        async def infer_and_read():
            from .web_tools import prefetch

            response = await self.provider.run(request)
            await prefetch(self.company, turn_id, response, self.provider)
            return response

        task = asyncio.create_task(infer_and_read())
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=1)
                if heartbeat:
                    activity.heartbeat({"turn_id": turn_id})
                if not await asyncio.to_thread(self.company.is_current, turn_id):
                    with contextlib.suppress(ProviderFault, asyncio.TimeoutError):
                        await asyncio.wait_for(self.provider.cancel(turn_id), timeout=10)
                    from .web_tools import cancel_pending

                    with contextlib.suppress(ProviderFault, asyncio.TimeoutError):
                        await asyncio.wait_for(cancel_pending(self.company, turn_id, self.provider), timeout=10)
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                    return {"state": "stale"}
            response = await task
            return await asyncio.to_thread(self.company.commit_turn, turn_id, response)
        except ProviderFault as exc:
            if exc.code in {"quota", "busy", "unavailable"}:
                seconds = max(5, min(exc.retry_after_seconds or 30, 604800))
                await asyncio.to_thread(self.company.defer_turn, turn_id, seconds, exc.code, exc.code == "quota")
                return {"state": "defer", "seconds": seconds, "reason": exc.code}
            await asyncio.to_thread(self.company.block_turn, turn_id, exc.code)
            return {"state": "blocked", "reason": exc.code}
        except (PolicyError, ValidationError, ValueError) as exc:
            reason = "proposal_rejected:" + str(exc)[:180]
            await asyncio.to_thread(self.company.block_turn, turn_id, reason)
            return {"state": "blocked", "reason": "proposal_rejected"}
        finally:
            # On worker loss the remote runtime may finish and cache the result. Reuse the same
            # request ID after restart. Only a known revision change explicitly cancels inference.
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    @activity.defn(name="company_execute_turn")
    async def activity_execute(self, turn_id: str) -> dict:
        return await self.execute(turn_id, heartbeat=True)

    @activity.defn(name="company_block_turn")
    async def activity_block(self, turn_id: str) -> None:
        await asyncio.to_thread(self.company.block_turn, turn_id, "activity_retries_exhausted")
