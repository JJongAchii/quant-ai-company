"""Claude API credit lane: price table, rolling 31-day budget ledger, pauses and status file."""

import fcntl
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ..contracts import ProviderFault
from .codex_runner import atomic_json

WINDOW = 31 * 86400
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
TOOL = "StructuredOutput"
SYSTEM = ("Return your final answer by calling the StructuredOutput tool exactly once, with input that matches "
          "its schema. Do not answer in plain text.")
SPEND_LIMIT_CODE = "enforced_spend_limit_reached"
SHORT_CONTRACTS = ("newsletter_v1", "video_performance_v1", "video_answer_v1", "agent_decision")
# A pause that is active hides the ones after it.
PAUSE_ORDER = ("key", "spend_limit", "uncertain", "rate_limit", "overloaded")


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""
    input: float
    output: float
    cache_write: float
    cache_read: float


PRICES = {
    "claude-opus-5-5": Price(4, 20, 5, 0.20),
    "claude-opus-5": Price(5, 25, 6.25, 0.50),
}


def cost_usd(model, usage):
    price = PRICES[model]
    return (usage.get("input_tokens", 0) * price.input + usage.get("output_tokens", 0) * price.output
            + usage.get("cache_creation_input_tokens", 0) * price.cache_write
            + usage.get("cache_read_input_tokens", 0) * price.cache_read) / 1e6


def max_tokens(contract):
    return 64000 if contract in SHORT_CONTRACTS else 128000


def reservation_usd(request):
    """Worst case: the estimated input plus every output token the call may produce."""
    price = PRICES[request.model]
    tokens = len(request.prompt.encode()) // 2 + 3000 * len(request.attachments)
    return (tokens * price.input + max_tokens(request.output_contract) * price.output) / 1e6


