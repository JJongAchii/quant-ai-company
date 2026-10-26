"""In-process schedules that replaced Temporal (docs/adr/0049-in-process-loops-instead-of-temporal.md).

PostgreSQL holds every durable state and each tick resumes from it, so a loop only decides when to call its tick
again. A lane is one former Temporal task queue: loops that share a lane never tick at the same time, as when the
queue's worker ran one activity at a time."""

import asyncio
import contextlib
import functools
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

LOG = logging.getLogger(__name__)


@dataclass
class Loop:
    name: str
    tick: Callable[[], Awaitable[Any]]
    # None when the tick limits its own work (a lane wait inside it must not count against a limit).
    timeout: float | None
    # Seconds to wait after a tick, or a function of its result (None once every attempt failed).
    delay: float | Callable[[Any], float]
    attempts: int = 1
    retry_seconds: float = 1
    lane: asyncio.Semaphore | None = None

    def wait(self, result) -> float:
        return self.delay(result) if callable(self.delay) else self.delay


async def attempt(name, tick, *, timeout, attempts=1, retry_seconds=1, lane=None):
    """One tick with the old activity's timeout and retries; None when every attempt failed. A timed-out tick is
    cancelled like a lost worker: model requests keep their stable IDs and the next tick resumes them."""
    for number in range(attempts):
        try:
            async with lane or contextlib.nullcontext():
                return await asyncio.wait_for(tick(), timeout)
        except Exception as exc:  # noqa: BLE001 - one failing tick must never stop its loop
            LOG.error("%s tick failed (attempt %s/%s): %s", name, number + 1, attempts, type(exc).__name__)
            if number + 1 < attempts:
                await asyncio.sleep(retry_seconds * 2 ** number)
    return None


async def run(loop: Loop):
    while True:
        result = await attempt(loop.name, loop.tick, timeout=loop.timeout, attempts=loop.attempts,
                               retry_seconds=loop.retry_seconds, lane=loop.lane)
        try:
            wait = loop.wait(result)
        except Exception as exc:  # noqa: BLE001 - a malformed result must not stop the service's other loops
            LOG.error("%s wait failed: %s", loop.name, type(exc).__name__)
            wait = 60
        await asyncio.sleep(wait)


async def run_all(loops):
    if not loops:
        await asyncio.Event().wait()
    await asyncio.gather(*(run(loop) for loop in loops))


class TurnLoop:
    """Runs every queued, running or waiting turn whose time has come, one at a time in the worker's main lane.

    The turn's state is in PostgreSQL (prepare_turn decides ready/defer/done), so after a restart the same turns are
    picked up again. Only when to look at a deferred or failed turn again is kept here, and an account change wakes a
    deferred turn early."""

    def __init__(self, company, executor, lane, timeout=1200):
        self.company, self.executor, self.lane, self.timeout = company, executor, lane, timeout
        self.next_at, self.failures = {}, {}

    def wake(self, turn_id):
        self.next_at.pop(str(turn_id), None)

    async def tick(self):
        due = [str(turn_id) for turn_id in await asyncio.to_thread(self.company.due_turns)]
        # Turns that left the due list are finished or wait on a database due time instead.
        self.next_at = {turn_id: at for turn_id, at in self.next_at.items() if turn_id in due}
        now = time.monotonic()
        turn_id = next((turn_id for turn_id in due if self.next_at.get(turn_id, 0) <= now), None)
        if turn_id is None:
            return {"state": "idle"}
        result = await attempt("turn", lambda: self.executor.execute(turn_id), timeout=self.timeout, lane=self.lane)
        if result is None:
            # Like the old activity: three quick attempts, then a minute before the same durable request again.
            failures = self.failures[turn_id] = self.failures.get(turn_id, 0) + 1
            self.next_at[turn_id] = time.monotonic() + (2 * 2 ** (failures - 1) if failures < 3 else 60)
            if failures >= 3:
                self.failures.pop(turn_id)
            return {"state": "failed", "turn_id": turn_id}
        self.failures.pop(turn_id, None)
        # A turn that is still due after a finished run is looked at again a second later, never in a tight loop.
        wait = max(1, result.get("seconds", 30)) if result.get("state") == "defer" else 1
        self.next_at[turn_id] = time.monotonic() + wait
        return {"state": "ran", "turn_id": turn_id, "result": result.get("state")}


# A question waits this long between tries while an episode is in production; after about seven hours it is closed.
QUESTION_WAIT, QUESTION_TRIES = timedelta(minutes=5), 84


class QuestionLoop:
    """Answers owner questions in video analytics threads, one at a time in the analytics lane. A question asks again
    every five minutes while it has to wait, and is closed with a notice after about seven hours of tries. As with the
    old per-question workflow, time while the video worker was down does not count."""

    def __init__(self, runner, lane):
        self.runner, self.lane = runner, lane
        self.next_at, self.first_try = {}, {}

    async def tick(self):
        if not await asyncio.to_thread(self.runner.store.enabled):
            return {"state": "disabled"}
        pending = await asyncio.to_thread(self.runner.store.pending_questions)
        open_ids = {str(row["id"]) for row in pending}
        self.next_at = {key: at for key, at in self.next_at.items() if key in open_ids}
        self.first_try = {key: at for key, at in self.first_try.items() if key in open_ids}
        now = time.monotonic()
        for row in pending:
            question_id = str(row["id"])
            if self.next_at.get(question_id, 0) > now:
                continue
            if now - self.first_try.setdefault(question_id, now) >= (QUESTION_WAIT * QUESTION_TRIES).total_seconds():
                state = await attempt("video-question-give-up", functools.partial(self.runner.give_up, question_id),
                                      timeout=300, attempts=3, retry_seconds=30, lane=self.lane)
            else:
                state = await attempt("video-question", functools.partial(self.runner.answer, question_id),
                                      timeout=1800, attempts=2, retry_seconds=30, lane=self.lane)
            # Any other answer ends the question's tries in this process, as the old per-question workflow ended.
            self.next_at[question_id] = (time.monotonic() + QUESTION_WAIT.total_seconds()
                                         if state in {None, "waiting", "retry"} else math.inf)
            return {"state": state or "retry", "question": question_id}
        return {"state": "idle"}
