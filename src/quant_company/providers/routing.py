"""Route each frozen request by its model; never substitute providers after a fault."""

import asyncio
import re

from ..contracts import ProviderFault

CLAUDE_MODEL = "claude-opus-5-5"
# High-volume news desk work runs on Sonnet; the briefing, the video and staff keep Opus (owner, 2026-11-17).
CLAUDE_SONNET = "claude-sonnet-5-5"
CLAUDE_MODEL_IDS = frozenset({CLAUDE_MODEL, CLAUDE_SONNET})
CLAUDE_MODELS = [{"model": model, "reasoning_efforts": ["low", "medium", "high", "xhigh", "max"]}
                 for model in (CLAUDE_MODEL, CLAUDE_SONNET)]


def is_claude(model):
    return model in CLAUDE_MODEL_IDS


# The briefing, the news desk and Quant Scout run on their own Claude CLI slots (claude_runner.LOCKS), as they do on
# Codex (codex_runner lanes): an episode never waits for them and they never wait for an episode. Matched by the
# service-owned request ID of each pipeline, in the order Codex matches them.
OWN_LANES = (("brief", re.compile(r"^news-brief-")), ("quant", re.compile(r"^quant-feed-")),
             ("news", re.compile(r"^news-")))


def own_lane(request):
    """'brief', 'quant' or 'news' for such a Claude turn; None for work on the shared slot (video, email, staff)."""
    if not is_claude(request.model):
        return None
    return next((lane for lane, pattern in OWN_LANES if pattern.match(request.request_id)), None)


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
            if provider is self.claude and not own_lane(request):
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
