"""Route each frozen request by its model; never substitute providers after a fault."""

import asyncio

from ..contracts import ProviderFault

CLAUDE_MODEL = "claude-opus-5-5"
CLAUDE_MODELS = [{"model": CLAUDE_MODEL, "reasoning_efforts": ["low", "medium", "high", "xhigh", "max"]}]


def expected_provider(settings, model):
    if settings.model_provider == "fixture":
        return "fixture"
    return "claude" if model == CLAUDE_MODEL else "codex"


class CompanyProvider:
    def __init__(self, company, codex, claude):
        self.company, self.codex, self.claude = company, codex, claude
        self.routes = {}

    async def run(self, request):
        provider = self.claude if request.model == CLAUDE_MODEL else self.codex
        self.routes[request.request_id] = provider
        try:
            if provider is self.claude:
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
            provider = self.claude if row and row["selection"]["model"] == CLAUDE_MODEL else self.codex
        return await provider.cancel(request_id)

    def binding(self, request_id):
        with self.company.db.transaction() as conn:
            return conn.execute("SELECT selection FROM model_execution_bindings WHERE request_id=%s",
                                (request_id,)).fetchone()
