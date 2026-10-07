"""Opt-in local CLI configuration check; no prompt, credentials or inference."""

import os
import shutil

import pytest

from quant_company.contracts import ProviderRequest, ProviderSession
from quant_company.providers.codex_runner import (
    SUPPORTED_CLI_VERSION,
    CodexRunner,
    RunnerConfig,
    safe_environment,
)


@pytest.mark.integration
@pytest.mark.parametrize("model,effort", [("gpt-6-astra", "max"), ("gpt-5.6-terra", "high"), ("gpt-6.1-sol", "max")])
@pytest.mark.skipif(os.environ.get("CODEX_CONFIG_PROBE") != "1", reason="Set CODEX_CONFIG_PROBE=1 for the local CLI probe")
async def test_real_cli_accepts_required_configuration_without_inference(tmp_path, model, effort):
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
    request = ProviderRequest(request_id="configuration-probe", model=model, reasoning_effort=effort, prompt="Never sent")
    await runner._configuration_preflight(request, work_dir, env)
    request.session = ProviderSession(id="audit-config-probe")
    await runner._configuration_preflight(request, work_dir, env)
    await runner._configuration_preflight(request, work_dir, env, "00000000-0000-4000-8000-000000000001")
    assert not config.jobs_dir.exists()
    assert not list(auth.glob("auth*"))
