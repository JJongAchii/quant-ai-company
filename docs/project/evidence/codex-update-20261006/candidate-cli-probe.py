"""CLI-only candidate probe. Empty authentication; no model inference or production changes."""

import asyncio
import json
import pathlib
import tempfile
from unittest.mock import AsyncMock

from quant_company.contracts import ProviderRequest, ProviderSession
from quant_company.providers import model_catalog
from quant_company.providers.codex_runner import CodexRunner, RunnerConfig

BINARY = pathlib.Path(".local/codex-release-probe-01601/node_modules/.bin/codex").resolve()


async def main():
    with tempfile.TemporaryDirectory(prefix="company-codex-release-probe-") as root:
        root = pathlib.Path(root)
        auth = root / "empty-auth"
        auth.mkdir()
        work = root / "work"
        work.mkdir()
        runner = CodexRunner(RunnerConfig(codex_bin=str(BINARY), codex_home=auth, jobs_dir=root / "jobs"),
                             environment={"PATH": "/opt/homebrew/bin:/usr/bin:/bin", "HOME": str(root)})
        from quant_company.providers.codex_runner import safe_environment

        env = safe_environment(runner.config, runner.environment)
        result = await runner.process.run([str(BINARY), "--version"], cwd=work, env=env, timeout_seconds=15,
                                          max_stdout_bytes=1024, max_stderr_bytes=1024)
        version = result.stdout.decode().strip()
        assert version == "codex-cli 0.160.1"
        checks = []
        for name, web, session in (("plain", False, None), ("web_search", True, None),
                                   ("session_resume", False, ProviderSession(id="cli-qualification"))):
            request = ProviderRequest(request_id="cli-qualification-" + name, model="gpt-6.1-sol",
                                      reasoning_effort="high", prompt="This prompt is never submitted.",
                                      web_search=web, session=session)
            try:
                await runner._configuration_preflight(request, work, env,
                    "00000000-0000-4000-8000-000000000000" if session else None)
                checks.append({"path": name, "accepted": True})
            except Exception as exc:
                checks.append({"path": name, "accepted": False, "error": type(exc).__name__ + ":" + str(exc)})
        # Simulate only the authentication gate for this CLI/catalog compatibility check.
        # The production gate remains pinned and uses genuine official ChatGPT login.
        model_catalog.SUPPORTED_CLI_VERSION = "0.160.1"
        runner.account_status = AsyncMock(return_value={"profile": "primary", "authentication": "chatgpt"})
        catalog = None
        error = None
        try:
            catalog = await model_catalog.read_catalog(runner, "primary")
        except Exception as exc:
            error = type(exc).__name__ + ":" + str(exc)
        assert not list(auth.glob("auth*"))
        assert not list(auth.glob("sessions/**/*"))
        print(json.dumps({"cli_version": version, "platform": "local macOS arm64", "configuration_checks": checks,
                          "models": catalog, "catalog_error": error,
                          "authentication_gate": "simulated; empty auth directory", "model_turns_started": 0,
                          "production_changes": False, "account_availability_verified": False}, indent=2))


asyncio.run(main())
