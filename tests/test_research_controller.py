"""Real PostgreSQL orchestration with labelled synthetic staff proposals; no strategy run."""

import hashlib
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from quant_company.company import Company, PolicyError, now, stable
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.research.controller import MissionController, _compatible_stage_reads, stage_prompt
from quant_company.research.mission_backend import MissionBackend

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


def test_scoped_reads_are_internal_and_duplicate_reads_preserve_progress(mission):
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
    context = json.loads(prompt.split("MISSION DATA JSON:\n", 1)[1])
    assert context["file_progress"] == {"fixture.txt": {"next_offset": None, "chunks_read": 1}}
    duplicate = company.commit_turn(next_id, response.model_copy(update={"request_id": next_id}))
    assert duplicate["state"] == "completed" and duplicate["duplicate_read"]
    with company.db.transaction() as conn:
        stage = conn.execute("SELECT * FROM research_mission_stages").fetchone()
        assert stage["attempt"] == 1 and stage["state"] == "running"
        assert stage["error"] == "evidence_chunk_already_read:fixture.txt@0;choose_an_unread_chunk"
        assert conn.execute("SELECT count(*) AS count FROM research_stage_reads").fetchone()["count"] == 1
    _, third_id = active(mission)
    third_prompt = company.prepare_turn(third_id)["request"]["prompt"]
    assert "evidence_chunk_already_read:fixture.txt@0;choose_an_unread_chunk" in third_prompt
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
        conn.execute("""UPDATE research_stage_reads SET next_offset=character_offset+12000
            WHERE stage_id=%s AND path='fixture.txt'""", (stage["id"],))
        task = conn.execute("SELECT * FROM tasks WHERE id=%s", (stage["task_id"],)).fetchone()
        _, prompt = stage_prompt(company, conn, task)
    assert "request exactly one file chunk" in prompt and "Never batch file reads" in prompt
    assert len(prompt) < 90000
    context = json.loads(prompt.split("MISSION DATA JSON:\n", 1)[1])
    assert 1 <= len(context["read_chunks"]) <= 5
    assert context["file_progress"] == {"fixture.txt": {"next_offset": 120000, "chunks_read": 10}}


def test_evidence_prompt_retains_early_compact_frozen_menu(mission):
    company = mission.company
    controller = MissionController(company, backend=FixtureBackend(company))
    controller.tick()
    stage, _ = active(mission)
    menu = "REGISTERED_VARIANTS = ('M2', 'A25', 'A75', 'B', 'C', 'D50', 'D75')"
    with company.db.transaction() as conn:
        conn.execute("""UPDATE research_mission_stages SET context=context || %s::jsonb WHERE id=%s""",
                     (json.dumps({"frozen_experiment_code": {
                         "config_path": "code/config.json", "code_paths": ["code/menu.py"]}}), stage["id"]))
        conn.execute("""INSERT INTO research_stage_reads(id,stage_id,path,character_offset,content,sha256)
            VALUES (%s,%s,'code/menu.py',0,%s,%s)""", (uuid4(), stage["id"], menu, "b"*64))
        for index in range(7):
            conn.execute("""INSERT INTO research_stage_reads(id,stage_id,path,character_offset,content,sha256)
                VALUES (%s,%s,%s,0,%s,%s)""",
                         (uuid4(), stage["id"], f"large-{index}.txt", "x"*12000, "c"*64))
        task = conn.execute("SELECT * FROM tasks WHERE id=%s", (stage["task_id"],)).fetchone()
        _, prompt = stage_prompt(company, conn, task)
    assert len(prompt) < 90000
    context = json.loads(prompt.split("MISSION DATA JSON:\n", 1)[1])
    assert menu in {chunk["content"] for chunk in context["read_chunks"]}


