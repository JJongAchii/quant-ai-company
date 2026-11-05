"""Owner-enabled Responses API credits before the existing ChatGPT subscription.

The grant is an operator-confirmed remaining allowance, not an inferred OpenAI balance.
All four runtime lanes and both containers share the locked, durable grant ledger.
"""

import asyncio
import fcntl
import hashlib
import json
import math
import re
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import Field, field_validator, model_validator

from ..contracts import ProviderFault, StrictModel
from .codex_runner import (
    MAX_RECEIPT_BYTES,
    ProcessResult,
    atomic_json,
    cli_prompt,
    output_schema,
    parse_result,
    strict_json,
)

API_URL = "https://api.openai.com/v1/responses"
MAX_OUTPUT_TOKENS = 64000
# Standard, global processing; USD / 1M tokens. Official pricing checked 2026-11-05.
# https://developers.openai.com/api/docs/pricing (including the prompt-caching guide)
# Only these models can use the API; every other selection keeps its subscription path.
PRICES = {"gpt-6-astra": (10, 1, 50), "gpt-6.1-sol": (2, .1, 10)}
DENIALS = {400, 401, 403, 404, 429}
EXHAUSTED = {"credit_balance_exhausted", "organization_spend_limit_exceeded",
             "project_spend_limit_exceeded", "organization_usage_limit_exceeded", "insufficient_quota"}


class CreditGrant(StrictModel):
    version: Literal[1]
    grant_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    budget_usd: float = Field(gt=0, le=200, allow_inf_nan=False)
    starts_at: datetime
    expires_at: datetime
    free_only_confirmed: Literal[True]

    @field_validator("free_only_confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit free-only billing confirmation is required")
        return value

    @model_validator(mode="after")
    def valid_window(self):
        if (self.starts_at.tzinfo is None or self.expires_at.tzinfo is None
                or not 0 < (self.expires_at - self.starts_at).total_seconds() <= 32 * 86400):
            raise ValueError("Use an explicit, timezone-aware subscription credit cycle")
        return self


def rounded(value):
    return math.ceil(value * 1e6) / 1e6


def cost_ceiling(model, usage):
    """Conservative charge: assume every uncached token incurs a 30m cache write.

    Reasoning is already included in output_tokens. Long-context multipliers apply
    to the whole request; neither cached input nor reasoning is counted twice.
    """
    price, cached, output = PRICES[model], usage.get("cached_input_tokens", 0), usage["output_tokens"]
    raw = usage["input_tokens"]
    long_context = raw > 272000
    return rounded(((raw - cached) * price[0] * 1.25 * (2 if long_context else 1)
                    + cached * price[1] * (2 if long_context else 1)
                    + output * price[2] * (1.5 if long_context else 1)) / 1e6)


def request_body(request):
    return {"model": request.model, "input": cli_prompt(request).decode(), "store": False,
            "service_tier": "default", "max_output_tokens": MAX_OUTPUT_TOKENS,
            "reasoning": {"effort": request.reasoning_effort or "high"},
            "prompt_cache_options": {"mode": "implicit", "ttl": "30m"},
            "text": {"format": {"type": "json_schema", "name": "company_output",
                                "strict": True, "schema": output_schema(request)}}}


def token_usage(data):
    raw = data["usage"]
    values = {"input_tokens": raw["input_tokens"], "output_tokens": raw["output_tokens"],
              "cached_input_tokens": raw.get("input_tokens_details", {}).get("cached_tokens", 0),
              "reasoning_output_tokens": raw.get("output_tokens_details", {}).get("reasoning_tokens", 0)}
    if (any(type(v) is not int or not 0 <= v <= 2**53 for v in values.values())
            or values["cached_input_tokens"] > values["input_tokens"]
            or values["reasoning_output_tokens"] > values["output_tokens"]):
        raise ValueError("Invalid usage")
    return values


