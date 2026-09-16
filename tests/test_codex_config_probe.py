"""Opt-in local CLI configuration check; no prompt, credentials or inference."""

import os
import shutil

import pytest

from quant_company.contracts import ProviderRequest
from quant_company.providers.codex_runner import (
    SUPPORTED_CLI_VERSION,
    CodexRunner,
    RunnerConfig,
    safe_environment,
)


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("CODEX_CONFIG_PROBE") != "1", reason="Set CODEX_CONFIG_PROBE=1 for the local CLI probe")
async def test_real_cli_accepts_required_configuration_without_inference(tmp_path):
    binary = shutil.which("codex")
    if not binary:
        pytest.skip("Official Codex CLI is not installed")
    auth = tmp_path / "empty-auth"
    auth.mkdir()
    work_dir = tmp_path / "empty-work"
    work_dir.mkdir()
    config = RunnerConfig(codex_bin=binary, codex_home=auth, jobs_dir=tmp_path / "jobs")
    source = {name: os.environ[name] for name in ("PATH", "HOME") if name in os.environ}
    env = safe_environment(config, source)
    runner = CodexRunner(config, environment=source)
    version = await runner.process.run([binary, "--version"], cwd=work_dir, env=env, timeout_seconds=15,
                                       max_stdout_bytes=65536, max_stderr_bytes=65536)
    assert version.returncode == 0
    assert version.stdout.strip() == f"codex-cli {SUPPORTED_CLI_VERSION}".encode()
    request = ProviderRequest(request_id="configuration-probe", model="gpt-5.6-luna", prompt="Never sent")
    await runner._configuration_preflight(request, work_dir, env)
    assert not config.jobs_dir.exists()
    assert not list(auth.glob("auth*"))