def test_audit_prompt_retains_early_compact_scope_evidence(mission):
    company = mission.company
    controller = MissionController(company, backend=FixtureBackend(company))
    controller.tick()
    stage, _ = active(mission)
    receipt = '{"company_commit":"' + "d"*40 + '","qualification":"verified"}'
    with company.db.transaction() as conn:
        conn.execute("""UPDATE research_mission_stages SET stage='audit',actor='validator',
            context=context || %s::jsonb WHERE id=%s""",
                     (json.dumps({"audit": {"scope": ["scope/receipt.json"]}}), stage["id"]))
        conn.execute("""INSERT INTO research_stage_reads(id,stage_id,path,character_offset,content,sha256)
            VALUES (%s,%s,'audit/scope/receipt.json',0,%s,%s)""",
                     (uuid4(), stage["id"], receipt, "d"*64))
        for index in range(7):
            conn.execute("""INSERT INTO research_stage_reads(id,stage_id,path,character_offset,content,sha256)
                VALUES (%s,%s,%s,0,%s,%s)""",
                         (uuid4(), stage["id"], f"large-audit-{index}.csv", "x"*12000, "e"*64))
        task = conn.execute("SELECT * FROM tasks WHERE id=%s", (stage["task_id"],)).fetchone()
        _, prompt = stage_prompt(company, conn, task)
    assert len(prompt) < 90000
    context = json.loads(prompt.split("MISSION DATA JSON:\n", 1)[1])
    assert receipt in {chunk["content"] for chunk in context["read_chunks"]}
    assert "MUST start at byte 0 with a qlab YAML frontmatter block" in prompt
    assert "no prose before frontmatter" in prompt


def test_audit_prompt_keeps_large_resident_code_chunks(mission):
    company = mission.company
    controller = MissionController(company, backend=FixtureBackend(company))
    controller.tick()
    stage, _ = active(mission)
    resident = {
        "audit/scope/trials/example/code/engine.py": [(0, "engine-resident\n" + "e" * 18400)],
        "audit/scope/trials/example/code/adapter.py": [
            (0, "adapter-resident\n" + "a" * 19900), (20000, "adapter-tail\n" + "a" * 650),
        ],
        "audit/scope/trials/example/code/signals.py": [(0, "signals-resident\n" + "s" * 3600)],
        "audit/scope/supplements/checker.py": [(0, "checker-resident\n" + "c" * 12200)],
        "audit/scope/supplements/contract.json": [(0, "contract-resident\n" + "k" * 8800)],
        "audit/scope/supplements/receipt.json": [(0, "receipt-resident\n" + "r" * 4500)],
    }
    with company.db.transaction() as conn:
        conn.execute("""UPDATE research_mission_stages SET stage='audit',actor='validator',
            context=context || %s::jsonb WHERE id=%s""", (json.dumps({"audit": {
                "scope": [path.removeprefix("audit/") for path in resident],
                "resident_evidence_paths": list(resident),
            }, "mission": {
                "id": "fixture-mission", "revision": 1, "manifest_digest": "a" * 64,
                "stage": {"stage": "audit"}, "cycle": 1, "cycle_trials": 1,
                "cumulative_trials": 1, "incumbent_trial_id": "fixture-trial",
                "spec": {"title": "Synthetic audit", "unrelated_history": "m" * 10000},
            }, "evidence_sources": [{"padding": "s" * 2500}],
            "relevant_evidence": {"padding": ["r" * 2500]},
            "available_files": [
                {"name": f"audit/scope/file-{index}.json", "sha256": "a" * 64,
                 "size": 12345, "characters": 12345}
                for index in range(44)
            ]}), stage["id"]))
        expected = set()
        for path, chunks in resident.items():
            for offset, content in chunks:
                expected.add((path, offset, content))
                conn.execute("""INSERT INTO research_stage_reads(
                    id,stage_id,path,character_offset,content,sha256
                ) VALUES (%s,%s,%s,%s,%s,%s)""",
                             (uuid4(), stage["id"], path, offset, content, "f" * 64))
        conn.execute("""UPDATE research_stage_reads SET created_at=now()+interval '1 minute'
            WHERE stage_id=%s AND path='audit/scope/supplements/receipt.json'""", (stage["id"],))
        output_path = "audit/scope/trials/example/outputs/orders-stress.json"
        for index in range(25):
            conn.execute("""INSERT INTO research_stage_reads(
                id,stage_id,path,character_offset,content,next_offset,sha256,created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,now()+interval '2 minutes'+(%s * interval '1 second'))""",
                         (uuid4(), stage["id"], output_path, index * 12000, "x" * 12000,
                          (index + 1) * 12000, "e" * 64, index))
        task = conn.execute("SELECT * FROM tasks WHERE id=%s", (stage["task_id"],)).fetchone()
        _, prompt = stage_prompt(company, conn, task)
    assert len(prompt) < 90000
    context = json.loads(prompt.split("MISSION DATA JSON:\n", 1)[1])
    retained = {(chunk["path"], chunk["offset"], chunk["content"]) for chunk in context["read_chunks"]}
    assert expected <= retained
    assert set(context["mission"]) == {
        "id", "revision", "manifest_digest", "stage", "cycle", "cycle_trials",
        "cumulative_trials", "incumbent_trial_id", "title", "evidence_rule",
    }
    assert all(set(item) == {"name"} for item in context["available_files"])
    assert "evidence_sources" not in context and "relevant_evidence" not in context
    assert context["file_progress"][output_path] == {"next_offset": 300000, "chunks_read": 25}


