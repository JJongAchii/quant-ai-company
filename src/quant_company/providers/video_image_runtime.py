"""Codex subscription image generation for the daily-brief video (owner decision 2026-10-12).

The shared codex-runtime keeps image generation off and is not changed. This separate service turns on only Codex's
built-in image tool (no shell, files, web, apps, MCP, agents or memories), uses the runtime's ChatGPT login and keeps
its own receipts: one request ID makes at most one image, a lost answer is served from the receipt, and a generation
that was interrupted is reported uncertain instead of being run again. It holds no Slack, database or paid API key.
"""

import asyncio
import fcntl
import hashlib
import hmac
import json
import os
import re
import shutil
import struct
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..contracts import ProviderFault
from .codex_runner import (
    DISABLED_FEATURES,
    MAX_STDERR_BYTES,
    SUPPORTED_CLI_VERSION,
    ProcessRunner,
    RunnerConfig,
    atomic_json,
    safe_environment,
    strict_json,
)

REQUEST_ID = (r"^video-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
              r"-illustration-[a-z0-9]+(?:-[a-z0-9]+){0,3}$")
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_BODY_BYTES = 64 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
FAULT_STATUS = {"quota": 429, "auth": 401, "busy": 409, "uncertain": 409, "invalid_request": 422, "timeout": 502,
                "invalid_output": 502, "unavailable": 502}
QUOTA_MARKERS = ("usage_limit_reached", "usage limit", "rate_limit_exceeded", "rate limit", "quota_exceeded")
AUTH_MARKERS = ("unauthorized", "authentication", "token_expired", "refresh_token", "not logged in")
# Item types a pure image turn may report; anything else (commands, files, web, MCP) means the turn left its bounds.
PERMITTED_ITEMS = {"agent_message", "reasoning", "todo_list", "error", "image_generation", "image_generation_call"}
IMAGE_FEATURES = frozenset({"image_generation", "code_mode", "code_mode_host", "code_mode_only"})
INSTRUCTION = ("Generate exactly one image with your built-in image generation tool from the brief below. "
               "Do not run commands, read or write files, browse or call any other tool. When the image is "
               "generated, reply with the single word: done.\n\n")


class ImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: str = Field(pattern=REQUEST_ID)
    model: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,63}$")
    reasoning_effort: Literal["low", "medium", "high"] = "low"
    prompt: str = Field(min_length=20, max_length=4000)


