"""Writes the email on claude-runtime, in the video worker (the process that already holds that runtime's token).

One turn per tick, with its own queue and records: a failure here never touches a video job or the brief. The
request is frozen before it is sent, so a turn still running is asked again by the same ID."""

import asyncio
import logging

from pydantic import ValidationError
from temporalio import activity

from ..contracts import ProviderFault
from ..providers.claude_runner import ClaudeRequest
from ..providers.client import RuntimeClient
from . import checks
from . import store as records
from .contracts import NewsletterDraft, prompt
from .store import NewsletterStore

LOG = logging.getLogger(__name__)
# A short email: lighter than the episode script so it is ready well inside the 45-minute window.
EFFORT = "high"
MODEL_WAIT_SECONDS = 120
WAITS = {"busy": 20, "quota": 900, "pending": 20, "unavailable": 60, "auth": 300}


def contract_detail(error):
    return "; ".join(".".join(map(str, e["loc"])) + ": " + e["msg"]
                     for e in error.errors(include_url=False, include_input=False))[:1500]


class NewsletterWriter:
    def __init__(self, company, provider=None):
        self.company, self.settings = company, company.settings
        self.store = NewsletterStore(company)
        self._provider = provider

    @property
    def provider(self):
        if self._provider is None:
            self._provider = RuntimeClient(self.settings.video_model_runtime_url,
                                           self.settings.model_runtime_token.get_secret_value(),
                                           timeout_seconds=MODEL_WAIT_SECONDS, expected_provider="claude")
        return self._provider

    def request(self, issue):
        number = len(issue["attempts"]) + 1
        identity = f"newsletter-{issue['id']}-{number}"
        if issue["request"] and issue["request"]["request_id"] == identity:
            return ClaudeRequest.model_validate(issue["request"])
        # A rewrite gets the last written version and what the checks (or the contract) rejected in the last turn.
        written = [a for a in issue["attempts"] if a.get("draft")]
        last = issue["attempts"][-1] if issue["attempts"] else None
        feedback = (last.get("findings") or [f"{last['outcome']}: {last.get('detail', '')}"]) if last else []
        request = ClaudeRequest(request_id=identity, model=self.settings.video_model, reasoning_effort=EFFORT,
                                prompt=prompt(issue["source"], written[-1]["draft"] if written else None, feedback),
                                output_contract="newsletter_v1")
        self.store.freeze_request(issue, request.model_dump(mode="json"))
        return request

    async def tick(self):
        issue = await asyncio.to_thread(self.store.claim_writing)
        if not issue:
            return {"state": "idle"}
        try:
            return {"issue": str(issue["id"]), **await self.write(issue)}
        finally:
            await asyncio.to_thread(self.store.release, issue)

    async def write(self, issue):
        request = await asyncio.to_thread(self.request, issue)
        record = {"n": len(issue["attempts"]) + 1, "request_id": request.request_id, "at": records.utcnow().isoformat()}
        try:
            response = await self.provider.run(request)
        except ProviderFault as error:
            if error.code in WAITS:
                # Nothing was produced (or the turn still runs): the same request is asked again later.
                await asyncio.to_thread(self.store.defer, issue, error.retry_after_seconds or WAITS[error.code],
                                        error.code)
                return {"state": "waiting", "code": error.code}
            await asyncio.to_thread(self.store.attempt, issue, {**record, "outcome": error.code,
                                                               "detail": str(error.message)[:500]})
            return {"state": "attempted", "outcome": error.code}
        decision = response.decision
        try:
            if (response.provider != "claude" or response.usage.get("actual_model") != request.model
                    or response.request_id != request.request_id or len(decision.artifacts) != 1
                    or decision.status != "complete" or decision.tools or decision.delegations or decision.messages
                    or decision.memories or decision.follow_up or response.web_searches):
                raise ValueError("newsletter response identity mismatch")
            draft = NewsletterDraft.model_validate_json(decision.artifacts[0].content)
        except ValidationError as error:
            await asyncio.to_thread(self.store.attempt, issue, {**record, "outcome": "invalid_output",
                                                               "detail": contract_detail(error)})
            return {"state": "attempted", "outcome": "invalid_output"}
        except ValueError as error:
            await asyncio.to_thread(self.store.attempt, issue, {**record, "outcome": "invalid_output",
                                                               "detail": str(error)})
            return {"state": "attempted", "outcome": "invalid_output"}
        found = checks.findings(draft, issue["source"])
        record.update(usage=response.usage)
        if found:
            await asyncio.to_thread(self.store.attempt, issue, {**record, "outcome": "check_failed", "findings": found,
                                                               "draft": draft.model_dump(mode="json")})
            return {"state": "attempted", "outcome": "check_failed", "findings": len(found)}
        accepted = await asyncio.to_thread(self.store.accept, issue, draft, {**record, "outcome": "passed"})
        return {"state": "ready" if accepted else "superseded"}

    @activity.defn(name="company_newsletter_tick")
    async def activity_tick(self):
        try:
            return await self.tick()
        except Exception as error:  # noqa: BLE001 - the dispatcher's fallback covers a writer that keeps failing
            LOG.error("newsletter writer tick failed: %s", type(error).__name__)
            return {"state": "failed", "error": type(error).__name__}