def test_audit_prompt_fails_before_evicting_latest_resident_chunk(mission):
    company = mission.company
    controller = MissionController(company, backend=FixtureBackend(company))
    controller.tick()
    stage, _ = active(mission)
    paths = ["audit/scope/supplements/first.txt", "audit/scope/supplements/latest.txt"]
    with company.db.transaction() as conn:
        conn.execute("""UPDATE research_mission_stages SET stage='audit',actor='validator',
            context=context || %s::jsonb WHERE id=%s""", (json.dumps({"audit": {
                "scope": [path.removeprefix("audit/") for path in paths],
                "resident_evidence_paths": paths,
            }}), stage["id"]))
        for index, path in enumerate(paths):
            conn.execute("""INSERT INTO research_stage_reads(
                id,stage_id,path,character_offset,content,sha256,created_at
            ) VALUES (%s,%s,%s,0,%s,%s,now()+(%s * interval '1 minute'))""",
                         (uuid4(), stage["id"], path, "x" * 50000, "f" * 64, index))
        task = conn.execute("SELECT * FROM tasks WHERE id=%s", (stage["task_id"],)).fetchone()
        with pytest.raises(PolicyError, match="Audit resident evidence exceeds model context"):
            stage_prompt(company, conn, task)


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
    assert context["file_progress"] == {} and context["last_error"] is None
    company.commit_turn(turn_id, response.model_copy(update={"request_id": turn_id}))
    with company.db.transaction() as conn:
        assert {row["attempt"] for row in conn.execute("SELECT attempt FROM research_stage_reads")} == {1, 2}