def request_digest(request: ImageRequest) -> str:
    return hashlib.sha256(json.dumps(request.model_dump(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def image_command(config: RunnerConfig, request: ImageRequest, work_dir: Path) -> list[str]:
    overrides = [
        'forced_login_method="chatgpt"', 'cli_auth_credentials_store="file"', 'model_provider="openai"',
        'approval_policy="never"', 'web_search="disabled"', "mcp_servers={}", "apps._default.enabled=false",
        "notify=[]", "agents.enabled=false", "allow_login_shell=false", 'shell_environment_policy.inherit="none"',
        "project_doc_max_bytes=0", "project_doc_fallback_filenames=[]", 'history.persistence="none"',
        "hide_agent_reasoning=true", "show_raw_agent_reasoning=false", "features.skip_host_skill_discovery=true",
        "memories.generate_memories=false", "memories.use_memories=false",
        f"projects={{ {json.dumps(str(work_dir))} = {{ trust_level=\"untrusted\" }} }}",
        f"model_reasoning_effort={json.dumps(request.reasoning_effort)}",
    ]
    # The shared runtime's disabled feature list, except the built-in image tool and the code-mode bridge that exposes
    # it (Codex 0.160.1: "code-mode host is disabled" otherwise). The bridge is what codex-runtime already enables
    # for web search; its capability set still excludes shell, files, apps, MCP and agent spawning.
    overrides += [f"features.{name}={'true' if name in IMAGE_FEATURES else 'false'}" for name in DISABLED_FEATURES]
    argv = [config.codex_bin, "exec", "--ignore-user-config", "--ignore-rules", "--strict-config", "--sandbox",
            "read-only", "--skip-git-repo-check", "--json", "--color", "never", "--model", request.model,
            "--cd", str(work_dir), "--ephemeral"]
    for override in overrides:
        argv += ["-c", override]
    return [*argv, "-"]


def parse_events(stdout: bytes, returncode: int) -> tuple[str, dict]:
    """The thread ID and token usage of one completed image turn; a fault otherwise."""
    try:
        events = [strict_json(line) for line in stdout.splitlines() if line.strip()]
        if any(not isinstance(event, dict) for event in events):
            raise ValueError("Invalid event")
        items = [event.get("item") for event in events if str(event.get("type", "")).startswith("item.")]
        if any(not isinstance(item, dict) for item in items):
            raise ValueError("Invalid item")
    except (ValueError, TypeError):
        raise ProviderFault("uncertain", "Codex events could not be read; reconcile before replacing it.") from None
    if any(item.get("type") not in PERMITTED_ITEMS for item in items):
        raise ProviderFault("uncertain", "Codex reported an action outside image generation.")
    completed = [e for e in events if e.get("type") == "turn.completed"]
    failed = [e for e in events if e.get("type") in ("turn.failed", "error")]
    if returncode != 0 or len(completed) != 1:
        text = json.dumps(failed).lower()
        if failed and not completed and not any(item.get("type") == "agent_message" for item in items):
            if any(marker in text for marker in QUOTA_MARKERS):
                raise ProviderFault("quota", "Codex subscription allowance is temporarily unavailable.", 900)
            if any(marker in text for marker in AUTH_MARKERS):
                raise ProviderFault("auth", "Codex requires a valid ChatGPT login.")
        raise ProviderFault("uncertain", "Codex did not confirm a complete image turn.")
    threads = [e.get("thread_id") for e in events if e.get("type") == "thread.started"]
    if len(threads) != 1 or not isinstance(threads[0], str) or not re.fullmatch(r"[\w-]{1,128}", threads[0]):
        raise ProviderFault("uncertain", "Codex did not report one thread for the image turn.")
    usage = completed[0].get("usage") if isinstance(completed[0].get("usage"), dict) else {}
    return threads[0], {k: v for k, v in usage.items() if type(v) is int and 0 <= v <= 2**53}


def collect_image(codex_home: Path, thread_id: str, target: Path) -> dict:
    """Moves the turn's image next to its receipt (copy + delete: different mounts) and describes it."""
    folder = codex_home / "generated_images" / thread_id
    files = sorted((p for p in folder.iterdir() if p.is_file()), key=lambda p: (p.stat().st_mtime_ns, p.name)) \
        if folder.is_dir() else []
    if not files:
        raise ProviderFault("invalid_output", "Codex finished without an image.")
    data = files[-1].read_bytes()
    if not 0 < len(data) <= MAX_IMAGE_BYTES or not data.startswith(PNG_MAGIC) or data[12:16] != b"IHDR":
        raise ProviderFault("invalid_output", "The generated file is not a PNG image within limits.")
    width, height = struct.unpack(">II", data[16:24])
    partial = target.with_name(target.name + ".partial")
    partial.write_bytes(data)
    os.replace(partial, target)
    shutil.rmtree(folder, ignore_errors=True)
    return {"file": target.name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "width": width, "height": height, "media_type": "image/png", "generated": len(files)}


class ImageRunner:
    def __init__(self, config: RunnerConfig, *, process_runner: ProcessRunner | None = None,
                 environment: dict | None = None) -> None:
        self.config, self.process = config, process_runner or ProcessRunner()
        self.environment = dict(os.environ) if environment is None else environment
        self._active: set[str] = set()

    def receipt_path(self, request_id: str) -> Path:
        return self.config.jobs_dir.resolve() / f"{request_id}.json"

    def _cached(self, request: ImageRequest, digest: str) -> dict | None:
        path = self.receipt_path(request.request_id)
        if not path.exists():
            return None
        receipt = strict_json(path.read_bytes())
        if receipt.get("input_digest") != digest:
            raise ProviderFault("invalid_request", "This request ID already named a different image.")
        if receipt.get("state") == "complete":
            return {**receipt["image"], "usage": receipt.get("usage", {})}
        if receipt.get("state") == "failed":
            raise ProviderFault(receipt["fault"], receipt.get("detail", ""), receipt.get("retry_after_seconds", 0))
        if request.request_id in self._active:
            raise ProviderFault("busy", "This image is still being generated.", 30)
        raise ProviderFault("uncertain", "An earlier generation of this image did not finish; it is not run again.")

    async def _preflight(self, work_dir: Path, env: dict) -> None:
        options = dict(cwd=work_dir, env=env, timeout_seconds=15, max_stdout_bytes=MAX_STDERR_BYTES,
                       max_stderr_bytes=MAX_STDERR_BYTES)
        version = await self.process.run([self.config.codex_bin, "--version"], **options)
        if version.returncode != 0 or version.stdout.decode(errors="replace").strip() != f"codex-cli {SUPPORTED_CLI_VERSION}":
            raise ProviderFault("unavailable", f"The image runtime requires the validated Codex CLI {SUPPORTED_CLI_VERSION}.")
        login = await self.process.run([self.config.codex_bin, "login", "status", "-c", 'forced_login_method="chatgpt"',
                                        "-c", 'cli_auth_credentials_store="file"'], **options)
        status = (login.stdout + login.stderr).decode(errors="replace")
        if login.returncode != 0 or not re.search(r"(?m)^Logged in using ChatGPT\s*$", status):
            raise ProviderFault("auth", "Authenticate the shared Codex runtime using the official ChatGPT login.")

    async def run(self, request: ImageRequest) -> dict:
        directory = self.config.jobs_dir.resolve()
        try:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError:
            raise ProviderFault("unavailable", "The image jobs directory is unavailable.") from None
        digest = request_digest(request)
        cached = self._cached(request, digest)
        if cached is not None:
            return cached
        lock = os.open(directory / ".image.lock", os.O_CREAT | os.O_RDWR, 0o600)
        receipt, path = None, self.receipt_path(request.request_id)
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ProviderFault("busy", "Another image is being generated.", 30) from None
            cached = self._cached(request, digest)
            if cached is not None:
                return cached
            self._active.add(request.request_id)
            env = safe_environment(self.config, self.environment)
            with tempfile.TemporaryDirectory(prefix=".image-", dir=directory) as temporary:
                work = Path(temporary)
                await self._preflight(work, env)
                receipt = {"version": 1, "request_id": request.request_id, "input_digest": digest, "state": "running",
                           "started_at": time.time(), "cli_version": SUPPORTED_CLI_VERSION,
                           "requested_execution": {"model": request.model, "reasoning_effort": request.reasoning_effort}}
                atomic_json(path, receipt)
                output = await self.process.run(
                    image_command(self.config, request, work), cwd=work, env=env,
                    stdin=(INSTRUCTION + request.prompt).encode(), timeout_seconds=self.config.timeout_seconds,
                    max_stdout_bytes=self.config.max_stdout_bytes, max_stderr_bytes=self.config.max_stderr_bytes)
            thread_id, usage = parse_events(output.stdout, output.returncode)
            image = collect_image(self.config.codex_home.resolve(), thread_id, directory / f"{request.request_id}.png")
            atomic_json(path, {**receipt, "state": "complete", "completed_at": time.time(), "thread_id": thread_id,
                               "image": image, "usage": usage})
            return {**image, "usage": usage}
        except ProviderFault as fault:
            if receipt is not None:
                # Definitive refusals replay as they are; anything else may have generated and is not run again.
                code = fault.code if fault.code in {"quota", "auth", "invalid_output", "timeout"} else "uncertain"
                atomic_json(path, {**receipt, "state": "failed", "fault": code, "detail": fault.message[:300],
                                   "retry_after_seconds": fault.retry_after_seconds, "completed_at": time.time()})
            raise
        except asyncio.CancelledError:
            if receipt is not None:
                atomic_json(path, {**receipt, "state": "failed", "fault": "uncertain", "completed_at": time.time(),
                                   "detail": "Cancelled after starting."})
            raise
        except Exception:
            if receipt is not None:
                atomic_json(path, {**receipt, "state": "failed", "fault": "uncertain", "completed_at": time.time(),
                                   "detail": "The image could not be committed."})
            raise ProviderFault("uncertain", "The image runtime could not commit a result.") from None
        finally:
            self._active.discard(request.request_id)
            os.close(lock)

    def image_bytes(self, request_id: str) -> tuple[bytes, str]:
        receipt = strict_json(self.receipt_path(request_id).read_bytes())
        if receipt.get("state") != "complete":
            raise FileNotFoundError(request_id)
        data = (self.config.jobs_dir.resolve() / receipt["image"]["file"]).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != receipt["image"]["sha256"]:
            raise ValueError("Image changed after its receipt")
        return data, digest


def runner_from_environment() -> ImageRunner:
    home, jobs = os.environ.get("CODEX_HOME"), os.environ.get("CODEX_JOBS_DIR")
    if not home or not jobs:
        raise ProviderFault("unavailable", "CODEX_HOME and CODEX_JOBS_DIR are required.")
    return ImageRunner(RunnerConfig(codex_home=Path(home), jobs_dir=Path(jobs),
                                    codex_bin=os.environ.get("CODEX_BIN", "codex"),
                                    timeout_seconds=float(os.environ.get("CODEX_TIMEOUT_SECONDS", "300"))))


def fault_response(fault: ProviderFault) -> JSONResponse:
    headers = {"Retry-After": str(fault.retry_after_seconds)} if fault.retry_after_seconds else {}
    return JSONResponse({"code": fault.code, "message": fault.message, "retry_after_seconds": fault.retry_after_seconds},
                        status_code=FAULT_STATUS.get(fault.code, 502), headers=headers)


def create_app(*, runner: ImageRunner | None = None, token: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield

    app = FastAPI(title="Private video image runtime", docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.state.runner = runner

    def authorize(request: Request) -> None:
        secret = token if token is not None else os.environ.get("MODEL_RUNTIME_TOKEN", "")
        if not secret:
            raise ProviderFault("unavailable", "The private image runtime is not configured.")
        if not hmac.compare_digest(request.headers.get("authorization", "").encode(), f"Bearer {secret}".encode()):
            raise ProviderFault("auth", "A valid runtime bearer token is required.")

    def get_runner() -> ImageRunner:
        if app.state.runner is None:
            app.state.runner = runner_from_environment()
        return app.state.runner

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/images")
    async def generate(request: Request) -> JSONResponse:
        try:
            authorize(request)
            body = await request.body()
            if len(body) > MAX_BODY_BYTES:
                raise ProviderFault("invalid_request", "Request exceeds byte limit.")
            try:
                payload = ImageRequest.model_validate(strict_json(body))
            except (ValueError, TypeError, ValidationError):
                raise ProviderFault("invalid_request", "Request does not match ImageRequest.") from None
            result = await get_runner().run(payload)
            return JSONResponse({"request_id": payload.request_id, **result})
        except ProviderFault as fault:
            return fault_response(fault)

    @app.get("/v1/images/{request_id}")
    async def download(request_id: str, request: Request) -> Response:
        try:
            authorize(request)
            if not re.fullmatch(REQUEST_ID, request_id):
                raise ProviderFault("invalid_request", "Unknown image request.")
            try:
                data, digest = get_runner().image_bytes(request_id)
            except (OSError, KeyError, ValueError):
                raise ProviderFault("unavailable", "No completed image for this request.") from None
            return Response(content=data, media_type="image/png", headers={"X-Image-SHA256": digest})
        except ProviderFault as fault:
            return fault_response(fault)

    return app


app = create_app()
