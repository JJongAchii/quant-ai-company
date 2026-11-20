"""Route each frozen request by its model; never substitute providers after a fault."""

import asyncio
import re

from ..contracts import ProviderFault

CLAUDE_MODEL = "claude-opus-5-5"
# News screening and Quant Scout run on Sonnet; the briefing, the news desk, the video and staff on Opus (2026-11-17).
CLAUDE_SONNET = "claude-sonnet-5-5"
CLAUDE_MODEL_IDS = frozenset({CLAUDE_MODEL, CLAUDE_SONNET})
CLAUDE_MODELS = [{"model": model, "reasoning_efforts": ["low", "medium", "high", "xhigh", "max"]}
                 for model in (CLAUDE_MODEL, CLAUDE_SONNET)]


def is_claude(model):
    return model in CLAUDE_MODEL_IDS


# Every company turn on Claude runs on its own CLI slot (claude_runner.LOCKS), as on Codex (codex_runner lanes): the
# briefing, the news desk, Quant Scout and the rest of the company (staff, research). The Shorts and the email have
# their own too (2026-11-18, 2026-11-20); the original slot stays with the video, so an episode never waits for company
# work and company work never waits for an episode (owner, 2026-11-17: the video must never be late). Matched by request ID, in the order Codex matches them.
OWN_LANES = (("brief", re.compile(r"^news-brief-")), ("quant", re.compile(r"^quant-feed-")),
             ("news", re.compile(r"^news-")))


def own_lane(request):
    """The CLI slot of a Claude company turn: 'brief', 'quant', 'news', or 'company' for staff and research."""
    if not is_claude(request.model):
        return None
    return next((lane for lane, pattern in OWN_LANES if pattern.match(request.request_id)), "company")


def expected_provider(settings, model):
    if settings.model_provider == "fixture":
        return "fixture"
    return "claude" if is_claude(model) else "codex"


class CompanyProvider:
    def __init__(self, company, codex, claude):
        self.company, self.codex, self.claude = company, codex, claude
        self.routes = {}

    async def run(self, request):
        provider = self.claude if is_claude(request.model) else self.codex
        self.routes[request.request_id] = provider
        try:
            from .claude_company import company_request

            # Only work that would share the episode's slot yields to it; every company turn has a slot of its own.
            if provider is self.claude and not company_request(request):
                from ..newsletter.store import NewsletterStore

                if await asyncio.to_thread(NewsletterStore(self.company).video_needs_model):
                    raise ProviderFault("busy", "Episode production has priority on the Claude subscription lane.", 60)
            return await provider.run(request)
        finally:
            self.routes.pop(request.request_id, None)

    async def cancel(self, request_id):
        provider = self.routes.get(request_id)
        if provider is None:
            # A worker may restart between dispatch and cancellation. Selection is durable.
            row = await asyncio.to_thread(self.binding, request_id)
            provider = self.claude if row and is_claude(row["selection"]["model"]) else self.codex
        return await provider.cancel(request_id)

    def binding(self, request_id):
        with self.company.db.transaction() as conn:
            return conn.execute("SELECT selection FROM model_execution_bindings WHERE request_id=%s",
                                (request_id,)).fetchone()
