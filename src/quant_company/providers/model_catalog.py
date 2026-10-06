"""Read Codex model/list without starting a thread or inference, inside the private runtime."""

import asyncio
import json
import re
import tempfile
from pathlib import Path

from ..contracts import ProviderFault
from .codex_runner import (
    DISABLED_FEATURES,
    SUPPORTED_CLI_VERSION,
    ProcessRunner,
    safe_environment,
    strict_json,
)

MAX_BYTES = 512 * 1024
MODEL_ID = r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}"
EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}


def validate_models(rows):
    if not isinstance(rows, list) or not 0 < len(rows) <= 500:
        raise ValueError("Invalid model catalog")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"model", "reasoning_efforts"}:
            raise ValueError("Invalid model entry")
        name, efforts = row["model"], row["reasoning_efforts"]
        if (not isinstance(name, str) or not re.fullmatch(MODEL_ID, name) or name in seen
                or not isinstance(efforts, list) or not efforts
                or any(not isinstance(e, str) or e not in EFFORTS for e in efforts)):
            raise ValueError("Invalid model capabilities")
        seen.add(name)
    return rows


async def read_catalog(runner, profile):
    config = runner.account_config(profile)
    env = safe_environment(config, runner.environment)
    # CLI 0.154 has ignore-user-config only on exec, not app-server. Discovery
    # therefore requires the credential-only runtime home instead of loading user configuration.
    if (config.codex_home / "config.toml").exists():
        raise ProviderFault("unavailable", "Model discovery requires a credential-only Codex home without config.toml.")
    if (await runner.account_status(profile))["authentication"] != "chatgpt":
        raise ProviderFault("auth", "Authenticate the selected profile with ChatGPT.")
    with tempfile.TemporaryDirectory(prefix="company-model-catalog-") as work:
        version = await runner.process.run([config.codex_bin, "--version"], cwd=Path(work), env=env,
            timeout_seconds=5, max_stdout_bytes=1024, max_stderr_bytes=1024)
        if version.returncode or version.stdout.strip() != f"codex-cli {SUPPORTED_CLI_VERSION}".encode():
            raise ProviderFault("unavailable", "Model discovery requires the validated Codex CLI.")
        argv = [config.codex_bin, "app-server", "--strict-config", "--listen", "stdio://"]
        overrides = ['forced_login_method="chatgpt"', 'cli_auth_credentials_store="file"',
                     'model_provider="openai"', 'approval_policy="never"', 'sandbox_mode="read-only"',
                     'web_search="disabled"', "mcp_servers={}", "apps._default.enabled=false", "notify=[]",
                     "agents.enabled=false", "features.skip_host_skill_discovery=true",
                     "memories.generate_memories=false", "memories.use_memories=false"]
        for override in overrides + [f"features.{name}=false" for name in DISABLED_FEATURES]:
            argv.extend(("-c", override))
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(
            *argv, cwd=work, env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=MAX_BYTES))
        try:
            process = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            process = await spawn
            await asyncio.shield(ProcessRunner()._stop(process))
            raise
        stderr = None
        try:
            async def drain_errors():
                total = 0
                while part := await process.stderr.read(4096):
                    total += len(part)
                    if total > MAX_BYTES:
                        raise ValueError("Catalog stderr limit")

            consumed = 0

            async def send(value):
                process.stdin.write(json.dumps(value).encode() + b"\n")
                await process.stdin.drain()

            async def rpc(identity, method, params):
                nonlocal consumed
                await send({"id": identity, "method": method, "params": params})
                while True:
                    line = await process.stdout.readline()
                    consumed += len(line)
                    if not line or consumed > MAX_BYTES:
                        raise ValueError("Missing or oversized catalog reply")
                    message = strict_json(line)
                    if not isinstance(message, dict):
                        raise ValueError("Invalid catalog reply")
                    if message.get("id") == identity:
                        if "error" in message or "result" not in message:
                            raise ValueError("Catalog RPC rejected")
                        return message["result"]
                    if "id" in message:
                        raise ValueError("Unexpected catalog RPC")

            async def discover():
                await rpc(1, "initialize", {"clientInfo": {"name": "quant-company", "version": "0.1.0"}})
                await send({"method": "initialized"})
                rows, cursor, cursors = [], None, set()
                for index in range(25):
                    params = {"limit": 100, "includeHidden": False}
                    if cursor:
                        params["cursor"] = cursor
                    page = await rpc(index + 2, "model/list", params)
                    for item in page["data"]:
                        if not item.get("hidden", False):
                            rows.append({"model": item["model"], "reasoning_efforts": [
                                effort["reasoningEffort"] for effort in item["supportedReasoningEfforts"]]})
                    cursor = page.get("nextCursor")
                    if cursor is None:
                        return validate_models(rows)
                    if not isinstance(cursor, str) or not cursor or cursor in cursors:
                        raise ValueError("Invalid catalog cursor")
                    cursors.add(cursor)
                raise ValueError("Catalog page limit")

            stderr = asyncio.create_task(drain_errors())
            discovery = asyncio.create_task(discover())
            try:
                async with asyncio.timeout(20):
                    done, _ = await asyncio.wait([stderr, discovery], return_when=asyncio.FIRST_COMPLETED)
                    if stderr in done:
                        stderr.result()
                    return await discovery
            finally:
                discovery.cancel()
                await asyncio.gather(discovery, return_exceptions=True)
        except (ValueError, KeyError, TypeError, AttributeError, OSError, TimeoutError):
            raise ProviderFault("unavailable", "The selected account model catalog could not be verified.") from None
        finally:
            if stderr:
                stderr.cancel()
                await asyncio.gather(stderr, return_exceptions=True)
            await asyncio.shield(ProcessRunner()._stop(process))
