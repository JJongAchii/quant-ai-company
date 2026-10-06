"""Catalog protocol uses real subprocesses; synthetic account/model responses unless explicitly stated."""

import json
import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from quant_company.contracts import ProviderFault
from quant_company.providers.client import RuntimeClient
from quant_company.providers.codex_runner import CodexRunner, RunnerConfig
from quant_company.providers.codex_runtime import create_app
from quant_company.providers.model_catalog import read_catalog

from .test_codex_runtime import fake_codex as fake_codex
from .test_codex_runtime import runner_for


async def test_catalog_is_authenticated_profile_scoped_and_never_starts_a_turn(fake_codex, tmp_path):
    config, configure, calls = fake_codex
    backup = tmp_path / "backup-auth"
    backup.mkdir()
    runner = runner_for(replace(config, backup_codex_home=backup))
    app = create_app(runner=runner, token="secret-runtime-token")
    with TestClient(app) as web:
        assert web.get("/v1/models/primary").status_code == 401
    client = RuntimeClient("http://runtime", "secret-runtime-token", transport=httpx.ASGITransport(app=app))
    configure(catalog_pages=[
        [{"model": "candidate-a", "supportedReasoningEfforts": [{"reasoningEffort": "high"}]}],
        [{"model": "candidate-b", "supportedReasoningEfforts": [{"reasoningEffort": "max"}]}]])
    assert await client.models("backup") == [{"model": "candidate-a", "reasoning_efforts": ["high"]},
                                              {"model": "candidate-b", "reasoning_efforts": ["max"]}]
    log = [json.loads(line) for line in (tmp_path / "catalog-calls.jsonl").read_text().splitlines()]
    assert [item["rpc"]["method"] for item in log] == ["initialize", "initialized", "model/list", "model/list"]
    assert all(item["environment"]["CODEX_HOME"] == str(backup) for item in log)
    assert all('approval_policy="never"' in item["args"] and 'features.shell_tool=false' in item["args"] for item in log)
    assert calls() == []
    assert not config.jobs_dir.exists()


@pytest.mark.parametrize("catalog", [[], [{"model": "../invalid", "supportedReasoningEfforts": []}],
                                    [{"model": "candidate-a", "supportedReasoningEfforts": [{"reasoningEffort": "invented"}]}]])
async def test_malformed_capability_catalog_is_not_usable(fake_codex, catalog):
    config, configure, calls = fake_codex
    configure(catalog=catalog)
    with pytest.raises(ProviderFault, match="could not be verified"):
        await read_catalog(runner_for(config), "primary")
    assert calls() == []


async def test_catalog_refuses_custom_configuration_and_api_auth(fake_codex):
    config, _, calls = fake_codex
    (config.codex_home / "config.toml").write_text('model_provider="other"\n')
    with pytest.raises(ProviderFault, match="credential-only"):
        await read_catalog(runner_for(config), "primary")
    (config.codex_home / "config.toml").unlink()
    runner = CodexRunner(config, environment={"OPENAI_API_KEY": "synthetic-never-forward"})
    with pytest.raises(ProviderFault, match="API credentials"):
        await read_catalog(runner, "primary")
    assert calls() == []


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("CODEX_CATALOG_PROBE_BIN"), reason="Opt-in real CLI protocol/config probe")
async def test_real_cli_catalog_protocol_with_empty_auth_is_not_account_availability_evidence(tmp_path):
    # No real login: only the login gate is simulated to inspect the real CLI's
    # initialize/model/list protocol and config acceptance. No thread/turn RPC is sent.
    auth = tmp_path / "empty-auth"
    auth.mkdir()
    config = RunnerConfig(codex_bin=os.environ["CODEX_CATALOG_PROBE_BIN"], codex_home=auth, jobs_dir=tmp_path / "jobs")
    runner = CodexRunner(config, environment={key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ})
    runner.account_status = AsyncMock(return_value={"profile": "primary", "authentication": "chatgpt"})
    models = await read_catalog(runner, "primary")
    assert models
    assert not list(Path(auth).glob("sessions/**/*"))
    assert not list(Path(auth).glob("auth*"))