def typed_result(request, data, usage):
    """Use the same decision/domain validation as the subscription transport."""
    if (data.get("status") != "completed" or not isinstance(data.get("model"), str)
            or not re.fullmatch(re.escape(request.model) + r"(?:-\d{4}-\d{2}-\d{2})?", data["model"])):
        raise ProviderFault("invalid_output", "The API did not confirm the selected model and completed output.")
    messages = []
    for item in data["output"]:
        if item["type"] == "reasoning":
            continue
        if item["type"] != "message" or item.get("role") != "assistant":
            raise ProviderFault("uncertain", "The API reported an unexpected tool or output action.")
        for content in item["content"]:
            if content["type"] != "output_text":
                raise ProviderFault("invalid_output", "The API did not return the required structured text.")
            messages.append(content["text"])
    if len(messages) != 1:
        raise ProviderFault("invalid_output", "The API did not return one structured final message.")
    events = [{"type": "item.completed", "item": {"type": "agent_message", "text": messages[0]}},
              {"type": "turn.completed", "usage": usage}]
    process = ProcessResult(0, b"\n".join(json.dumps(e).encode() for e in events), b"")
    return parse_result(request, process, 900)


class OpenAICreditLane:
    def __init__(self, directory, grant_file, key_file, *, transport=None, clock=time.time):
        self.directory = Path(directory)
        self.grant_file = Path(grant_file) if grant_file else None
        self.key_file = Path(key_file) if key_file else None
        self.transport, self.clock = transport, clock

    @contextmanager
    def locked(self, grant):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.directory / ".budget.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            name = hashlib.sha256(grant.grant_id.encode()).hexdigest()
            path = self.directory / (name + ".json")
            binding = grant.model_dump(mode="json")
            ledger = {"grant": binding, "entries": {}, "paused_until": 0}
            if path.exists():
                try:
                    if path.stat().st_size > 8 * 1024 * 1024:
                        raise ValueError("Ledger too large")
                    ledger = strict_json(path.read_bytes())
                    if (ledger["grant"] != binding or not isinstance(ledger["entries"], dict)
                            or not self.amount(ledger["paused_until"])):
                        raise ValueError("Invalid ledger")
                    for key, entry in ledger["entries"].items():
                        if (not isinstance(key, str) or not self.amount(entry["reserved"])
                                or entry["settled"] is not None and not self.amount(entry["settled"])):
                            raise ValueError("Invalid ledger entry")
                except (ValueError, KeyError, TypeError, AttributeError):
                    raise ProviderFault("uncertain", "The API grant ledger requires reconciliation.") from None
            yield ledger
            atomic_json(path, ledger)

    @staticmethod
    def amount(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0

    @staticmethod
    def total(ledger):
        return sum(e["reserved"] if e["settled"] is None else e["settled"]
                   for e in ledger["entries"].values())

    def status(self, grant, ledger, reason):
        atomic_json(self.directory / "status.json", {"reason": reason, "grant_id": grant.grant_id,
                    "budget_usd": grant.budget_usd, "accounted_usd": self.total(ledger),
                    "expires_at": grant.expires_at.isoformat(), "calls": len(ledger["entries"]),
                    "paused_until": ledger["paused_until"], "updated_at": self.clock()})

    def configuration(self, timeout):
        try:
            if not self.grant_file or not self.key_file or self.grant_file.stat().st_size > 16384:
                return None
            grant = CreditGrant.model_validate(strict_json(self.grant_file.read_bytes()))
            if not grant.starts_at.timestamp() <= self.clock() < grant.expires_at.timestamp() - timeout:
                return None
            if self.key_file.stat().st_size > 1024:
                return None
            key = self.key_file.read_text().strip()
            if not re.fullmatch(r"sk-[A-Za-z0-9_-]{16,512}", key):
                return None
            return grant, key
        except (OSError, ValueError, TypeError):
            # No API attempt exists; missing, invalid or expired opt-in uses the subscription.
            return None

    async def run(self, request, path, digest, profile, revision, timeout):
        if (profile != "primary" or request.model not in PRICES or request.reasoning_effort is None
                or request.web_search
                or request.session is not None or request.attachments):
            return None
        # A previous explicit refusal pins this request to the subscription, even after a restart.
        if path.exists():
            previous = strict_json(path.read_bytes())
            if previous.get("state") == "api_refused":
                return None
            # Historical subscription attempts retain their original route.
            return None
        config = self.configuration(timeout)
        if config is None:
            return None
        grant, key = config
        body = request_body(request)
        # UTF-8 bytes bound visible tokens; reserve headroom for server/schema framing.
        reserve = cost_ceiling(request.model, {"input_tokens": len(json.dumps(body, ensure_ascii=False).encode()) + 4096,
                                               "output_tokens": MAX_OUTPUT_TOKENS})
        receipt = {"version": 1, "request_id": request.request_id, "input_digest": digest,
                   "state": "running", "started_at": self.clock(), "backend": "responses_api",
                   "account": {"profile": profile, "revision": revision},
                   "requested_execution": {"model": request.model, "reasoning_effort": request.reasoning_effort},
                   "api_grant_id": grant.grant_id, "api_reserved_usd": reserve}
        # Pin the request before reserving, including across a grant renewal or a crash.
        # A crash here may require reconciliation, but can never hide an earlier dispatch.
        atomic_json(path, receipt)
        with self.locked(grant) as ledger:
            if request.request_id in ledger["entries"]:
                raise ProviderFault("uncertain", "An API reservation already exists; reconcile its receipt.")
            if ledger["paused_until"] > self.clock() or self.total(ledger) + reserve > grant.budget_usd:
                self.status(grant, ledger, "subscription_budget_or_pause")
                atomic_json(path, {**receipt, "state": "api_refused", "not_executed": True})
                return None
            ledger["entries"][request.request_id] = {"reserved": reserve, "settled": None}
            self.status(grant, ledger, "api_reserved")
        try:
            status, data = await self.call(body, key, timeout)
            if status in DENIALS:
                code = (data.get("error") or {}).get("code") if isinstance(data, dict) else None
                pause = grant.expires_at.timestamp() if code in EXHAUSTED else self.clock() + 60
                # Persist refusal before releasing the reservation or handing work to the CLI.
                atomic_json(path, {**receipt, "state": "api_refused", "not_executed": True,
                                   "api_status": status})
                with self.locked(grant) as ledger:
                    ledger["entries"][request.request_id]["settled"] = 0
                    ledger["paused_until"] = max(ledger["paused_until"], pause)
                    self.status(grant, ledger, "subscription_api_refused")
                return None
            if status != 200:
                raise ProviderFault("uncertain", "The API outcome is unknown; reconcile before replacing it.")
            usage = token_usage(data)
            charge = cost_ceiling(request.model, usage)
            with self.locked(grant) as ledger:
                ledger["entries"][request.request_id]["settled"] = charge
                if charge > reserve:
                    ledger["paused_until"] = grant.expires_at.timestamp()
                self.status(grant, ledger, "reservation_exceeded" if charge > reserve else "api_completed")
            if charge > reserve:
                raise ProviderFault("uncertain", "The API usage exceeded its reservation; reconcile the grant.")
            result = typed_result(request, data, usage)
            result = result.model_copy(update={"usage": {**result.usage, "backend": "responses_api",
                "billing_mode": "api_credit", "api_grant_id": grant.grant_id,
                "budget_charge_usd": charge, "budget_charge_is_upper_bound": True, "actual_model": data["model"]}})
            atomic_json(path, {**receipt, "state": "complete", "completed_at": self.clock(),
                               "result": result.model_dump(mode="json")})
            return result
        except asyncio.CancelledError:
            fault = ProviderFault("uncertain", "The API turn was cancelled after dispatch; reconcile its receipt.")
            self.save_fault(path, receipt, fault)
            raise
        except ProviderFault as fault:
            self.save_fault(path, receipt, fault)
            raise
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            fault = ProviderFault("uncertain", "The API outcome could not be committed; reconcile its receipt.")
            self.save_fault(path, receipt, fault)
            raise fault from None

    @staticmethod
    def save_fault(path, receipt, fault):
        atomic_json(path, {**receipt, "state": "failed", "fault": {
            "code": fault.code, "message": fault.message, "retry_after_seconds": fault.retry_after_seconds}})

    async def call(self, body, key, timeout):
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=timeout, trust_env=False,
                                         follow_redirects=False) as client:
                async with client.stream("POST", API_URL, json=body,
                    headers={"Authorization": "Bearer " + key}) as response:
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_RECEIPT_BYTES:
                            raise ProviderFault("uncertain", "The API response exceeded its bounded transport.")
                    try:
                        data = strict_json(raw)
                    except ValueError:
                        if response.status_code in DENIALS:
                            data = {}
                        else:
                            raise
                    return response.status_code, data
        except (httpx.HTTPError, ValueError):
            # Connect/timeouts and 5xx never silently replay a potentially billed request.
            raise ProviderFault("uncertain", "The API transport outcome is unknown; reconcile its receipt.") from None
