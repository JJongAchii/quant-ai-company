"""Official Claude subscription CLI, with no model tools, and the owner's opt-in API credit lane.

The account owner must disable usage credits. CLI isolation prevents API-key and
model/configuration fallback; it cannot change the account's billing preference.
The API credit lane (claude_api, spec 2026-10-23) stays off unless enabled with a cap above 0. Its key is read from
its own file only for the API request header; the CLI environment never carries it.
"""

import asyncio
import fcntl
import hashlib
import json
import logging
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
from .claude_api import API_VERSION, PRICES, ApiNotExecuted, Budget, call, cost_usd, read_key, reservation_usd
from .codex_runner import ProcessRunner, atomic_json, request_digest, strict_json

LOG = logging.getLogger(__name__)

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
    # The owner's API credit lane: off unless enabled with a cap above 0 (USD over a rolling 31 days).
    api_enabled: bool = False
    api_cap_usd: float = 0.0
    api_key_file: Path | None = None

    def __post_init__(self):
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 600:
            raise ValueError("Invalid Claude timeout")
        if not math.isfinite(self.video_timeout_seconds) or not 0 < self.video_timeout_seconds <= 10800:
            raise ValueError("Invalid Claude video timeout")
        if not math.isfinite(self.newsletter_timeout_seconds) or not 0 < self.newsletter_timeout_seconds <= 1800:
            raise ValueError("Invalid Claude newsletter timeout")
        if not math.isfinite(self.api_cap_usd) or not 0 <= self.api_cap_usd <= 1000:
            raise ValueError("Invalid Claude API cap")

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
# Contracts the owner approved for the API credit lane first; every other contract uses the subscription first and
# reaches the API only when the subscription refused before running anything (auth or quota).
API_FIRST = frozenset({"video_shorts_v1", "newsletter_v1", "video_performance_v1", "video_weekly_v1",
                       "video_answer_v1", "agent_decision", "video_screen_fix_v1"})
# A Shorts is reviewed and inspected like the episode, with the episode's own contracts (2026-10-27): its editors' turns
# are Shorts work, so they take the API lane first too. They are told apart from the episode's by request ID.
SHORTS_REQUEST = re.compile(r"^video-[0-9a-f-]{36}-shorts-")
SHORTS_SHARED = frozenset({"video_review_v1", "video_visual_review_v1"})
API_SLOTS = 2
UNCERTAIN_PAUSE_SECONDS = 1800
# API outcomes that may have executed: the same request ID never goes to the API again (spec 9-3).
API_SPENT = frozenset({"consumed", "uncertain", "timeout"})
CLI_OUTCOMES = frozenset({"auth", "quota", "uncertain", "invalid_output", "timeout"})
TOKENS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
REVIEW_INVALID = "Claude output did not satisfy the independent review contract."


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


def api_first(request):
    """The owner's API-first work (API_FIRST), and a Shorts editor's turn under an episode contract."""
    return request.output_contract in API_FIRST or (request.output_contract in SHORTS_SHARED
                                                     and bool(SHORTS_REQUEST.match(request.request_id)))


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


def code(value, pattern=r"[a-z_]{1,40}"):
    """A CLI identifier for a private receipt; anything else could carry model text."""
    return value if isinstance(value, str) and re.fullmatch(pattern, value) else "other"