class Budget:
    def __init__(self, directory, cap_usd, clock=time.time):
        self.directory = Path(directory)
        self.cap_usd = cap_usd
        self.clock = clock
        self.memory_pauses = {}
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    @contextmanager
    def locked(self):
        with open(self.directory / ".ledger.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def load(self):
        """The ledger, empty when absent; None when unreadable, so every caller fails closed."""
        try:
            data = json.loads((self.directory / "ledger.json").read_text())
            if not isinstance(data.get("entries"), list) or not isinstance(data.get("pauses", {}), dict):
                return None
            return {"entries": data["entries"], "pauses": data.get("pauses", {})}
        except FileNotFoundError:
            return {"entries": [], "pauses": {}}
        except (OSError, ValueError, AttributeError):
            return None

    def store(self, ledger):
        cutoff = self.clock() - WINDOW
        ledger["entries"] = [entry for entry in ledger["entries"] if entry["at"] > cutoff]
        atomic_json(self.directory / "ledger.json", ledger)

    def live(self, ledger):
        cutoff = self.clock() - WINDOW
        return [entry for entry in ledger["entries"] if entry["at"] > cutoff]

    def total(self, ledger):
        return sum(entry["reserved"] if entry["actual"] is None else entry["actual"] for entry in self.live(ledger))

    def reserve(self, request_id, model, amount, route):
        with self.locked():
            ledger = self.load()
            if ledger is None or self.total(ledger) + amount > self.cap_usd:
                return False
            ledger["entries"].append({"request_id": request_id, "model": model, "route": route,
                                      "at": self.clock(), "reserved": amount, "actual": None})
            self.store(ledger)
            return True

    def settle(self, request_id, amount):
        with self.locked():
            ledger = self.load()
            if ledger is None:
                return
            for entry in ledger["entries"]:
                if entry["request_id"] == request_id:
                    entry["actual"] = amount
            self.store(ledger)

    def release(self, request_id):
        with self.locked():
            ledger = self.load()
            if ledger is None:
                return
            ledger["entries"] = [entry for entry in ledger["entries"] if entry["request_id"] != request_id]
            self.store(ledger)

    def spent(self):
        with self.locked():
            ledger = self.load()
            return 0.0 if ledger is None else self.total(ledger)

    def pause(self, reason, until, persist=True):
        if persist:
            with self.locked():
                ledger = self.load()
                if ledger is not None:
                    ledger["pauses"][reason] = until
                    self.store(ledger)
                    return
        self.memory_pauses[reason] = until

    def paused(self):
        with self.locked():
            ledger = self.load()
        pauses = dict(ledger["pauses"] if ledger else {})
        pauses.update(self.memory_pauses)
        now = self.clock()
        for reason in PAUSE_ORDER:
            if reason in pauses and (pauses[reason] is None or pauses[reason] > now):
                return {"reason": reason, "until": pauses[reason]}
        return None

    def write_status(self, enabled):
        pause = self.paused()
        with self.locked():
            ledger = self.load()
            if ledger is None:
                state, spent, calls, fallback = "ledger_unreadable", 0.0, {}, None
            else:
                live = self.live(ledger)
                spent = self.total(ledger)
                calls = {}
                for entry in live:
                    calls[entry["route"]] = calls.get(entry["route"], 0) + 1
                fallback = max((entry["at"] for entry in live if entry["route"] == "fallback"), default=None)
                state = "off" if not enabled or self.cap_usd <= 0 else "paused" if pause else "on"
            atomic_json(self.directory / "status.json", {
                "state": state, "enabled": enabled, "cap_usd": self.cap_usd, "spent_31d_usd": spent,
                "calls_31d": calls, "last_fallback_at": fallback, "paused": pause, "updated_at": self.clock()})


# Part B: one streaming Messages API call.

def read_key(path):
    """The API key from its file, trimmed; None when the file is absent or empty."""
    if path is None:
        return None
    try:
        return Path(path).read_text().strip() or None
    except OSError:
        return None


class ApiNotExecuted(Exception):
    """The API answered with a non-200 status. pause is (reason, until, persist) or None."""

    def __init__(self, reason, consumed, pause=None):
        super().__init__(f"Claude API not executed: {reason}")
        self.reason = reason
        self.consumed = consumed
        self.pause = pause


def body(request, schema, title):
    content = [{"type": "image", "source": {"type": "base64", "media_type": a.media_type, "data": a.data}}
               for a in request.attachments] + [{"type": "text", "text": request.prompt}]
    return {"model": request.model, "max_tokens": max_tokens(request.output_contract), "stream": True,
            "output_config": {"effort": request.reasoning_effort or "high"}, "system": SYSTEM,
            "tools": [{"name": TOOL, "description": title, "input_schema": schema}],
            "tool_choice": {"type": "auto"},
            "messages": [{"role": "user", "content": content}]}


def next_month_start(now):
    moment = datetime.fromtimestamp(now, UTC)
    year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
    return datetime(year, month, 1, tzinfo=UTC).timestamp()


def error_fields(raw):
    """Only error.type and error.details.error_code; nothing else of an error body is kept."""
    try:
        error = json.loads(raw).get("error")
        details = error.get("details") if isinstance(error, dict) else None
        code = details.get("error_code") if isinstance(details, dict) else None
        return code if isinstance(code, str) else None
    except (ValueError, AttributeError):
        return None


def not_executed(status, headers, raw, now):
    if status == 429:
        if error_fields(raw) == SPEND_LIMIT_CODE:
            return ApiNotExecuted("spend_limit", False, ("spend_limit", next_month_start(now), True))
        try:
            wait = float(headers.get("retry-after", ""))
        except ValueError:
            wait = 60.0
        return ApiNotExecuted("rate_limit", False, ("rate_limit", now + (wait if wait > 0 else 60.0), True))
    if status == 529:
        return ApiNotExecuted("overloaded", False, ("overloaded", now + 60, True))
    if status in (401, 403):
        return ApiNotExecuted("key", False, ("key", None, False))
    if status in (500, 504):
        return ApiNotExecuted("server", True)
    return ApiNotExecuted("rejected", False)


def uncertain(why):
    return ProviderFault("uncertain", f"Claude API outcome unknown: {why}")


async def events(response):
    """(name, data) per SSE event; a blank line ends one."""
    name, data = None, []
    async for line in response.aiter_lines():
        if not line:
            if data:
                try:
                    yield name, json.loads("\n".join(data))
                except ValueError:
                    raise uncertain("unreadable stream event") from None
            name, data = None, []
        elif line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        raise uncertain("stream ended inside an event")


async def call(request, schema, title, key, *, transport=None, clock=time.time):
    """Returns (tool_input, usage). The key goes only into the x-api-key header."""
    headers = {"x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"}
    timeout = httpx.Timeout(connect=10, read=300, write=60, pool=10)
    blocks, usage, stop, model, finished = {}, {}, None, None, False
    try:
        async with httpx.AsyncClient(transport=transport, timeout=timeout, trust_env=False,
                                     follow_redirects=False) as client:
            async with client.stream("POST", API_URL, headers=headers,
                                     content=json.dumps(body(request, schema, title))) as response:
                if response.status_code != 200:
                    raw = await response.aread()
                    raise not_executed(response.status_code, response.headers, raw, clock())
                async for name, data in events(response):
                    kind = data.get("type", name) if isinstance(data, dict) else name
                    if kind == "error":
                        raise uncertain("error event after the stream began")
                    if kind == "message_start":
                        message = data.get("message", {})
                        model = message.get("model")
                        usage.update({k: v for k, v in message.get("usage", {}).items() if isinstance(v, int)})
                    elif kind == "content_block_start":
                        blocks[data["index"]] = {"block": data["content_block"], "json": ""}
                    elif kind == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("type") == "input_json_delta" and data["index"] in blocks:
                            blocks[data["index"]]["json"] += delta.get("partial_json", "")
                    elif kind == "message_delta":
                        stop = data.get("delta", {}).get("stop_reason", stop)
                        if isinstance(data.get("usage", {}).get("output_tokens"), int):
                            usage["output_tokens"] = data["usage"]["output_tokens"]
                    elif kind == "message_stop":
                        finished = True
    except (httpx.HTTPError, KeyError, TypeError, AttributeError):
        raise uncertain("the stream failed") from None
    if not finished:
        raise uncertain("the stream ended before message_stop")
    if model != request.model:
        raise uncertain("the response came from a different model")
    result = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    result.update({k: usage[k] for k in result if k in usage})
    result["model"] = model
    if stop != "tool_use":
        raise ProviderFault("invalid_output", f"Claude API stopped with {stop if isinstance(stop, str) else 'no reason'}")
    for item in blocks.values():
        if item["block"].get("type") == "tool_use" and item["block"].get("name") == TOOL:
            try:
                tool_input = json.loads(item["json"] or "{}")
            except ValueError:
                raise ProviderFault("invalid_output", "StructuredOutput input was not valid JSON") from None
            if not isinstance(tool_input, dict):
                raise ProviderFault("invalid_output", "StructuredOutput input was not an object")
            return tool_input, result
    raise ProviderFault("invalid_output", "No StructuredOutput tool call arrived")
