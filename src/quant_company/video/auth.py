"""Explicit account login; media credentials never enter the model runtime."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

RUNWAY_URL = "https://mcp.runwayml.com/mcp"
CALLBACK = "http://127.0.0.1:8766/callback"


def write_secret(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or path.parent.is_symlink() or path.parent.stat().st_mode & 0o077:
        raise ValueError('Media credentials require a private directory without symlinks')
    descriptor, name = tempfile.mkstemp(prefix='.token-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class RunwayTokenStorage:
    def __init__(self, directory):
        self.directory = Path(directory)

    def read(self, name, cls):
        path = self.directory / name
        if not path.exists():
            return None
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError("Media credential files must be private regular files")
        return cls.model_validate_json(path.read_text())

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken

        return self.read("runway-tokens.json", OAuthToken)

    async def set_tokens(self, tokens):
        write_secret(self.directory / "runway-tokens.json", tokens.model_dump(mode="json"))

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull

        return self.read("runway-client.json", OAuthClientInformationFull)

    async def set_client_info(self, value):
        write_secret(self.directory / "runway-client.json", value.model_dump(mode="json"))


def runway_auth(directory, redirect=None, callback=None):
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata

    async def unavailable(*args):
        raise ValueError("Runway login required: use quant-company video-auth runway")

    return OAuthClientProvider(RUNWAY_URL, OAuthClientMetadata(
        client_name="Quant Company daily briefing", redirect_uris=[CALLBACK],
        grant_types=["authorization_code", "refresh_token"], response_types=["code"],
        token_endpoint_auth_method="none"), RunwayTokenStorage(directory),
        redirect_handler=redirect or unavailable, callback_handler=callback or unavailable)


async def login_runway(directory, host='127.0.0.1'):
    """Run on the server through an SSH tunnel for port 8766; never copy browser cookies."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    future = asyncio.get_running_loop().create_future()

    async def receive(reader, writer):
        try:
            request = (await asyncio.wait_for(reader.readline(), 5)).decode()
            target = request.split(" ")[1]
            query = parse_qs(urlsplit(target).query)
            if urlsplit(target).path != "/callback" or not query.get("code"):
                raise ValueError("Invalid callback")
            if not future.done():
                future.set_result((query["code"][0], query.get("state", [None])[0]))
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\nLogin received. Return to the terminal.")
            await writer.drain()
        except (ValueError, IndexError, TimeoutError):
            writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
        finally:
            writer.close()
            await writer.wait_closed()

    async def redirect(url):
        print("Open this Runway authorization URL in your browser:", url, flush=True)

    async def callback():
        return await asyncio.wait_for(asyncio.shield(future), 300)

    async with await asyncio.start_server(receive, host, 8766):
        async with streamablehttp_client(RUNWAY_URL, auth=runway_auth(directory, redirect, callback)) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                print("Runway OAuth connection verified. No media generated.")


def login_youtube(directory, host='127.0.0.1'):
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl"]
    flow = InstalledAppFlow.from_client_secrets_file(str(Path(directory) / "youtube-client.json"), scopes)
    value = flow.run_local_server(host="127.0.0.1", port=8767, open_browser=False,
                                 bind_addr=host, access_type="offline", prompt="consent")
    write_secret(Path(directory) / "youtube-tokens.json", json.loads(value.to_json()))
    print("YouTube OAuth credentials stored. No uploads or public releases performed.")
