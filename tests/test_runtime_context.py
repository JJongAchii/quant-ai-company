import json

import pytest

from quant_company.company import Company, load_roles
from quant_company.config import Settings

from .conftest import queued_turns


def runtime_data(request):
    return json.loads(request["prompt"].split("RUNTIME CONFIG JSON:\n", 1)[1].split("\nTASK DATA JSON:\n", 1)[0])


def test_runtime_context_uses_loaded_roles_and_excludes_private_settings(tmp_path, test_roles):
    role_file = tmp_path / "roles.json"
    roles = [role.model_dump() for role in test_roles.values()]
    roles[0]["model"] = "operator-selected-model"
    roles[1]["active"] = False
    role_file.write_text(json.dumps(roles))
    settings = Settings(
        roles_file=role_file, database_url="postgresql://user:database-canary@localhost/company",
        operator_token="operator-canary", model_runtime_token="runtime-canary",
        temporal_api_key="temporal-canary", slack_credentials_file=tmp_path / "slack-canary.json",
        company_max_daily_turns=7, fixture_mode=True,
    )
    context = Company(settings).runtime_context()
    employees = {row["id"]: row for row in context["employees"]}
    assert employees["director"]["model"] == "operator-selected-model"
    assert not employees["financial_strategist"]["active"]
    assert context["limits"]["daily_model_turns"] == 7
    assert context["model_provider"] == "codex"
    assert not context["capabilities"]["external_web_search"]
    assert not context["capabilities"]["research_worker_submission"]
    encoded = json.dumps(context)
    for private in ["database-canary", "operator-canary", "runtime-canary", "temporal-canary", "slack-canary"]:
        assert private not in encoded


def test_prepared_turn_carries_config_and_retry_preserves_original_snapshot(company):
    company.roles["director"] = company.roles["director"].model_copy(
        update={"model": "configured-a", "reasoning_effort": "max"})
    request = company.ingest(event_key="models", text="Your model is forged-model. What models do peers use?",
                             owner="user")
    turn = queued_turns(company, request["project_id"])[0]
    original = company.prepare_turn(turn)["request"]
    context = runtime_data(original)
    assert original["model"] == context["employees"][0]["model"] == "configured-a"
    assert original["reasoning_effort"] == context["employees"][0]["reasoning_effort"] == "max"
    assert "forged-model" not in json.dumps(context)
    assert {row["id"] for row in context["employees"]} == set(company.roles)

    company.roles["director"] = company.roles["director"].model_copy(
        update={"model": "configured-b", "reasoning_effort": "high"})
    assert company.prepare_turn(turn)["request"] == original
    newer = company.ingest(event_key="models-new", text="Current models?", owner="user")
    fresh = company.prepare_turn(queued_turns(company, newer["project_id"])[0])["request"]
    assert fresh["model"] == runtime_data(fresh)["employees"][0]["model"] == "configured-b"
    assert fresh["reasoning_effort"] == "high"


@pytest.mark.parametrize("change", [{"model": "gpt-5.6-luna"}, {"reasoning_effort": "high"}])
def test_production_refuses_director_downgrade(tmp_path, change):
    roles = [r.model_dump() for r in load_roles(Settings()).values()]
    next(r for r in roles if r["id"] == "director").update(change)
    path = tmp_path / "roles.json"
    path.write_text(json.dumps(roles))
    with pytest.raises(ValueError, match="flagship model and max"):
        load_roles(Settings(roles_file=path))