def refused(events, item, offered):
    """The CLI's own refusal of a call to a tool this session never offered (CLI 2.1.x wording).

    Anything else, including an error from a tool that ran, is not proof that nothing executed."""
    name = item.get("name")
    if not isinstance(offered, list) or not isinstance(name, str) or not item.get("id"):
        return False
    for event in events:
        content = event.get("message", {}).get("content", []) if event.get("type") == "user" else []
        for result in content if isinstance(content, list) else []:
            if not isinstance(result, dict) or result.get("type") != "tool_result" or result.get("tool_use_id") != item["id"]:
                continue
            text = result.get("content")
            if isinstance(text, list):
                text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
            return result.get("is_error") is True and isinstance(text, str) and bool(re.match(
                r"<tool_use_error>Error: No such tool available: " + re.escape(name) + r"(?:[.<\s]|$)", text))
    return False


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
            kinds = ",".join(code(e.get("subtype")) for e in results) or "none"
            causes = ",".join(sorted({code(error) for error in errors}))
            raise ProviderFault("uncertain", f"Claude did not confirm a complete review (exit {output.returncode}, "
                                f"result {kinds}" + (f", errors {causes}" if causes else "") + "); reconcile its original ID.")
        start, result = starts[0], results[0]
        if (start.get("model") != request.model or start.get("mcp_servers")
                or set(start.get("tools", [])) - {"StructuredOutput"}):
            raise ProviderFault("uncertain", "Claude reported an unexpected model or tool configuration.")
        attempts = []
        for message in messages:
            for item in message.get("message", {}).get("content", []):
                if item.get("type") == "tool_use" and item.get("name") != "StructuredOutput":
                    name = code(item.get("name"), r"[A-Za-z0-9_]{1,40}")
                    if not refused(events, item, start.get("tools")):
                        raise ProviderFault("uncertain", f"Claude reported a disallowed tool action ({name}).")
                    attempts.append(name)
        # After a refused call, any other failure stays uncertain, as it was before refused calls were accepted.
        if result.get("is_error") is not False or result.get("subtype") != "success" or errors:
            raise ProviderFault("uncertain" if attempts else "invalid_output",
                                "Claude did not return a successful structured review.")
        if result.get("permission_denials"):
            raise ProviderFault("uncertain" if attempts else "invalid_output", "Claude attempted a prohibited action.")
        models = result.get("modelUsage")
        if not isinstance(models, dict) or set(models) != {request.model}:
            raise ProviderFault("uncertain", "The actual review model could not be verified.")
        usage = result.get("usage", {})
        if not isinstance(usage, dict):
            raise ValueError("Invalid usage")
        counts = {}
        for name in TOKENS:
            value = usage.get(name, 0)
            if type(value) is not int or not 0 <= value <= 2**53:
                raise ValueError("Invalid token count")
            counts[name] = value
        # total_cost_usd is a client-side API-equivalent estimate, not a subscription bill.
        counts.update(actual_model=request.model, cli_version=CLI_VERSION, effort=request.reasoning_effort or "high",
                      effort_evidence="requested_cli_argument_not_provider_attested",
                      billing_mode="subscription", account_usage_credits_disabled_owner_confirmed=True)
        if attempts:
            counts["refused_tool_attempts"] = attempts
        return structured_response(request, result["structured_output"], counts)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ProviderFault("invalid_output", REVIEW_INVALID) from None


def structured_response(request, structured, usage):
    """The contract-checked response for one structured output, from the CLI or the API lane."""
    schema, title, say = output_schema(request)
    try:
        review = schema.model_validate(structured)
    except ValidationError as error:
        if request.output_contract == "agent_decision":
            raise ProviderFault("invalid_output", REVIEW_INVALID) from None
        # Contract rules and locations only (no rejected text), kept in the private receipt's fault_detail.
        reasons = "; ".join(".".join(map(str, e["loc"])) + ": " + e["msg"]
                            for e in error.errors(include_url=False, include_input=False))
        raise ProviderFault("invalid_output", "Video output failed its contract: " + reasons[:1500]) from None
    return ProviderResponse(request_id=request.request_id, provider="claude", usage=usage,
                            decision=AgentDecision(status="complete", say=say,
                                artifacts=[ArtifactDraft(title=title, content=review.model_dump_json())]))


def attempts_of(receipt):
    return list(receipt.get("attempts", [])) if receipt else []


def attempt(backend, outcome, at, **detail):
    return {"backend": backend, "outcome": outcome, "at": at, **detail}


