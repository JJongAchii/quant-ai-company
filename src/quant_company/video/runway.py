import asyncio
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from ..web_fetch import PublicConnection, public_url
from .auth import RUNWAY_URL, runway_auth


def speech_credits(text):
    """Credit estimate for one narration take. Observed 2026-10-07 (Runway web, ElevenLabs Eleven v4): 1 credit per
    take for 55-210 characters across 20 takes. One credit per started 500 characters keeps a margin."""
    return max(1, -(-len(text) // 500))


RETAKE_RESERVE = 0.2  # budget head-room for pronunciation retakes, checked before the first charge


class RunwaySpeech:
    def __init__(self, credentials_dir):
        self.credentials_dir = credentials_dir

    async def call(self, name, arguments):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        if name not in {"whoami", "generate_speech", "get_task"}:
            raise ValueError("Media tool is not allowed")
        async with streamablehttp_client(RUNWAY_URL, auth=runway_auth(self.credentials_dir),
                                         timeout=45, sse_read_timeout=90) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                matches = [t for t in tools if t.name in {name, "runway_" + name}]
                if len(matches) != 1:
                    raise ValueError("Required Runway tool is unavailable")
                value = await session.call_tool(matches[0].name, arguments)
                if value.isError:
                    raise ValueError("Runway rejected media request; inspect receipt before retrying")
                if value.structuredContent is not None:
                    return value.structuredContent
                texts = [x.text for x in value.content if x.type == "text"]
                if len(texts) != 1:
                    raise ValueError("Unexpected Runway receipt")
                return json.loads(texts[0])

    async def account(self, workspace_id):
        data = await self.call("whoami", {})
        if (not workspace_id or not data.get("authenticated") or not data.get("team")
                or data["team"].get("id") != workspace_id):
            raise ValueError("Runway workspace differs from configured production account")
        balance = data.get("credits", {})
        if balance.get("unlimited") or not isinstance(balance.get("total"), (int, float)):
            raise ValueError("Explicit credit balance required for daily production")
        return data

    async def submit(self, text, voice, model):
        if model != "eleven_multilingual_v2" or not 1 <= len(text) <= 5000:
            raise ValueError("Unsupported narration")
        result = await self.call("generate_speech", {"text": text, "voice": voice, "model": model,
                                "languageCode": "ko", "name": "daily-brief narration"})
        if not result.get("taskId"):
            raise ValueError("Generation returned no task ID; reconcile before resubmission")
        return result

    async def completed(self, task_id):
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            result = await self.call("get_task", {"id": task_id})
            if result.get("status") in {"FAILED", "CANCELED", "failed", "cancelled"}:
                raise ValueError("Runway speech task failed; receipts retained")
            if result.get("status") == "SUCCEEDED" and isinstance(result.get("url"), str):
                return result["url"]
            urls = result.get("output") or result.get("outputUrls")
            if result.get("status") in {"SUCCEEDED", "completed", "succeeded"} and isinstance(urls, list) and urls:
                return urls[0]
            await asyncio.sleep(5)
        raise TimeoutError("Runway task is still pending; task ID retained")


def download_audio(url, destination):
    """Fetch only public HTTPS addresses, resolving once and validating TLS; no credentials."""
    target = urlsplit(public_url(url))
    destination = Path(destination)
    connection = PublicConnection(target.hostname, timeout=30)
    temporary = destination.with_suffix(".download")
    try:
        connection.request("GET", target.path + ("?" + target.query if target.query else ""))
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("Audio download unavailable; task receipt retained")
        total, checksum = 0, hashlib.sha256()
        with temporary.open("wb") as stream:
            while data := response.read(65536):
                total += len(data)
                if total > 32 * 1024 * 1024:
                    raise ValueError("Audio asset exceeds limit")
                stream.write(data)
                checksum.update(data)
        if total < 100:
            raise ValueError("Empty audio asset")
        temporary.replace(destination)
        return {"sha256": checksum.hexdigest(), "bytes": total}
    finally:
        connection.close()
        temporary.unlink(missing_ok=True)