def test_operator_reconciles_timeout_into_started_audit_attempt_with_current_thread_replay(mission):
    company = mission.company
    controller = MissionController(company, backend=FixtureBackend(company))
    assert controller.tick()["state"] == "running"
    stage, first_turn = active(mission)
    evidence = company.settings.research_artifact_dir / "audit-evidence.txt"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("immutable validator evidence")
    digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    context = {
        **stage["context"],
        "audit": {"scope": ["audit-evidence.txt"], "validator_request_id": first_turn},
        "_audit": {"binding": {"validator_request_id": first_turn},
                   "required_reads": {"audit/evidence.txt": {
                       "sha256": digest, "characters": len(evidence.read_text())}}},
        "_private_files": {"audit/evidence.txt": {
            "path": str(evidence), "sha256": digest, "size": evidence.stat().st_size}},
        "available_files": [{"name": "audit/evidence.txt", "sha256": digest,
                             "size": evidence.stat().st_size}],
    }
    with company.db.transaction() as conn:
        conn.execute("""UPDATE research_mission_stages SET stage='audit',actor='validator',context=%s
            WHERE id=%s""", (Jsonb(context), stage["id"]))
        conn.execute("UPDATE tasks SET agent='validator' WHERE id=%s", (stage["task_id"],))

    company.prepare_turn(first_turn)
    read = ProviderResponse(request_id=first_turn, provider="fixture", decision=AgentDecision(
        say="", status="continue", tools=[{"name": "research_control", "arguments": {
            "action": "read_stage_file", "path": "audit/evidence.txt"}}]))
    company.commit_turn(first_turn, read)
    _, failed_turn = active(mission)
    company.prepare_turn(failed_turn)
    company.block_turn(failed_turn, "timeout")

    with company.db.transaction() as conn:
        prior = conn.execute("SELECT * FROM research_mission_stages WHERE id=%s FOR UPDATE", (stage["id"],)).fetchone()
        second_task_id = stable(f"mission-stage-attempt:{stage['id']}:2")
        second_task = conn.execute("""INSERT INTO tasks(id,project_id,agent,instruction,revision,depth,priority,kind)
            VALUES (%s,%s,'validator','Retry synthetic audit',1,0,0,'research_stage') RETURNING *""",
                                   (second_task_id, mission.project["project_id"])).fetchone()
        conn.execute("INSERT INTO research_stage_attempts(stage_id,attempt,task_id) VALUES (%s,2,%s)",
                     (stage["id"], second_task_id))
        second_turn = company._new_turn(conn, second_task)
        context = dict(prior["context"])
        context["audit"] = {**context["audit"], "validator_request_id": str(second_turn)}
        context["_audit"] = {**context["_audit"],
                             "binding": {"validator_request_id": str(second_turn)}}
        conn.execute("""UPDATE research_mission_stages SET task_id=%s,attempt=2,state='running',context=%s,
            error=NULL,retry_at=NULL WHERE id=%s""", (second_task_id, Jsonb(context), stage["id"]))

    company.prepare_turn(str(second_turn))
    company.defer_turn(str(second_turn), 5, "busy")
    recovered = company.retry_task(str(stage["task_id"]), reconciliation_note=(
        "Durable runtime receipt says timeout with no response; following busy request produced no receipt."))
    assert recovered["recovery_mode"] == "next_attempt_exact_digest_reads"
    assert recovered["reused_read_count"] == 1
    assert recovered["turn_id"] != str(second_turn)
    prepared = company.prepare_turn(recovered["turn_id"])
    payload = json.loads(prepared["request"]["prompt"].split("MISSION DATA JSON:\n", 1)[1])
    assert payload["file_progress"] == {}

    replay = ProviderResponse(request_id=recovered["turn_id"], provider="fixture", decision=AgentDecision(
        say="", status="continue", tools=[{"name": "research_control", "arguments": {
            "action": "read_stage_file", "path": "audit/evidence.txt"}}]))
    company.commit_turn(recovered["turn_id"], replay)
    _, final_turn = active(mission)
    company.prepare_turn(final_turn)
    response = ProviderResponse(request_id=final_turn, provider="fixture", decision=AgentDecision(
        say="", status="complete", artifacts=[{"title": "Synthetic audit",
        "content": json.dumps({"markdown": "Synthetic validator result"})}]))
    company.commit_turn(final_turn, response)
    snapshot = mission.snapshot()
    with company.db.transaction() as conn:
        current = conn.execute("SELECT * FROM research_mission_stages WHERE id=%s", (stage["id"],)).fetchone()
        backend = MissionBackend(company)
        turns = backend._actor_turns(conn, current["task_id"], "validator", snapshot)
        assert [turn["status"] for turn in turns] == ["stale", "completed", "completed"]
        backend._check_audit_turns(conn, current, snapshot)
        assert {row["attempt"] for row in conn.execute("SELECT attempt FROM research_stage_reads")} == {1, 2}


def test_reconciled_audit_excludes_prior_bytes_when_full_file_digest_changed():
    timestamp = now()
    row = {"stage": "audit", "attempt": 2, "context": {
        "_audit_resume": {"attempt": 1},
        "_private_files": {
            "stable.txt": {"sha256": "a" * 64},
            "changed.txt": {"sha256": "c" * 64},
        },
    }}
    reads = [
        {"id": uuid4(), "attempt": 1, "path": "stable.txt", "character_offset": 0,
         "content": "stable", "next_offset": None, "sha256": "a" * 64, "created_at": timestamp},
        {"id": uuid4(), "attempt": 1, "path": "changed.txt", "character_offset": 0,
         "content": "old", "next_offset": None, "sha256": "b" * 64, "created_at": timestamp},
    ]
    assert [item["path"] for item in _compatible_stage_reads(row, reads)] == ["stable.txt"]


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