class ClaudeRunner:
    def __init__(self, config, *, process_runner=None, source_environment=None, api_transport=None,
                 clock=time.time):
        self.config = config
        self.process = process_runner or ProcessRunner()
        self.source = dict(os.environ if source_environment is None else source_environment)
        self.transport = api_transport
        self.clock = clock
        # API calls take their own slots and never the shared CLI lock (.runtime.lock).
        self.slots = asyncio.Semaphore(API_SLOTS)
        self.budget = None
        self.status_refreshed = False
        self.status_key = None
        # The latest API refusal ({"reason", "at"}), for the status file.
        self.last_refusal = None
        # Request IDs inside run(), in any phase: a second ask of the same ID meanwhile is pending.
        self.claims = set()
        # request ID -> (task, "cli" | "api")
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

    @staticmethod
    def _receipt(path):
        """The receipt _cached has just verified (None when absent), with a well-formed attempts list."""
        try:
            receipt = strict_json(path.read_bytes())
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            raise ProviderFault("uncertain", "The durable Claude receipt could not be verified.") from None
        attempts = receipt.get("attempts", []) if isinstance(receipt, dict) else None
        if not isinstance(attempts, list) or not all(isinstance(item, dict) for item in attempts):
            raise ProviderFault("uncertain", "The durable Claude receipt could not be verified.")
        return receipt

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
            fault = ProviderFault("auth", "Authenticate the isolated runtime using its Pro/Max account.")
            fault.unexecuted = "preflight"  # nothing ran and nothing was recorded
            raise fault from None

    # The API credit lane.

    def _lane_on(self):
        return self.config.api_enabled and self.config.api_cap_usd > 0

    def _budget(self):
        if self.budget is None:
            self.budget = Budget(self.config.jobs_dir.resolve() / ".api", self.config.api_cap_usd, self.clock)
        return self.budget

    def _status(self):
        try:
            self._budget().write_status(enabled=self.config.api_enabled, key_present=self._key_present(),
                                        last_refusal=self.last_refusal)
        except Exception as error:  # noqa: BLE001 - logged by type only; the lane's own checks fail closed
            # The status file only informs operators; a failed write must not cost a result or a turn.
            LOG.warning("Claude API status file not written: %s", type(error).__name__)

    def _key_present(self):
        """Whether the key file holds a key (never the key itself), as the status file last said it."""
        self.status_key = read_key(self.config.api_key_file) is not None
        return self.status_key

    def _refresh_status(self):
        """The first turn after a start rewrites the status: a restart ends key pauses, a switch-off the lane."""
        if self.status_refreshed:
            return
        self.status_refreshed = True
        try:
            wanted = self._lane_on() or (self.config.jobs_dir.resolve() / ".api").is_dir()
        except OSError:
            wanted = self._lane_on()
        if wanted:
            self._status()

    @staticmethod
    def _quietly(operation, *args):
        """A ledger write after an API call. A failure is logged by type and never loses the receipt or the result;
        an unsettled reservation keeps counting in full."""
        try:
            operation(*args)
            return True
        except Exception as error:  # noqa: BLE001
            LOG.warning("Claude API ledger %s failed: %s", operation.__name__, type(error).__name__)
            return False

    def _pause(self, budget, reason, until, persist=True):
        if not self._quietly(budget.pause, reason, until, persist):
            budget.memory_pauses[reason] = until  # a ledger that cannot be written still pauses this runtime

    def _lane(self, request, attempts, route):
        """(key, ledger ID, reserved USD) for one API attempt, or None when the API may not take this request."""
        if not self._lane_on() or request.model not in PRICES:
            return None
        if any(item.get("backend") == "api" and item.get("outcome") in API_SPENT for item in attempts):
            return None
        key = read_key(self.config.api_key_file)
        if (key is not None) != self.status_key:
            self._status()  # the key file was filled or emptied since the status file last said
        if key is None:
            return None
        # Attempt-qualified, so settling or releasing one attempt never touches another of the same request.
        ledger_id = f"{request.request_id}#{len(attempts)}"
        amount = reservation_usd(request)
        try:
            budget = self._budget()
            if budget.paused() is not None:
                return None
            reserved = budget.reserve(ledger_id, request.model, amount, route)
        except Exception as error:  # noqa: BLE001 - an unreadable or broken ledger closes the lane
            LOG.warning("Claude API lane closed, ledger unavailable: %s", type(error).__name__)
            return None  # subscription work goes on: API-first to the CLI, a fallback keeps the CLI's fault
        self._status()
        return (key, ledger_id, amount) if reserved else None

    async def _run_api(self, request, path, digest, attempts, lane, restore):
        """One API attempt. When it did not execute, `restore` plus this attempt becomes the receipt again."""
        key, ledger_id, reserved = lane
        budget = self._budget()
        schema, title, _ = output_schema(request)
        effort = request.reasoning_effort or "high"
        receipt = {"provider": "claude", "request_id": request.request_id, "input_digest": digest,
                   "state": "running", "started_at": time.time(), "backend": "api", "api_version": API_VERSION,
                   "requested_execution": {"model": request.model, "reasoning_effort": effort}, "attempts": attempts}

        def failed(code, detail):
            atomic_json(path, {**receipt, "state": "failed", "fault": code, "fault_detail": detail,
                               "retry_at": time.time(), "attempts": attempts + [attempt("api", code, self.clock())]})

        def spent(code, detail, pause=True):
            # It may have run: count the whole reservation and never repeat this ID. An API-side uncertain outcome
            # or a timeout also pauses the lane (spec 9-2); a deliberate cancel is not an API fault and does not.
            failed(code, detail)
            self._quietly(budget.settle, ledger_id, reserved)
            if pause:
                self._pause(budget, "uncertain", self.clock() + UNCERTAIN_PAUSE_SECONDS)
            self._status()

        started = False
        try:
            atomic_json(path, receipt)
            self.active[request.request_id] = (asyncio.current_task(), "api")
            try:
                async with self.slots:
                    started = True
                    structured, usage = await asyncio.wait_for(
                        call(request, schema.model_json_schema(), title, key, transport=self.transport,
                             clock=self.clock),
                        self.config.process_limit(request))
                cost = cost_usd(request.model, usage)
                counts = {name: usage[name] for name in TOKENS}
                counts.update(actual_model=usage["model"], effort=effort, effort_evidence="requested_api_parameter",
                              billing_mode="api_credit", backend="api", cost_usd=round(cost, 6),
                              api_version=API_VERSION)
                try:
                    result = structured_response(request, structured, counts)
                except ProviderFault as fault:
                    fault.usage = usage
                    raise
                atomic_json(path, {**receipt, "state": "complete", "completed_at": time.time(),
                                   "result": result.model_dump(mode="json"),
                                   "attempts": attempts + [attempt("api", "complete", self.clock())]})
                self._quietly(budget.settle, ledger_id, cost)
                self._status()
                return result
            except ApiNotExecuted as refusal:
                detail = {"reason": refusal.reason} | ({"status": refusal.status} if refusal.status else {})
                outcome = "consumed" if refusal.consumed else "not_executed"
                atomic_json(path, {**restore, "attempts": attempts + [attempt("api", outcome, self.clock(), **detail)]})
                self.last_refusal = {"reason": refusal.reason, "at": self.clock()}
                if refusal.pause:
                    self._pause(budget, *refusal.pause)
                if refusal.consumed:
                    self._quietly(budget.settle, ledger_id, reserved)  # a 5xx other than 529 may have run
                else:
                    self._quietly(budget.release, ledger_id)
                self._status()
                raise
            except TimeoutError:
                spent("timeout", "The Claude API call exceeded its time limit; it will not be repeated.")
                raise ProviderFault("timeout", "Claude API timed out; reconcile this request before replacing it.") from None
            except ProviderFault as fault:
                if fault.code != "invalid_output":
                    spent("uncertain", str(fault))
                    raise
                failed("invalid_output", str(fault))
                usage = getattr(fault, "usage", None)
                self._quietly(budget.settle, ledger_id, cost_usd(request.model, usage) if usage else reserved)
                self._status()
                raise
            except asyncio.CancelledError:
                if started:
                    spent("uncertain", "The Claude API call was cancelled while it ran; its outcome is unknown.",
                          pause=False)
                else:  # still waiting for a slot: nothing was sent
                    atomic_json(path, {**restore, "attempts": attempts + [
                        attempt("api", "not_executed", self.clock(), reason="cancelled")]})
                    self._quietly(budget.release, ledger_id)
                    self._status()
                raise
            finally:
                self.active.pop(request.request_id, None)
        except OSError:
            raise ProviderFault("unavailable", "Claude runtime storage is unavailable.") from None

    async def _api_instead(self, request, path, digest, restore, fault):
        """The API takes work the subscription refused before running it; otherwise that refusal stands."""
        route = "api_first" if api_first(request) else "fallback"
        lane = self._lane(request, attempts_of(restore), route)
        if lane is None:
            raise fault
        try:
            return await self._run_api(request, path, digest, attempts_of(restore), lane, restore)
        except ApiNotExecuted:
            raise fault from None

    async def run(self, request: ProviderRequest):
        if request.model not in allowed_models(request) or request.web_search:
            raise ProviderFault("invalid_output", "This runtime accepts only offline Opus 5 structured requests.")
        output_schema(request)
        if request.request_id in self.claims:
            # The API lane does not take the CLI lock, so this claim keeps one ask per ID in every phase
            # (preflight, an API call, a fallback): a second ask can never start a second paid call.
            raise ProviderFault("pending", "This request is still running; ask again with its ID.", 20)
        self.claims.add(request.request_id)
        try:
            return await self._route(request)
        finally:
            self.claims.discard(request.request_id)

    async def _route(self, request):
        directory = self.config.jobs_dir.resolve()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / f"{request.request_id}.json"
        digest = request_digest(request)
        self._refresh_status()
        try:
            cached = self._cached(path, digest, request.request_id)
        except ProviderFault as fault:
            if fault.code != "quota":
                raise
            # Deferred and not yet due: the API may take it now (spec 9-3); otherwise it waits as today.
            return await self._api_instead(request, path, digest, self._receipt(path), fault)
        if cached:
            return cached
        receipt = self._receipt(path)
        if receipt is not None and receipt["state"] == "running":
            if receipt.get("backend") == "api":
                # Not claimed here (run() checked): the runtime restarted during the call, which may have run
                # and been charged. Never replay it.
                raise ProviderFault("uncertain", "A Claude API call was interrupted; it will not be repeated.")
            return await self._run_cli(request, path, digest)
        attempts = attempts_of(receipt)
        if api_first(request):
            lane = self._lane(request, attempts, "api_first")
            if lane is not None:
                restore = receipt or {"provider": "claude", "request_id": request.request_id, "input_digest": digest,
                                      "state": "deferred", "retry_at": time.time(), "backend": "api"}
                try:
                    return await self._run_api(request, path, digest, attempts, lane, restore)
                except ApiNotExecuted:
                    pass
            return await self._run_cli(request, path, digest)
        try:
            return await self._run_cli(request, path, digest)
        except ProviderFault as fault:
            stage = getattr(fault, "unexecuted", None)
            if stage is None:
                raise
            after = self._receipt(path)
            if stage == "preflight":
                if after is not None and after["state"] != "deferred":
                    raise  # changed during the preflight (e.g. cancelled): the API must not take it
                # The CLI recorded nothing. Keep the ID retryable, as with no receipt, while recording the API attempt.
                base = after or {"provider": "claude", "request_id": request.request_id, "input_digest": digest}
                after = {**base, "state": "deferred", "fault": fault.code, "fault_detail": str(fault),
                         "retry_at": time.time(), "backend": "subscription",
                         "attempts": attempts_of(after) + [attempt("subscription", fault.code, self.clock())]}
            return await self._api_instead(request, path, digest, after, fault)

    async def _run_cli(self, request, path, digest):
        directory = path.parent
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
                # Cancellation, or an API turn of this ID from elsewhere, can arrive during the preflight.
                cached = self._cached(path, digest, request.request_id, locked=True)
                if cached:
                    return cached
                # Read the history now, so the running receipt never drops an attempt recorded meanwhile.
                attempts = attempts_of(self._receipt(path))
                receipt = {"provider": "claude", "request_id": request.request_id, "input_digest": digest,
                           "state": "running", "started_at": time.time(), "cli_version": CLI_VERSION,
                           "requested_execution": {"model": request.model,
                                                   "reasoning_effort": request.reasoning_effort or "high"},
                           "backend": "subscription", "attempts": attempts}
                atomic_json(path, receipt)
                self.active[request.request_id] = (asyncio.current_task(), "cli")
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
                                       "result": result.model_dump(mode="json"),
                                       "attempts": attempts + [attempt("subscription", "complete", self.clock())]})
                    return result
                except ProviderFault as fault:
                    outcome = fault.code if fault.code in CLI_OUTCOMES else "uncertain"
                    atomic_json(path, {**receipt, "state": "deferred" if fault.code == "quota" else "failed",
                                       "fault": fault.code, "fault_detail": str(fault),
                                       "retry_at": time.time() + fault.retry_after_seconds,
                                       "attempts": attempts + [attempt("subscription", outcome, self.clock())]})
                    if fault.code in {"auth", "quota"}:
                        fault.unexecuted = "run"  # zero output tokens: refused before running, already recorded
                    raise
                finally:
                    # Cancellation/crash leaves the durable running marker and cannot trigger another inference.
                    self.active.pop(request.request_id, None)
        except OSError:
            raise ProviderFault("unavailable", "Claude runtime storage or executable is unavailable.") from None
        finally:
            os.close(fd)

    async def cancel(self, request_id, shared_only=False):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", request_id):
            raise ProviderFault("uncertain", "Invalid cancellation request ID.")
        task, lane = self.active.get(request_id, (None, None))
        if task and shared_only and lane == "api":
            # An API turn holds no shared slot: cancelling it would only waste a paid call (spec 9-1).
            return "kept"
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
                if lane == "api" and receipt.get("state") == "failed" and receipt.get("fault") == "uncertain":
                    # The cancelled call had started and may have been charged: answer as for a started CLI turn,
                    # keeping the receipt the API path wrote.
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
    key_file = os.environ.get("CLAUDE_API_KEY_FILE")
    try:
        config = ClaudeConfig(Path(auth), Path(jobs),
            usage_credits_disabled_confirmed=os.environ.get("CLAUDE_USAGE_CREDITS_DISABLED_CONFIRMED") == "true",
            api_enabled=os.environ.get("CLAUDE_API_ENABLED") == "true",
            api_cap_usd=float(os.environ.get("CLAUDE_API_CAP_USD") or 0),
            api_key_file=Path(key_file) if key_file else None)
    except ValueError:
        raise ProviderFault("unavailable", "The Claude runtime configuration is invalid.") from None
    return ClaudeRunner(config)
