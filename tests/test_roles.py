"""Qualify the actual packaged roster against the service's shared Role contract."""

import json
import subprocess
from importlib.resources import files
from pathlib import Path
from typing import get_args

from quant_company.contracts import Role, ToolRequest

IDS = {
    "director", "financial_strategist", "researcher_kr", "researcher_global", "researcher_crypto",
    "data", "engineer", "validator", "risk", "operations", "reporter",
}
ACTIVE = {"director", "financial_strategist", "researcher_kr", "data"}


def load_roles() -> list[Role]:
    raw = json.loads(files("quant_company").joinpath("roles.json").read_text())
    return [Role.model_validate(item) for item in raw]


def test_packaged_roster_is_valid_and_references_supported_capabilities():
    roles = load_roles()
    assert len(roles) == len(IDS)
    assert {role.id for role in roles} == IDS
    assert {role.id for role in roles if role.active} == ACTIVE
    supported_tools = set(get_args(ToolRequest.model_fields["name"].annotation))
    for role in roles:
        assert role.name.strip() and role.mission.strip() and role.instructions.strip()
        assert role.version.isdecimal() and int(role.version) > 0
        assert set(role.tools) <= supported_tools
        assert len(role.tools) == len(set(role.tools))
        assert set(role.can_delegate_to) <= IDS - {role.id}
        if role.active:
            assert set(role.can_delegate_to) <= ACTIVE


def test_roster_round_trip_to_typed_artifact(tmp_path):
    roles = load_roles()
    root = Path(__file__).resolve().parents[1]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    assert len(commit) == 40
    artifact = tmp_path / "roster-qualified.json"
    payload = {"schema_version": 1, "exact_commit": commit,
               "roles": [role.model_dump(mode="json") for role in roles]}
    artifact.write_text(json.dumps(payload, allow_nan=False))
    consumed = json.loads(artifact.read_text())
    assert [Role.model_validate(row) for row in consumed["roles"]] == roles
    assert consumed["exact_commit"] == commit
