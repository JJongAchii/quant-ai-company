"""Domain-free Slack ingress; authenticated SDK sockets feed the same durable inbox."""

import asyncio
import logging
import re
import signal
from functools import partial

from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.web.async_client import AsyncWebClient

from .company import Company, PolicyError
from .slack import SlackIngress

log = logging.getLogger("quant_company.socket")


class RedactSlackSecrets(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        message = re.sub(r"x(?:app|ox[bpars])-[A-Za-z0-9-]+", "[SLACK_TOKEN]", message)
        message = re.sub(r"wss://\S+", "[SLACK_SOCKET_URL]", message)
        record.msg, record.args = message, ()
        # SDK exception text may repeat a URL/token outside the formatted message.
        record.exc_info = record.exc_text = None
        return True


async def accept_envelope(ingress, role, client, request):
    if not request.envelope_id:
        return
    if request.type == "events_api":
        try:
            if not isinstance(request.payload, dict) or request.payload.get("type") != "event_callback":
                raise PolicyError("Invalid Socket Mode event")
            await asyncio.to_thread(ingress.accept, role, request.payload, ingress.credentials[role])
        except (PolicyError, KeyError, TypeError, ValueError):
            # Permanent policy rejection: acknowledge, but never create work.
            log.warning("Slack event rejected by policy for %s", role)
        except Exception:
            # Transient DB failure: no ACK, so a redelivery can safely retry.
            log.error("Slack event was not committed for %s; awaiting redelivery", role)
            return
    await client.send_socket_mode_response(SocketModeResponse(envelope_id=request.envelope_id))


async def socket_main(settings, *, company=None, credentials=None, client_factory=SocketModeClient, stop=None):
    company = company or Company(settings)
    ingress = SlackIngress(settings, company, credentials)
    active = [role.id for role in company.roles.values() if role.active]
    if not settings.slack_team_id or not settings.slack_allowed_users:
        raise ValueError("Socket Mode requires a workspace ID and allowed user IDs")
    for role in active:
        credential = ingress.credentials.get(role, {})
        if (not all(credential.get(key) for key in ["app_id", "bot_user_id", "bot_token"])
                or not credential.get("app_token", "").startswith("xapp-")):
            raise ValueError(f"Socket Mode credentials are incomplete for {role}")

    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    registered = []
    for sig in [signal.SIGTERM, signal.SIGINT]:
        try:
            loop.add_signal_handler(sig, stop.set)
            registered.append(sig)
        except (NotImplementedError, RuntimeError, ValueError):
            pass
    sdk_log = logging.getLogger("quant_company.slack_sdk")
    sdk_log.setLevel(logging.WARNING)
    if not any(isinstance(item, RedactSlackSecrets) for item in sdk_log.filters):
        sdk_log.addFilter(RedactSlackSecrets())
    clients = []
    try:
        for role in active:
            credential = ingress.credentials[role]
            client = client_factory(app_token=credential["app_token"], logger=sdk_log,
                                    web_client=AsyncWebClient(token=credential["bot_token"], logger=sdk_log),
                                    auto_reconnect_enabled=True, trace_enabled=False)
            clients.append(client)
            client.socket_mode_request_listeners.append(partial(accept_envelope, ingress, role))
            await asyncio.wait_for(client.connect(), timeout=45)
        log.info("Slack Socket Mode connected for %d employees", len(clients))
        await stop.wait()
    except Exception:
        raise RuntimeError("Slack Socket Mode could not stay connected; check credentials and connectivity") from None
    finally:
        await asyncio.gather(*(client.close() for client in clients), return_exceptions=True)
        for sig in registered:
            loop.remove_signal_handler(sig)
