import asyncio
import json
import logging
from datetime import UTC, datetime
from functools import partial

from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.web.async_client import AsyncWebClient

from quant_company.company import Company
from quant_company.config import Settings
from quant_company.slack import SlackIngress
from quant_company.socket_mode import RedactSlackSecrets, accept_envelope


async def check():
    c = Company(Settings())
    credentials = json.loads(c.settings.slack_credentials_file.read_text())
    cred = credentials['trend_scout']
    ingress = SlackIngress(c.settings,c,credentials)
    log = logging.getLogger('quant_company.trend_window_socket_probe')
    log.addFilter(RedactSlackSecrets())
    log.setLevel(logging.WARNING)
    web = AsyncWebClient(token=cred['bot_token'],logger=log)
    auth = await web.auth_test()
    assert auth['team_id']==c.settings.slack_team_id and auth['user_id']==cred['bot_user_id']
    scopes = set(auth.headers.get('x-oauth-scopes','').split(','))
    assert {'chat:write','channels:history','app_mentions:read'}<=scopes
    socket = SocketModeClient(app_token=cred['app_token'],web_client=web,logger=log,auto_reconnect_enabled=False)
    socket.socket_mode_request_listeners.append(partial(accept_envelope,ingress,'trend_scout'))
    try:
        await asyncio.wait_for(socket.connect(),45)
        assert await socket.is_connected()
    finally:
        await socket.close()
    return {'checked_at':datetime.now(UTC).isoformat(),'mode':'short-lived authenticated SDK probe using actual socket container configuration; original socket stays running',
            'authenticated_socket_connected':True,'team_id':auth['team_id'],'bot_user_id':auth['user_id'],
            'app_id':cred['app_id'],'scopes':sorted(scopes),'messages_sent':0,'credential_values_logged':False}


try:
    print(json.dumps(asyncio.run(check())))
except Exception as exc:
    print(json.dumps({'state':'failed','error_type':type(exc).__name__}))
    raise SystemExit(1) from None
