"""Real profiles/Git/PG and signed captured Slack HTTP; no live Slack or market trial."""

import json

from fastapi.testclient import TestClient

from quant_company.api import create_app
from quant_company.research.controller import mission_tool

from .test_research_approvals import deliver_approval, interaction, signed_form
from .test_research_audit import qlab_profile  # noqa: F401
from .test_research_mission_backend import backend_fixture  # noqa: F401


async def test_signed_button_activates_exact_provisioned_mission_once(backend_fixture, credentials):  # noqa: F811
    h = backend_fixture
    spec = h.spec.model_dump(mode="json") | {"title": "Pending synthetic button acceptance"}
    with h.company.db.transaction() as conn:
        project = h.company._project(conn, h.project["project_id"])
        task = conn.execute("SELECT * FROM tasks WHERE id=%s", (h.project["task_id"],)).fetchone()
        draft = mission_tool(h.company, conn, project, task, {"action": "mission_draft", "spec": spec})
    assert draft["state"] == "draft"
    body = await deliver_approval(h.company, credentials)
    payload = interaction(h.company, credentials, body)
    raw, headers = signed_form(payload, credentials["director"])
    app = create_app(h.company.settings, h.company, credentials)
    with TestClient(app) as client:
        for _ in range(2):
            response = client.post("/slack/events/director", content=raw, headers=headers)
            assert response.status_code == 200, response.text
    with h.company.db.transaction() as conn:
        row = conn.execute("SELECT * FROM research_missions WHERE id=%s", (draft["id"],)).fetchone()
        assert row["state"] == "active" and row["manifest_digest"] == draft["manifest_digest"]
        assert conn.execute("SELECT count(*) AS n FROM research_mission_controls WHERE mission_id=%s",
                            (draft["id"],)).fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM research_jobs").fetchone()["n"] == 0
        assert h.company._project(conn, h.project["project_id"])["revision"] == 1


async def test_approval_refuses_drifted_operator_profile_without_amendment(backend_fixture, credentials):  # noqa: F811
    h = backend_fixture
    spec = h.spec.model_dump(mode="json") | {"title": "Synthetic drifted profile approval"}
    with h.company.db.transaction() as conn:
        project = h.company._project(conn, h.project["project_id"])
        task = conn.execute("SELECT * FROM tasks WHERE id=%s", (h.project["task_id"],)).fetchone()
        draft = mission_tool(h.company, conn, project, task, {"action": "mission_draft", "spec": spec})
    body = await deliver_approval(h.company, credentials)
    profile_path = h.company.settings.research_profiles_file
    profiles = json.loads(profile_path.read_text())
    profiles[spec["execution_profile"]]["public_profile"]["evaluation_timeout_seconds"] += 1
    profile_path.write_text(json.dumps(profiles))
    raw, headers = signed_form(interaction(h.company, credentials, body), credentials["director"])
    with TestClient(create_app(h.company.settings, h.company, credentials)) as client:
        response = client.post("/slack/events/director", content=raw, headers=headers)
    assert response.status_code in (200, 400, 403, 409), response.text
    with h.company.db.transaction() as conn:
        row = conn.execute("SELECT * FROM research_missions WHERE id=%s", (draft["id"],)).fetchone()
        assert row["state"] == "draft" and row["approval_event_id"] is None
        assert h.company._project(conn, h.project["project_id"])["revision"] == 1
