import hashlib
import json

from quant_company.research.runner import ResearchRunner
from quant_company.research.store import ResearchStore

from .test_research_report import COMPANY_COMMIT, producer, write_archive
from .test_research_store import owner, request, row_for


def returned(company, credentials, monkeypatch, tmp_path):
    path, recipe, _, contents = producer(tmp_path)
    monkeypatch.setattr("quant_company.research.store.load_recipe", lambda *args: recipe)
    company.settings.company_code_commit = COMPANY_COMMIT
    _, row = request(company)
    owner(company, credentials, row)
    assignment = ResearchStore(company).poll()["assignment"]
    receipt = json.loads(contents["receipt.json"])
    receipt.update({key: value for key, value in assignment.items() if key not in {"lease_token", "action"}})
    contents["receipt.json"] = json.dumps(receipt).encode()
    write_archive(path, contents)
    (company.settings.research_artifact_dir / str(row["id"])).mkdir(parents=True)
    ResearchStore(company).artifact_received(str(row["id"]), assignment["lease_token"], path,
                                             hashlib.sha256(path.read_bytes()).hexdigest())
    return row, path


def test_verified_result_persists_source_and_one_director_completion_even_after_restart(
    research, credentials, monkeypatch, tmp_path,
):
    row, _ = returned(research, credentials, monkeypatch, tmp_path)
    assert ResearchRunner(research).tick()["state"] == "completed"
    assert ResearchRunner(research).tick()["state"] == "idle"
    current = row_for(research, row["id"])
    assert current["state"] == "completed"
    state = research.project_state(str(row["project_id"]))
    reports = [t for t in state["tasks"] if t["kind"] == "answer"]
    assert len(reports) == 1 and reports[0]["agent"] == "director" and reports[0]["status"] == "pending"
    assert len(state["artifacts"]) == 1
    assert state["artifacts"][0]["source_ids"] == [current["report"]["source_id"]]
    with research.db.transaction() as conn:
        source = conn.execute("SELECT * FROM sources WHERE id=%s", (current["report"]["source_id"],)).fetchone()
    assert source["approved"] and source["synthetic"]
    assert json.loads(source["content"])["scientific_trials_added"] == 0


def test_invalid_or_changed_archive_hides_performance_and_notifies_owner_once(
    research, credentials, monkeypatch, tmp_path,
):
    row, path = returned(research, credentials, monkeypatch, tmp_path)
    path.write_bytes(b"damaged transport fixture")
    assert ResearchRunner(research).tick()["state"] == "awaiting_audit"
    assert ResearchRunner(research).tick()["state"] == "idle"
    with research.db.transaction() as conn:
        assert not conn.execute("SELECT id FROM sources WHERE id LIKE 'research:%%'").fetchall()
        messages = conn.execute("SELECT text FROM outbox WHERE text LIKE '%%성과%%'").fetchall()
    assert len(messages) == 1 and messages[0]["text"].startswith("<@UHUMAN>")
    assert row_for(research, row["id"])["report"] is None


def test_cancellation_during_publication_prevents_slack_and_source_registration(
    research, credentials, monkeypatch, tmp_path,
):
    row, _ = returned(research, credentials, monkeypatch, tmp_path)

    class RacingPublisher:
        def publish(self, job_id, html, archive):
            owner(research, credentials, row, text="중단", stamp="127.0")
            return {"uri": "fixture://cancelled/report", "view_url": "fixture://cancelled/report",
                    "html_sha256": hashlib.sha256(html.encode()).hexdigest()}

    assert ResearchRunner(research, RacingPublisher()).tick()["state"] == "superseded"
    assert row_for(research, row["id"])["state"] == "cancelled"
    with research.db.transaction() as conn:
        assert not conn.execute("SELECT id FROM sources WHERE id LIKE 'research:%%'").fetchall()
        assert not conn.execute("SELECT id FROM artifacts").fetchall()


