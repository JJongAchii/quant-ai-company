"""Real PostgreSQL orchestration with labelled synthetic staff proposals; no strategy run."""

import hashlib
import json
from datetime import timedelta
from uuid import uuid4

import pytest

from quant_company.company import Company, PolicyError, now
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.research.controller import MissionController, stage_prompt

from .test_research_missions import Harness, spec_payload


class FixtureBackend:
    def __init__(self, company):
        self.company = company

    def reconcile(self):
        return {"state": "idle"}

    def enqueue(self, snapshot):
        return {"state": "waiting"}

    def context(self, snapshot, row):
        root = self.company.settings.research_artifact_dir
        root.mkdir(parents=True, exist_ok=True)
        source = root / "fixture.txt"
        source.write_text("Fixture evidence, never market performance.")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        return {"available_files": [{"name": "fixture.txt", "sha256": digest, "size": source.stat().st_size}],
                "_private_files": {"fixture.txt": {"path": str(source), "sha256": digest}}}


@pytest.fixture
def mission(research):
    research.settings.company_autonomous_research_enabled = True
    for actor in ("engineer", "validator"):
        research.roles[actor] = research.roles["data"].model_copy(update={"id": actor, "active": False})
    research.put_source(source_id="fixture:baseline", title="Synthetic baseline", uri="fixture://baseline",
                        content="No market performance", available_at=now()-timedelta(days=1), approved=True, synthetic=True)
    harness = Harness(research)
    harness.mission()
    with research.db.transaction() as conn:
        conn.execute("UPDATE turns SET status='stale'")
        conn.execute("UPDATE tasks SET status='completed'")
    return harness


def active(mission):
    with mission.company.db.transaction() as conn:
        stage = conn.execute("SELECT * FROM research_mission_stages WHERE state='running'").fetchone()
        turn = conn.execute("SELECT id FROM turns WHERE task_id=%s AND status='queued'", (stage["task_id"],)).fetchone()
    return stage, str(turn["id"])


def complete(controller, mission, payload):
    assert controller.tick()["state"] == "running"
    stage, turn_id = active(mission)
    prepared = mission.company.prepare_turn(turn_id)
    assert prepared["state"] == "ready"
    assert "_private_files" not in prepared["request"]["prompt"]
    assert str(mission.company.settings.research_artifact_dir) not in prepared["request"]["prompt"]
    response = ProviderResponse(request_id=turn_id, provider="fixture", decision=AgentDecision(
        say="", status="complete", artifacts=[{"title": "Synthetic stage", "content": json.dumps(payload)}]))
    assert mission.company.commit_turn(turn_id, response)["state"] == "completed"
    assert mission.company.commit_turn(turn_id, response)["duplicate"]
    assert controller.tick()["state"] == "completed"
    return stage


def test_qualification_real_pg_restart_challenge_revision_and_selection(mission):
    company = mission.company
    company.settings.company_max_project_tasks = 1
    controller = MissionController(company, backend=FixtureBackend(company))
    first = mission.proposal()
    complete(controller, mission, first.model_dump(mode="json"))
    challenge = {"id": str(uuid4()), "proposal_id": str(first.id), "reviewer": "financial_strategist",
                 "concern": "The synthetic consumer boundary needs a different assertion",
                 "test": "Revise the hypothesis before running", "source_ids": ["fixture:baseline"]}
    complete(controller, mission, challenge)
    complete(controller, mission, {"decision": "revise", "rationale": "Independent challenge changes the hypothesis",
                                    "challenge_ids": [challenge["id"]]})
    # The company/controller process disappears. PostgreSQL retains the rejected hypothesis.
    company = Company(company.settings, company.roles)
    mission.company = company
    controller = MissionController(company, backend=FixtureBackend(company))
    second = mission.proposal(supersedes_proposal_id=first.id, hypothesis="Revised fixture consumer test")
    complete(controller, mission, second.model_dump(mode="json"))
    second_challenge = {**challenge, "id": str(uuid4()), "proposal_id": str(second.id), "test": "Test the revised consumer"}
    complete(controller, mission, second_challenge)
    complete(controller, mission, {"decision": "execute", "rationale": "The new controlled test addresses the challenge",
                                    "challenge_ids": [second_challenge["id"]]})
    snapshot = mission.snapshot()
    assert len(snapshot["proposals"]) == 2 and len(snapshot["rejections"]) == 1
    assert snapshot["trials"][0]["proposal_id"] == str(second.id)
    assert snapshot["cumulative_trials"] == 0
    assert controller.tick()["state"] == "running"
    stage, turn_id = active(mission)
    assert stage["actor"] == "engineer" and not company.roles["engineer"].active
    assert company.prepare_turn(turn_id)["request"]["model"] == company.roles["engineer"].model
    with company.db.transaction() as conn:
        discussions = conn.execute("SELECT agent,text FROM outbox WHERE text LIKE '[%%' ORDER BY created_at").fetchall()
    assert any(item["agent"] == "researcher_kr" and second.hypothesis in item["text"] for item in discussions)
    assert any(item["agent"] == "financial_strategist" and challenge["concern"] in item["text"] for item in discussions)
    assert any("[가설 수정 결정]" in item["text"] for item in discussions)
    assert len(discussions) == 6  # Each actual debate outcome published once despite repeated commits.


