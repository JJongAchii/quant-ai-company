"""Claude API credit lane: price table, rolling 31-day budget ledger, pauses and status file."""

import fcntl
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .codex_runner import atomic_json

WINDOW = 31 * 86400
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
