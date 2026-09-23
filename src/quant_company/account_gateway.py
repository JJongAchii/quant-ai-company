"""Private account routing for workers whose research code must stay pinned.

Only the application gateway has database/Temporal credentials. The isolated
Codex runtime still receives just a frozen model request and a profile selection.
"""

from contextlib import asynccontextmanager

from .accounts import AccountControl, AccountProvider
from .company import Company
from .config import Settings
from .contracts import ProviderFault
from .providers.client import RuntimeClient
from .providers.codex_runtime import create_app as private_app
from .runtime import connect, make_accounts_worker


class PinnedWorkerProvider(AccountProvider):
    async def run(self, request):
        try:
            return await super().run(request)
        except ProviderFault as fault:
            if fault.code == "quota":
                # Old workflow histories cannot handle the new wake signal. Poll
                # the durable account gate while its full model cooldown remains
                # in PostgreSQL; this does not retry inference every 30 seconds.
                raise ProviderFault("quota", fault.message, min(fault.retry_after_seconds or 30, 30)) from None
            raise


def create_app(*, company=None, client=None, token=None):
    def provider():
        office = company or Company(Settings())
        if not office.settings.model_accounts_enabled or office.settings.model_provider != "codex":
            raise ProviderFault("unavailable", "Explicit subscription account routing is required.")
        upstream = client or RuntimeClient(office.settings.model_runtime_url,
            office.settings.model_runtime_token.get_secret_value(),
            timeout_seconds=office.settings.company_model_timeout_seconds)
        return PinnedWorkerProvider(office, upstream)

    app = private_app(runner_factory=provider, token=token)
    transport_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        app.state.runner = provider()
        office = app.state.runner.company
        temporal = await connect(office.settings)
        control = AccountControl(office, app.state.runner.client, temporal)
        async with transport_lifespan(app), make_accounts_worker(temporal, office, control):
            yield

    app.router.lifespan_context = lifespan
    return app


app = create_app()