def test_private_stage_cannot_publish_prose_tools_or_fake_audit(mission):
    company = mission.company
    controller = MissionController(company, backend=FixtureBackend(company))
    controller.tick()
    _, turn_id = active(mission)
    company.prepare_turn(turn_id)
    response = ProviderResponse(request_id=turn_id, decision=AgentDecision(say="unverified score 987.123", status="complete"))
    with pytest.raises(PolicyError, match="private"):
        company.commit_turn(turn_id, response)
    company.block_turn(turn_id, "invalid performance value 987.123")
    state = company.project_state(mission.project["project_id"])
    assert "987.123" not in json.dumps(state)
    with company.db.transaction() as conn:
        stage = conn.execute("SELECT * FROM research_mission_stages").fetchone()
        assert stage["state"] == "waiting" and "987.123" in stage["error"]
        assert not conn.execute("SELECT 1 FROM outbox WHERE text LIKE '%987.123%'").fetchone()


def test_scoped_reads_are_internal_and_reject_repeated_or_changed_files(mission):
    company = mission.company
    controller = MissionController(company, backend=FixtureBackend(company))
    controller.tick()
    stage, turn_id = active(mission)
    company.prepare_turn(turn_id)
    response = ProviderResponse(request_id=turn_id, decision=AgentDecision(say="", status="continue", tools=[
        {"name": "research_control", "arguments": {"action": "read_stage_file", "path": "fixture.txt"}}]))
    company.commit_turn(turn_id, response)
    _, next_id = active(mission)
    prompt = company.prepare_turn(next_id)["request"]["prompt"]
    assert "Fixture evidence, never market performance." in prompt
    assert "_private_files" not in prompt
    with pytest.raises(PolicyError, match="already read"):
        company.commit_turn(next_id, response.model_copy(update={"request_id": next_id}))
    state = company.project_state(mission.project["project_id"])
    assert "Fixture evidence, never market performance." not in json.dumps(state)


def test_background_stages_wait_for_direct_owner_work(mission):
    company = mission.company
    # Use a separate approved fixture mission whose scientific resource policy is background.
    payload = spec_payload()
    payload["title"] = "Background synthetic mission"
    payload["resources"]["priority"] = "autonomous"
    mission.mission(spec=payload)
    with company.db.transaction() as conn:
        conn.execute("UPDATE research_missions SET state='paused' WHERE id<>%s", (mission.mission_id,))
        conn.execute("UPDATE turns SET status='stale'")
    controller = MissionController(company, backend=FixtureBackend(company))
    controller.tick()
    _, turn_id = active(mission)
    company.ingest(event_key="fixture:priority", text="Owner foreground request", owner="UHUMAN")
    assert company.prepare_turn(turn_id) == {"state": "defer", "seconds": 2, "reason": "higher_priority_request"}


def test_evidence_prompt_preserves_manifest_with_bounded_chunks(mission):
    company = mission.company
    controller = MissionController(company, backend=FixtureBackend(company))
    controller.tick()
    stage, _ = active(mission)
    with company.db.transaction() as conn:
        for index in range(10):
            conn.execute("""INSERT INTO research_stage_reads(id,stage_id,path,character_offset,content,sha256)
                VALUES (%s,%s,'fixture.txt',%s,%s,%s)""", (uuid4(), stage["id"], index*12000, "x"*12000, "a"*64))
        task = conn.execute("SELECT * FROM tasks WHERE id=%s", (stage["task_id"],)).fetchone()
        _, prompt = stage_prompt(company, conn, task)
    assert "request exactly one file chunk" in prompt and "Never batch file reads" in prompt
    assert len(prompt) < 90000
    context = json.loads(prompt.split("MISSION DATA JSON:\n", 1)[1])
    assert len(context["inspected_chunks"]) == 10 and 1 <= len(context["read_chunks"]) <= 5


def test_retry_reads_are_bound_to_new_validator_attempt(mission):
    company = mission.company
    controller = MissionController(company, backend=FixtureBackend(company))
    controller.tick()
    stage, turn_id = active(mission)
    company.prepare_turn(turn_id)
    response = ProviderResponse(request_id=turn_id, decision=AgentDecision(say="", status="continue", tools=[
        {"name": "research_control", "arguments": {"action": "read_stage_file", "path": "fixture.txt"}}]))
    company.commit_turn(turn_id, response)
    _, following = active(mission)
    company.block_turn(following, "fixture restart with an incomplete audit")
    with company.db.transaction() as conn:
        conn.execute("UPDATE research_mission_stages SET retry_at=now() WHERE id=%s", (stage["id"],))
    assert controller.tick()["state"] == "running"
    second, turn_id = active(mission)
    assert second["attempt"] == 2
    prompt = company.prepare_turn(turn_id)["request"]["prompt"]
    context = json.loads(prompt.split("MISSION DATA JSON:\n", 1)[1])
    assert context["inspected_chunks"] == []
    company.commit_turn(turn_id, response.model_copy(update={"request_id": turn_id}))
    with company.db.transaction() as conn:
        assert {row["attempt"] for row in conn.execute("SELECT attempt FROM research_stage_reads")} == {1, 2}


def test_maintenance_receives_sanitized_contract_failure_not_private_research(mission):
    from quant_company.maintenance.store import Store

    from .test_maintenance import config

    company = mission.company
    controller = MissionController(company, backend=FixtureBackend(company))
    controller.tick()
    _, turn_id = active(mission)
    company.prepare_turn(turn_id)
    company.block_turn(turn_id, "unverified private metric 987.123")
    maintenance = Store(company, config())
    maintenance.initialize()
    identity = maintenance.collect()
    with company.db.transaction() as conn:
        payload = conn.execute("SELECT payload FROM maintenance_jobs WHERE id=%s", (identity,)).fetchone()["payload"]
    events = [item for item in payload["observations"] if item["kind"] == "research_stage_waiting"]
    assert events and events[0]["detail"]["employee"] == "researcher_kr"
    assert "987.123" not in json.dumps(payload)