def test_publication_response_loss_retries_without_another_execution_or_duplicate_report(
    research, credentials, monkeypatch, tmp_path,
):
    row, _ = returned(research, credentials, monkeypatch, tmp_path)
    calls = []

    class LostFirstResponse:
        def publish(self, job_id, html, archive):
            calls.append(hashlib.sha256(html.encode()).hexdigest())
            if len(calls) == 1:
                raise ConnectionError("synthetic response loss after object stored")
            return {"uri": "fixture://returned/report", "view_url": "fixture://returned/report",
                    "html_sha256": calls[-1]}

    import pytest

    publisher = LostFirstResponse()
    with pytest.raises(RuntimeError, match="research_publication_unavailable"):
        ResearchRunner(research, publisher).tick()
    assert row_for(research, row["id"])["state"] == "received"
    assert ResearchStore(research).poll() == {"assignment": None}
    assert ResearchRunner(research, publisher).tick()["state"] == "completed"
    assert calls[0] == calls[1]


def test_model_task_limit_does_not_drop_a_verified_research_report(
    research, credentials, monkeypatch, tmp_path,
):
    row, _ = returned(research, credentials, monkeypatch, tmp_path)
    research.settings.company_max_project_tasks = 1
    assert ResearchRunner(research).tick()["state"] == "completed"
    assert row_for(research, row["id"])["report"]["director_task_id"]


def test_operator_revalidates_same_archive_after_validator_repair_without_worker_reexecution(
    research, credentials, monkeypatch, tmp_path,
):
    from fastapi.testclient import TestClient

    from quant_company.api import create_app
    from quant_company.research.report import ValidationError

    row, _ = returned(research, credentials, monkeypatch, tmp_path)

    def prior_validator(*args):
        raise ValidationError("synthetic_old_validator_schema_mismatch")

    with monkeypatch.context() as broken:
        broken.setattr("quant_company.research.report.validate_bundle", prior_validator)
        assert ResearchRunner(research).tick()["state"] == "awaiting_audit"
    withheld = row_for(research, row["id"])
    client = TestClient(create_app(research.settings, research, credentials))
    endpoint = f"/v1/research/jobs/{row['id']}/revalidate"
    body = {"artifact_sha256": withheld["artifact_sha256"], "expected_revision": row["revision"],
            "reason": "validator_repaired"}
    worker = {"Authorization": "Bearer " + research.settings.research_worker_token.get_secret_value()}
    operator = {"Authorization": "Bearer " + research.settings.operator_token.get_secret_value()}
    assert client.post(endpoint, json=body, headers=worker).status_code == 401
    assert client.post(endpoint, json={**body, "artifact_sha256": "0" * 64}, headers=operator).status_code == 409
    assert client.post(endpoint, json={**body, "expected_revision": 9}, headers=operator).status_code == 409
    assert client.post(endpoint, json=body, headers=operator).json() == {
        "ok": True, "state": "received", "duplicate": False}
    assert client.post(endpoint, json=body, headers=operator).json()["duplicate"]
    assert ResearchStore(research).poll() == {"assignment": None}
    assert ResearchRunner(research).tick()["state"] == "completed"
    completed = row_for(research, row["id"])
    for key in ("lease_token", "company_commit", "artifact_sha256", "approval_event_id"):
        assert completed[key] == withheld[key]
    assert completed["report"]["renderer_company_commit"] == research.settings.company_code_commit


def test_revalidation_cannot_bypass_evidence_validation_or_changed_owner_revision(
    research, credentials, monkeypatch, tmp_path,
):
    import pytest

    from quant_company.company import PolicyError
    from quant_company.research.contracts import ResearchRevalidation

    row, path = returned(research, credentials, monkeypatch, tmp_path)
    path.write_bytes(b"corrupt returned artifact")
    assert ResearchRunner(research).tick()["state"] == "awaiting_audit"
    value = ResearchRevalidation(artifact_sha256=row_for(research, row["id"])["artifact_sha256"],
                                 expected_revision=row["revision"], reason="evidence_restored")
    store = ResearchStore(research)
    assert store.revalidate(str(row["id"]), value)["state"] == "received"
    assert ResearchRunner(research).tick()["state"] == "awaiting_audit"
    assert row_for(research, row["id"])["report"] is None
    owner(research, credentials, row, text="중단", stamp="128.0")
    with pytest.raises(PolicyError):
        store.revalidate(str(row["id"]), value)
