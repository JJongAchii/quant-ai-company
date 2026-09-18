"""Private ASGI gateway for the isolated ChatGPT-authenticated Codex process."""

import asyncio
import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from pydantic import ValidationError
from starlette.responses import JSONResponse

from quant_company.contracts import ProviderFault, ProviderRequest
from quant_company.providers.codex_runner import CodexRunner, RunnerConfig, strict_json

MAX_REQUEST_BYTES = 512 * 1024
FAULT_STATUS = {"quota": 429, "auth": 401, "busy": 409, "uncertain": 409,
                "timeout": 502, "invalid_output": 502, "unavailable": 502}


def fault_response(fault: ProviderFault) -> JSONResponse:
    headers = {"Retry-After": str(fault.retry_after_seconds)} if fault.retry_after_seconds else {}
    return JSONResponse({"code": fault.code, "message": fault.message,
                         "retry_after_seconds": fault.retry_after_seconds},
                        status_code=FAULT_STATUS.get(fault.code, 502), headers=headers)


def runner_from_environment() -> CodexRunner:
    home = os.environ.get("CODEX_HOME")
    jobs = os.environ.get("CODEX_JOBS_DIR")
    if not home or not jobs:
        raise ProviderFault("unavailable", "Configure the Codex authentication and durable jobs directories.")
    try:
        config = RunnerConfig(codex_home=Path(home), jobs_dir=Path(jobs),
                              codex_bin=os.environ.get("CODEX_BIN", "codex"),
                              timeout_seconds=float(os.environ.get("CODEX_TIMEOUT_SECONDS", "300")))
    except ValueError:
        raise ProviderFault("unavailable", "The Codex runtime configuration is invalid.") from None
    return CodexRunner(config)


def create_app(*, runner: CodexRunner | None = None, token: str | None = None,
               runner_factory=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        for task in app.state.operations:
            task.cancel()
        await asyncio.gather(*app.state.operations, return_exceptions=True)

    app = FastAPI(title="Private Codex runtime", docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.state.runner = runner
    app.state.operations = set()

    def authorize(request: Request) -> None:
        secret = token if token is not None else os.environ.get("MODEL_RUNTIME_TOKEN", "")
        if not secret:
            raise ProviderFault("unavailable", "The private runtime is not configured.")
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied.encode(), f"Bearer {secret}".encode()):
            raise ProviderFault("auth", "A valid runtime bearer token is required.")

    def get_runner() -> CodexRunner:
        if app.state.runner is None:
            app.state.runner = (runner_factory or runner_from_environment)()
        return app.state.runner

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        # Liveness only. Never read credentials or query model/account metadata here.
        return {"status": "ok"}

    @app.post("/v1/turns")
    async def turn(request: Request) -> JSONResponse:
        try:
            authorize(request)
            body = bytearray()
            async for part in request.stream():
                body.extend(part)
                if len(body) > MAX_REQUEST_BYTES:
                    return JSONResponse({"code": "invalid_request", "message": "Request exceeds byte limit.",
                                         "retry_after_seconds": 0}, status_code=413)
            payload = ProviderRequest.model_validate(strict_json(body))
        except (ValueError, TypeError, ValidationError):
            # Do not echo invalid field values or the original prompt.
            return JSONResponse({"code": "invalid_request", "message": "Request does not match ProviderRequest.",
                                 "retry_after_seconds": 0}, status_code=422)
        except ProviderFault as fault:
            return fault_response(fault)

        def completed(task: asyncio.Task) -> None:
            app.state.operations.discard(task)
            if not task.cancelled():
                task.exception()  # Consume a detached failure without logging model output.

        try:
            operation = asyncio.create_task(get_runner().run(payload))
            app.state.operations.add(operation)
            operation.add_done_callback(completed)
            # Transport loss is not steering. Finish/cache the existing turn so
            # the caller can retrieve it by its unchanged request ID.
            result = await asyncio.shield(operation)
            return JSONResponse(result.model_dump(mode="json"))
        except ProviderFault as fault:
            return fault_response(fault)
        except asyncio.CancelledError:
            return fault_response(ProviderFault("uncertain", "The turn was interrupted; recover it by its original ID."))

    @app.post("/v1/turns/{request_id}/cancel")
    async def cancel(request_id: str, request: Request) -> JSONResponse:
        try:
            authorize(request)
            status = await get_runner().cancel(request_id)
            return JSONResponse({"request_id": request_id, "status": status})
        except ProviderFault as fault:
            return fault_response(fault)
        except OSError:
            return fault_response(ProviderFault("unavailable", "The durable cancellation store is unavailable."))

    return app


app = create_app()
