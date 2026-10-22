"""Official Claude subscription CLI, with no model tools or paid-provider fallback.

The account owner must disable usage credits. CLI isolation prevents API-key and
model/configuration fallback; it cannot change the account's billing preference.
"""

import asyncio
import fcntl
import hashlib
import json
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError, model_validator

from ..contracts import AgentDecision, ArtifactDraft, ProviderFault, ProviderRequest, ProviderResponse
from ..staff.review_contract import REVIEW_MODEL, IndependentReview
from .codex_runner import ProcessRunner, atomic_json, request_digest, strict_json

CLI_VERSION = "2.1.280"
MAX_OUTPUT = 1024 * 1024
# A max-effort episode streams its whole reasoning (about 1 MB already for a passing plan); only the result is kept.
VIDEO_MAX_OUTPUT = 32 * 1024 * 1024
FORBIDDEN_AUTH = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                  "ANTHROPIC_PROFILE", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                  "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_OAUTH_TOKEN")


@dataclass(frozen=True)
class ClaudeConfig:
    auth_dir: Path
    jobs_dir: Path
    binary: str = "claude"
    timeout_seconds: float = 300
    usage_credits_disabled_confirmed: bool = False
    # A full max-effort episode plan from the delivered brief exceeds the short explanation-review budget.
    video_timeout_seconds: float = 7200
    # The evening brief email shares this one slot with the PM video: a short email never holds it for long.
    newsletter_timeout_seconds: float = 900

    def __post_init__(self):
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 600:
            raise ValueError("Invalid Claude timeout")
        if not math.isfinite(self.video_timeout_seconds) or not 0 < self.video_timeout_seconds <= 10800:
            raise ValueError("Invalid Claude video timeout")
        if not math.isfinite(self.newsletter_timeout_seconds) or not 0 < self.newsletter_timeout_seconds <= 1800:
            raise ValueError("Invalid Claude newsletter timeout")

    def process_limit(self, request):
        if request.output_contract == "agent_decision":
            return self.timeout_seconds
        return self.newsletter_timeout_seconds if request.output_contract in CLAUDE_ONLY else self.video_timeout_seconds


def environment(config, source):
    if not config.usage_credits_disabled_confirmed:
        raise ProviderFault("auth", "Confirm disabled account usage credits before enabling reviews.")
    if any(source.get(key) for key in FORBIDDEN_AUTH):
        raise ProviderFault("auth", "Only the isolated official Claude subscription login is permitted.")
    result = {key: source[key] for key in ("PATH", "HOME", "LANG", "LC_ALL", "TZ") if key in source}
    result.update(CLAUDE_CONFIG_DIR=str(config.auth_dir.resolve()), DISABLE_AUTOUPDATER="1",
                  CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", NO_COLOR="1")
    return result


# Contracts only this runtime serves. contracts.py is also a render-task input (release.render_changes), so a contract
# no other runtime knows is added here: the render image and codex-runtime stay as they are.
CLAUDE_ONLY = ("newsletter_v1",)


class ClaudeRequest(ProviderRequest):
    output_contract: Literal[ProviderRequest.model_fields["output_contract"].annotation.__args__ + CLAUDE_ONLY] = (
        "agent_decision")

    @model_validator(mode="after")
    def scoped_output(self):
        if self.output_contract not in CLAUDE_ONLY:
            return super().scoped_output()
        if (not self.request_id.startswith("newsletter-") or self.web_search or self.session is not None
                or self.attachments or self.brief_deadline_unix is not None):
            raise ValueError("Newsletter output requires a tool-free newsletter request")
        return self


# Staff explanation reviews stay on the calibrated review model; video contracts may use the newer Opus.
VIDEO_MODELS = frozenset({REVIEW_MODEL, "claude-opus-5-5"})


def allowed_models(request):
    return {REVIEW_MODEL} if request.output_contract == "agent_decision" else VIDEO_MODELS


def output_schema(request):
    """The only structured outputs this tool-free runtime may produce, keyed by the frozen request contract."""
    if request.output_contract in {"video_plan_v1", "video_review_v1", "video_episode_v1", "video_visual_review_v1",
                                   "video_screen_fix_v1", "video_shorts_v1"}:
        from ..video.contracts import VideoPlan, VideoReview, VisualReview
        from ..video.episode import EpisodePlan, ScreenFix
        from ..video.shorts import ShortsPlan

        return {"video_plan_v1": (VideoPlan, "Video plan", "영상 대본 작성 완료"),
                "video_episode_v1": (EpisodePlan, "Video episode", "영상 장면 구성 완료"),
                "video_review_v1": (VideoReview, "Video adaptation review", "영상 각색 검토 완료"),
                "video_visual_review_v1": (VisualReview, "Video visual review", "영상 화면 검수 완료"),
                "video_screen_fix_v1": (ScreenFix, "Video screen fix", "영상 화면 수정 완료"),
                "video_shorts_v1": (ShortsPlan, "Video Shorts cut", "쇼츠 대본 작성 완료")}[request.output_contract]
    if request.output_contract in {"video_performance_v1", "video_weekly_v1", "video_answer_v1"}:
        from ..video.analytics_contracts import StrategyReport, VideoAnswer, VideoPerformance

        return {"video_performance_v1": (VideoPerformance, "Video performance analysis", "영상 성과 분석 완료"),
                "video_weekly_v1": (StrategyReport, "Channel strategy report", "채널 전략 리포트 작성 완료"),
                "video_answer_v1": (VideoAnswer, "Video performance answer", "영상 성과 답변 완료")}[
            request.output_contract]
    if request.output_contract == "newsletter_v1":
        from ..newsletter.contracts import NewsletterDraft

        return NewsletterDraft, "Evening brief email", "저녁 브리핑 메일 작성 완료"
    if request.output_contract == "agent_decision":
        return IndependentReview, "Independent explanation review", "독립 설명 검토 완료"
    raise ProviderFault("invalid_output", "This runtime does not produce the requested output contract.")


def stdin_payload(request):
    """Plain prompt text, or one stream-json user message carrying the frames and the prompt (no tools needed)."""
    if not request.attachments:
        return request.prompt.encode()
    content = [{"type": "image", "source": {"type": "base64", "media_type": a.media_type, "data": a.data}}
               for a in request.attachments] + [{"type": "text", "text": request.prompt}]
    return (json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n").encode()


def command(config, request):
    # Requests frozen before explicit effort support used high; never reinterpret their identity.
    effort = request.reasoning_effort or "high"
    return [config.binary, "-p", "--safe-mode", "--model", request.model, "--effort", effort,
            "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--disable-slash-commands", "--setting-sources", "", "--permission-mode", "dontAsk",
            "--permission-prompts", "none", "--no-session-persistence", "--max-turns", "2",
            "--settings", '{"fastMode":false,"forceLoginMethod":"claudeai"}',
            "--output-format", "stream-json", "--verbose", "--json-schema",
            json.dumps(output_schema(request)[0].model_json_schema(), separators=(",", ":"))] + (
            ["--input-format", "stream-json"] if request.attachments else [])


def parse_result(request, output):
    try:
        events = [strict_json(line) for line in output.stdout.splitlines() if line.strip()]
        if any(not isinstance(event, dict) for event in events):
            raise ValueError("Invalid events")
        starts = [e for e in events if e.get("type") == "system" and e.get("subtype") == "init"]
        results = [e for e in events if e.get("type") == "result"]
        messages = [e for e in events if e.get("type") == "assistant"]
        errors = [e.get("error") for e in messages if e.get("error")]
        if errors and not any(e.get("message", {}).get("usage", {}).get("output_tokens", 0) for e in messages):
            if all(error == "rate_limit" for error in errors):
                raise ProviderFault("quota", "Claude subscription allowance is unavailable.", 900)
            if all(error == "authentication_failed" for error in errors):
                raise ProviderFault("auth", "Renew the official Claude subscription login.")
        if output.returncode != 0 or len(results) != 1 or len(starts) != 1:
            raise ProviderFault("uncertain", "Claude did not confirm a complete review; reconcile its original ID.")
        start, result = starts[0], results[0]
        if (start.get("model") != request.model or start.get("mcp_servers")
                or set(start.get("tools", [])) - {"StructuredOutput"}):
            raise ProviderFault("uncertain", "Claude reported an unexpected model or tool configuration.")
        for message in messages:
            for item in message.get("message", {}).get("content", []):
                if item.get("type") == "tool_use" and item.get("name") != "StructuredOutput":
                    raise ProviderFault("uncertain", "Claude reported a disallowed tool action.")
        if result.get("is_error") is not False or result.get("subtype") != "success" or errors:
            raise ProviderFault("invalid_output", "Claude did not return a successful structured review.")
        if result.get("permission_denials"):
            raise ProviderFault("invalid_output", "Claude attempted a prohibited action.")
        models = result.get("modelUsage")
        if not isinstance(models, dict) or set(models) != {request.model}:
            raise ProviderFault("uncertain", "The actual review model could not be verified.")
        schema, title, say = output_schema(request)
        try:
            review = schema.model_validate(result["structured_output"])
        except ValidationError as error:
            if request.output_contract == "agent_decision":
                raise
            # Contract rules and locations only (no rejected text), kept in the private receipt's fault_detail.
            reasons = "; ".join(".".join(map(str, e["loc"])) + ": " + e["msg"]
                                for e in error.errors(include_url=False, include_input=False))
            raise ProviderFault("invalid_output", "Video output failed its contract: " + reasons[:1500]) from None
        usage = result.get("usage", {})
        if not isinstance(usage, dict):
            raise ValueError("Invalid usage")
        counts = {}
        for name in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            value = usage.get(name, 0)
            if type(value) is not int or not 0 <= value <= 2**53:
                raise ValueError("Invalid token count")
            counts[name] = value
        # total_cost_usd is a client-side API-equivalent estimate, not a subscription bill.
        counts.update(actual_model=request.model, cli_version=CLI_VERSION, effort=request.reasoning_effort or "high",
                      effort_evidence="requested_cli_argument_not_provider_attested",
                      billing_mode="subscription", account_usage_credits_disabled_owner_confirmed=True)
        return ProviderResponse(request_id=request.request_id, provider="claude", usage=counts,
                                decision=AgentDecision(status="complete", say=say,
                                    artifacts=[ArtifactDraft(title=title, content=review.model_dump_json())]))
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ProviderFault("invalid_output", "Claude output did not satisfy the independent review contract.") from None


class ClaudeRunner:
    def __init__(self, config, *, process_runner=None, source_environment=None):
        self.config = config
        self.process = process_runner or ProcessRunner()
        self.source = dict(os.environ if source_environment is None else source_environment)
        self.active = {}

    @staticmethod
    def _running(path):
        try:
            return strict_json(path.read_bytes()).get("state") == "running"
        except (OSError, ValueError, AttributeError):
            return False

    def _cached(self, path, digest, identity, locked=False):
        if not path.exists():
            return None
        try:
            if path.stat().st_size > 2 * MAX_OUTPUT:
                raise ValueError("Receipt too large")
            receipt = strict_json(path.read_bytes())
            if (receipt.get("provider") != "claude" or receipt.get("request_id") != identity
                    or receipt.get("input_digest") != digest):
                raise ValueError("Receipt mismatch")
            if receipt["state"] == "complete":
                result = ProviderResponse.model_validate(receipt["result"])
                if result.request_id != identity or result.provider != "claude":
                    raise ValueError("Result mismatch")
                return result
            if receipt["state"] == "deferred":
                delay = max(0, math.ceil(receipt["retry_at"] - time.time()))
                if delay:
                    raise ProviderFault("quota", "Claude subscription allowance is unavailable.", delay)
                return None
            if receipt["state"] == "running" and not locked:
                return None
            if receipt["state"] == "failed" and receipt.get("fault") in {"invalid_output", "timeout"}:
                # A definitive, executed attempt: report the same outcome again instead of running it twice.
                raise ProviderFault(receipt["fault"], str(receipt.get("fault_detail") or "The prior attempt failed.")[:2000])
            raise ProviderFault("uncertain", "A prior Claude attempt needs reconciliation; it will not be repeated.")
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ProviderFault("uncertain", "The durable Claude receipt could not be verified.") from None

    async def _preflight(self, cwd, env):
        options = dict(cwd=cwd, env=env, timeout_seconds=20, max_stdout_bytes=65536, max_stderr_bytes=65536)
        version = await self.process.run([self.config.binary, "--version"], **options)
        if version.returncode != 0 or version.stdout.strip() != f"{CLI_VERSION} (Claude Code)".encode():
            raise ProviderFault("unavailable", "The validated Claude CLI version is required.")
        auth = await self.process.run([self.config.binary, "auth", "status"], **options)
        try:
            status = strict_json(auth.stdout)
            if (auth.returncode != 0 or status.get("loggedIn") is not True
                    or status.get("authMethod") != "claude.ai"
                    or status.get("subscriptionType") not in {"pro", "max"}):
                raise ValueError("Not a subscription login")
        except (ValueError, TypeError, AttributeError):
            raise ProviderFault("auth", "Authenticate the isolated runtime using its Pro/Max account.") from None

    async def run(self, request: ProviderRequest):
        if request.model not in allowed_models(request) or request.web_search:
            raise ProviderFault("invalid_output", "This runtime accepts only offline Opus 5 structured requests.")
        output_schema(request)
        directory = self.config.jobs_dir.resolve()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / f"{request.request_id}.json"
        digest = request_digest(request)
        cached = self._cached(path, digest, request.request_id)
        if cached:
            return cached
        fd = os.open(directory / ".runtime.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if self._running(path):
                    # This very request holds the lane (its receipt says running): ask again later by the same ID.
                    raise ProviderFault("pending", "This request is still running; ask again with its ID.", 20) from None
                raise ProviderFault("busy", "Claude is already executing a review.", 10) from None
            cached = self._cached(path, digest, request.request_id, locked=True)
            if cached:
                return cached
            env = environment(self.config, self.source)
            with tempfile.TemporaryDirectory(prefix=".review-", dir=directory) as temporary:
                cwd = Path(temporary)
                await self._preflight(cwd, env)
                # Cancellation can arrive during the asynchronous auth/version preflight.
                self._cached(path, digest, request.request_id, locked=True)
                receipt = {"provider": "claude", "request_id": request.request_id, "input_digest": digest,
                           "state": "running", "started_at": time.time(), "cli_version": CLI_VERSION,
                           "requested_execution": {"model": request.model,
                                                   "reasoning_effort": request.reasoning_effort or "high"}}
                atomic_json(path, receipt)
                self.active[request.request_id] = asyncio.current_task()
                try:
                    output = await self.process.run(command(self.config, request), cwd=cwd, env=env,
                        stdin=stdin_payload(request), timeout_seconds=self.config.process_limit(request),
                        max_stdout_bytes=MAX_OUTPUT if request.output_contract == "agent_decision" else VIDEO_MAX_OUTPUT,
                        max_stderr_bytes=65536)
                    # Persist the observed process boundary before parsing can reject its response.
                    # Raw CLI output stays private to the process and is never copied to public faults.
                    receipt = {**receipt, "execution_evidence": {
                        "returncode": output.returncode, "completed_at": time.time(),
                        "stdout_bytes": len(output.stdout), "stdout_sha256": hashlib.sha256(output.stdout).hexdigest(),
                        "stderr_bytes": len(output.stderr), "stderr_sha256": hashlib.sha256(output.stderr).hexdigest(),
                    }}
                    atomic_json(path, receipt)
                    result = parse_result(request, output)
                    atomic_json(path, {**receipt, "state": "complete", "completed_at": time.time(),
                                       "result": result.model_dump(mode="json")})
                    return result
                except ProviderFault as fault:
                    atomic_json(path, {**receipt, "state": "deferred" if fault.code == "quota" else "failed",
                                       "fault": fault.code, "fault_detail": str(fault),
                                       "retry_at": time.time() + fault.retry_after_seconds})
                    raise
                finally:
                    # Cancellation/crash leaves the durable running marker and cannot trigger another inference.
                    self.active.pop(request.request_id, None)
        except OSError:
            raise ProviderFault("unavailable", "Claude runtime storage or executable is unavailable.") from None
        finally:
            os.close(fd)

    async def cancel(self, request_id):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", request_id):
            raise ProviderFault("uncertain", "Invalid cancellation request ID.")
        task = self.active.get(request_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        directory = self.config.jobs_dir.resolve()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / f"{request_id}.json"
        if path.exists():
            try:
                if path.stat().st_size > 2 * MAX_OUTPUT:
                    return "uncertain"
                receipt = strict_json(path.read_bytes())
                if receipt.get("provider") != "claude" or receipt.get("request_id") != request_id:
                    return "uncertain"
                if receipt.get("state") == "complete":
                    return "completed"
                if receipt.get("state") == "running":
                    return "uncertain"
            except (ValueError, TypeError, AttributeError):
                return "uncertain"
        else:
            receipt = {"provider": "claude", "request_id": request_id, "input_digest": None}
        atomic_json(path, {**receipt, "state": "cancelled", "cancelled_at": time.time()})
        return "cancelled"


def runner_from_environment():
    auth, jobs = os.environ.get("CLAUDE_CONFIG_DIR"), os.environ.get("CLAUDE_JOBS_DIR")
    if not auth or not jobs:
        raise ProviderFault("unavailable", "Configure isolated Claude auth and jobs directories.")
    return ClaudeRunner(ClaudeConfig(Path(auth), Path(jobs),
        usage_credits_disabled_confirmed=os.environ.get("CLAUDE_USAGE_CREDITS_DISABLED_CONFIRMED") == "true"))
