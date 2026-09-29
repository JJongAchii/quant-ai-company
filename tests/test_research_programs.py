"""Real PostgreSQL/Git; scripted employees and synthetic data, no live research."""

import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from quant_company.company import Company, PolicyError, now
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.research.adaptive_contracts import digest_model
from quant_company.research.builds import ServerResearchProfile
from quant_company.research.data_evidence import load_packets
from quant_company.research.feedback import resolve_challenges
from quant_company.research.mission_contracts import MissionSpec
from quant_company.research.program_contracts import ResearchProgram
from quant_company.research.program_controller import ProgramController
from quant_company.research.programs import ProgramStore, public_progress
from quant_company.research.worker import sha_file

from .test_research_adaptive_report import producer
from .test_research_missions import Harness


@pytest.fixture
def program(company, tmp_path):
    company.settings.company_research_enabled = True
    company.settings.company_autonomous_research_enabled = True
    company.settings.research_artifact_dir = tmp_path / "artifacts"
    company.settings.research_artifact_dir.mkdir()
    for actor in ("engineer", "validator"):
        company.roles[actor] = company.roles["data"].model_copy(update={"id": actor, "active": False})
    company.put_source(source_id="fixture:baseline", title="Synthetic baseline", uri="fixture://baseline",
        content="Synthetic original: point-in-time fixture data, no financial result.",
        available_at=now()-timedelta(days=1), approved=True, synthetic=True)
    root = tmp_path / "producer"
    root.mkdir()
    original = producer(root)
    _, _, manifest, public, _ = original
    public = public.model_copy(update={"id": "kr-etf-monthly-python-v1"})
    spec = MissionSpec.model_validate(manifest.spec.model_dump(mode="json") | {
        "execution_profile": public.id,
        "execution_profile_digest": digest_model(public), "baseline_source_ids": ["fixture:baseline"],
        "search": {"max_trials_per_cycle": 2, "max_total_trials": 2, "patience": 2,
                   "min_improvement": 0.001, "continuous": False}})
    bundle = root / "base.bundle"
    subprocess.run(["git", "-C", str(root / "repo"), "bundle", "create", str(bundle), "HEAD"],
                   check=True, capture_output=True)
    profile = ServerResearchProfile(public_profile=public, source_bundle=bundle.resolve(),
        source_bundle_sha256=sha_file(bundle), base_commit=spec.code.base_commit,
        config_path="config.json", config_files=["config.json"], allowed_write_paths=spec.code.write_paths)
    profiles = tmp_path / "profiles.json"
    profiles.write_text(json.dumps({spec.execution_profile: profile.model_dump(mode="json")}))
    company.settings.research_profiles_file = profiles
    h = Harness(company)
    h.original, h.public = original, public
    h.program_store = ProgramStore(company)
    h.program_spec = ResearchProgram(title="Synthetic bounded program", objective="Exercise research authority",
        envelopes=[{"name": "etf", "market": "kr_etf", "template": spec}], max_total_trials=2,
        max_compute_seconds=10000, max_missions=2, source_ids=["fixture:baseline"], include_quant_feed=False)
    with company.db.transaction() as conn:
        project = company._project(conn, h.project["project_id"])
        row = h.program_store.create(conn, project, h.program_spec)
        h.program_id, h.digest = row["id"], row["manifest_digest"]
    h.mission_id = h.program_id
    approval = h.owner_event("approve", target={"kind": "program", "target_id": h.program_id,
        "revision": 1, "manifest_digest": h.digest})
    with company.db.transaction() as conn:
        h.program_store.owner_command(conn, h.program_id, **h.owner_args(approval), action="approve")
        conn.execute("UPDATE turns SET status='stale'")
        conn.execute("UPDATE tasks SET status='completed'")
    return h


def task_proposal(**changes):
    return {"title": "Synthetic original replication", "envelope": "etf", "question": "Is the fixture reproducible?",
        "mode": "market_transfer", "original_claim": "Synthetic protocol only", "method": "Controlled fixture",
        "departures": "Uses synthetic data", "falsification": "Consumer rejects broken identity",
        "source_ids": ["fixture:baseline"], "citations": [{"source_id": "fixture:baseline", "location": "char:0",
            "quote": "Synthetic original: point-in-time fixture data"}], "predecessor_mission_ids": [], **changes}


def ready(**changes):
    return {"decision": "ready", "rationale": "Synthetic fixture data verified", "source_ids": ["fixture:baseline"],
        "point_in_time": True, "coverage": True, "executable_prices": True, "original_conditions": False, **changes}


def provision_data_packet(h):
    root = h.company.settings.research_artifact_dir / "provisioned" / "data-evidence"
    root.mkdir(parents=True)
    inputs = {"qualification.json": b'{"synthetic_fixture":true}', "development.csv": b"synthetic\n"}
    engine = h.original[4]["code/evaluator.py"]
    reports = {"limitations.txt": b"Synthetic fixture only. No market data readiness claim."}

    def place(name, content):
        path = root / name
        path.write_bytes(content)
        return {"path": str(path), "sha256": hashlib.sha256(content).hexdigest()}

    spec = h.program_spec.envelopes[0].template
    packet = {"program_digest": h.digest, "envelope": "etf",
        "execution_profile_digest": spec.execution_profile_digest, "lake_id": spec.data.lake_id,
        "blocking_gaps": ["Synthetic fixture cannot establish market provenance"],
        "input_files": {name: place(name, content) for name, content in inputs.items()},
        "engine": place("evaluator.py", engine),
        "reports": {name: place(name, content) for name, content in reports.items()}}
    registry = root / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "packets": [packet]}))
    h.company.settings.research_data_evidence_file = registry
    return packet


def create_task(h, **changes):
    with h.company.db.transaction() as conn:
        task_id = h.program_store.propose(conn, h.program_id, task_proposal(**changes), actor="researcher_kr")
        h.program_store.assess(conn, h.program_id, task_id, ready(), actor="data")
        h.program_store.decide(conn, h.program_id, task_id, {"decision": "accept", "rationale": "Bounded fixture"}, actor="director")
        h.mission_id = str(conn.execute("SELECT mission_id FROM research_program_tasks WHERE id=%s", (task_id,)).fetchone()["mission_id"])
    return task_id


def test_owner_followup_sees_current_program_stage(program):
    from .test_task_control import route, turn_for

    h = program
    ProgramController(h.company).tick()
    with h.company.db.transaction() as conn:
        progress = public_progress(conn, h.project["project_id"])
    assert progress[0]["state"] == "active"
    assert progress[0]["stage"]["stage"] == "program_proposal"
    assert progress[0]["mission_count"] == 0
    question = h.company.ingest(event_key="fixture:program-progress", text="방금 승인한 새 연구 진행 중이야?",
                                owner="UHUMAN", project_id=h.project["project_id"], interpret=True)
    assert route(h.company, question, "status")["intent"]["action"] == "followup"
    answer = h.company.prepare_turn(turn_for(h.company, question["task_id"]))
    context = json.loads(answer["request"]["prompt"].split("TASK DATA JSON:\n")[1])
    assert context["research_programs"] == progress


def test_program_inherits_bound_owner_authority_and_rejects_self_approval(program):
    h = program
    create_task(h)
    snap = h.snapshot()
    assert snap["program_id"] == h.program_id and snap["state"] == "active"
    assert snap["spec"]["code"] == h.program_spec.envelopes[0].template.code.model_dump()
    with h.company.db.transaction() as conn:
        parent = conn.execute("SELECT approval_event_id FROM research_programs WHERE id=%s", (h.program_id,)).fetchone()
        assert snap["approval_event_id"] == parent["approval_event_id"]
    event = h.owner_event("approve", bound=False)
    with pytest.raises(PolicyError, match="bound authenticated"):
        with h.company.db.transaction() as conn:
            h.program_store.owner_command(conn, h.program_id, **h.owner_args(event), action="approve")


def test_exact_replication_cannot_silently_be_a_market_transfer(program):
    h = program
    with h.company.db.transaction() as conn:
        task = h.program_store.propose(conn, h.program_id, task_proposal(mode="exact_replication"), actor="researcher_kr")
    with pytest.raises(PolicyError, match="original conditions"):
        with h.company.db.transaction() as conn:
            h.program_store.assess(conn, h.program_id, task, ready(), actor="data")


def test_task_acceptance_rechecks_parallel_limit_inside_the_transaction(program):
    create_task(program)
    with pytest.raises(PolicyError, match="parallel mission limit"):
        create_task(program, title="Second distinct fixture", question="Can another task start concurrently?")


def test_all_challenges_require_dispositions_and_revisions_block_execution(program):
    h = program
    create_task(h)
    proposal = h.proposal()
    h.invoke("add_proposal", h.mission_id, proposal, actor="researcher_kr")
    challenge = h.challenge(proposal)
    h.invoke("add_challenge", h.mission_id, challenge, actor="financial_strategist")
    with pytest.raises(PolicyError, match="Unresolved"):
        h.invoke("select", h.mission_id, proposal.id, actor="director", challenge_ids=[challenge.id],
                 rationale="Cannot ignore this objection", trial_id=uuid4())
    with h.company.db.transaction() as conn:
        resolve_challenges(h.company, conn, h.snapshot(), proposal.id, {
            "decision": "execute", "rationale": "Schedule the independent test", "responses": [{
                "challenge_id": str(challenge.id), "disposition": "test", "rationale": "Testable objection",
                "test_plan": "Assert the consumer rejects corrupt identities", "source_ids": ["fixture:baseline"]}]}, "director")
    h.invoke("select", h.mission_id, proposal.id, actor="director", challenge_ids=[challenge.id],
             rationale="Independent test obligation retained", trial_id=uuid4())
    assert h.snapshot()["challenge_responses"][0]["payload"]["disposition"] == "test"


def test_program_stage_requires_current_attempt_original_read_and_survives_restart(program):
    h = program
    controller = ProgramController(h.company)
    assert controller.tick()["state"] == "running"
    with h.company.db.transaction() as conn:
        stage = conn.execute("SELECT * FROM research_mission_stages WHERE program_id=%s", (h.program_id,)).fetchone()
        turn = conn.execute("SELECT id FROM turns WHERE task_id=%s", (stage["task_id"],)).fetchone()
    identity = str(turn["id"])
    assert stage["context"]["allowed_predecessor_mission_ids"] == []
    prepared = h.company.prepare_turn(identity)
    assert prepared["state"] == "ready"
    assert "If that list is empty, set predecessor_mission_ids to []" in prepared["request"]["prompt"]
    assert json.loads(prepared["request"]["prompt"].split("MISSION DATA JSON:\n", 1)[1])[
        "allowed_predecessor_mission_ids"] == []
    response = ProviderResponse(request_id=identity, provider="fixture", decision=AgentDecision(say="", status="complete",
        artifacts=[{"title": "Synthetic proposal", "content": json.dumps(task_proposal())}]))
    assert h.company.commit_turn(identity, response)["incomplete_source"]
    with h.company.db.transaction() as conn:
        current = conn.execute("SELECT error,attempt FROM research_mission_stages WHERE id=%s", (stage["id"],)).fetchone()
        read_turn = str(conn.execute("SELECT id FROM turns WHERE task_id=%s AND status='queued'",
                                     (stage["task_id"],)).fetchone()["id"])
    assert current["attempt"] == 1 and current["error"].startswith("cited_source_unread:")
    assert "read_first_chunk" in h.company.prepare_turn(read_turn)["request"]["prompt"]
    with pytest.raises(PolicyError, match="not been read"):
        h.company.commit_turn(read_turn, response.model_copy(update={"request_id": read_turn}))
    file = stage["context"]["evidence_sources"][0]["file"]
    read = ProviderResponse(request_id=read_turn, provider="fixture", decision=AgentDecision(say="", status="continue",
        tools=[{"name": "research_control", "arguments": {"action": "read_stage_file", "path": file, "offset": 0}}]))
    h.company.commit_turn(read_turn, read)
    h.company = Company(h.company.settings, h.company.roles)
    with h.company.db.transaction() as conn:
        next_id = str(conn.execute("SELECT id FROM turns WHERE task_id=%s AND status='queued'", (stage["task_id"],)).fetchone()["id"])
    assert h.company.prepare_turn(next_id)["state"] == "ready"
    h.company.commit_turn(next_id, response.model_copy(update={"request_id": next_id}))
    assert ProgramController(h.company).tick()["state"] == "completed"
    assert ProgramController(h.company).tick()["state"] == "running"
    with h.company.db.transaction() as conn:
        assert conn.execute("SELECT actor FROM research_mission_stages WHERE state='running'").fetchone()["actor"] == "data"


def test_partial_original_and_literal_newline_continue_same_attempt(program):
    h = program
    with h.company.db.transaction() as conn:
        conn.execute("UPDATE sources SET content=content || %s WHERE id='fixture:baseline'", ("x" * 12000,))
    controller = ProgramController(h.company)
    assert controller.tick()["state"] == "running"
    with h.company.db.transaction() as conn:
        stage = conn.execute("SELECT * FROM research_mission_stages WHERE program_id=%s", (h.program_id,)).fetchone()
        first = conn.execute("SELECT id FROM turns WHERE task_id=%s AND status='queued'", (stage["task_id"],)).fetchone()
    first_id = str(first["id"])
    h.company.prepare_turn(first_id)
    path = stage["context"]["evidence_sources"][0]["file"]
    def read(identity, offset):
        return ProviderResponse(request_id=identity, provider="fixture", decision=AgentDecision(
            say="", status="continue", tools=[{"name": "research_control", "arguments": {
                "action": "read_stage_file", "path": path, "offset": offset}}]))
    h.company.commit_turn(first_id, read(first_id, 0))
    with h.company.db.transaction() as conn:
        second_id = str(conn.execute("SELECT id FROM turns WHERE task_id=%s AND status='queued'",
                                     (stage["task_id"],)).fetchone()["id"])
    h.company.prepare_turn(second_id)
    proposal = task_proposal(method="First line\nSecond line")
    malformed = json.dumps(proposal).replace("First line\\nSecond line", "First line\nSecond line")
    with pytest.raises(json.JSONDecodeError):
        json.loads(malformed)
    premature = ProviderResponse(request_id=second_id, provider="fixture", decision=AgentDecision(
        say="", status="complete", artifacts=[{"title": "Synthetic proposal", "content": malformed}]))
    result = h.company.commit_turn(second_id, premature)
    assert result["incomplete_source"]
    with h.company.db.transaction() as conn:
        current = conn.execute("SELECT * FROM research_mission_stages WHERE id=%s", (stage["id"],)).fetchone()
        third_id = str(conn.execute("SELECT id FROM turns WHERE task_id=%s AND status='queued'",
                                    (stage["task_id"],)).fetchone()["id"])
        assert current["state"] == "running" and current["attempt"] == 1
        assert "read_next_chunk" in current["error"]
        assert conn.execute("SELECT count(*) AS n FROM research_program_tasks").fetchone()["n"] == 0
    prompt = h.company.prepare_turn(third_id)["request"]["prompt"]
    context = json.loads(prompt.split("MISSION DATA JSON:\n", 1)[1])
    assert context["file_progress"][path]["next_offset"] == 12000
    with pytest.raises(PolicyError, match="not been read"):
        h.company.commit_turn(third_id, premature.model_copy(update={"request_id": third_id}))
    h.company.commit_turn(third_id, read(third_id, 12000))
    with h.company.db.transaction() as conn:
        final_id = str(conn.execute("SELECT id FROM turns WHERE task_id=%s AND status='queued'",
                                    (stage["task_id"],)).fetchone()["id"])
    h.company.prepare_turn(final_id)
    valid = ProviderResponse(request_id=final_id, provider="fixture", decision=AgentDecision(
        say="", status="complete", artifacts=[{"title": "Synthetic proposal", "content": json.dumps(proposal)}]))
    assert h.company.commit_turn(final_id, valid)["state"] == "completed"
    assert controller.tick()["state"] == "completed"
    with h.company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM research_program_tasks").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM research_stage_reads WHERE stage_id=%s",
                            (stage["id"],)).fetchone()["n"] == 2


def test_data_packet_is_bound_read_and_keeps_blocked_assessment_independent(program):
    h = program
    packet = provision_data_packet(h)
    with h.company.db.transaction() as conn:
        program_row = conn.execute("SELECT * FROM research_programs WHERE id=%s", (h.program_id,)).fetchone()
    assert set(load_packets(h.company, program_row, {"etf": h.program_spec.envelopes[0]})) == {"etf"}
    with h.company.db.transaction() as conn:
        task_id = h.program_store.propose(conn, h.program_id, task_proposal(), actor="researcher_kr")
    controller = ProgramController(h.company)
    assert controller.tick()["state"] == "running"
    with h.company.db.transaction() as conn:
        stage = conn.execute("SELECT * FROM research_mission_stages WHERE program_id=%s", (h.program_id,)).fetchone()
        turn_id = str(conn.execute("SELECT id FROM turns WHERE task_id=%s", (stage["task_id"],)).fetchone()["id"])
    assert stage["stage"] == "program_data"
    required = stage["context"]["required_data_reads"]
    assert len(required) == 3 and stage["context"]["data_evidence_packets"][0]["envelope"] == "etf"
    assert "data/etf/inputs/development.csv" in stage["context"]["_private_files"]
    blocked = {"decision": "blocked", "rationale": "Synthetic packet cannot establish market provenance",
        "source_ids": ["fixture:baseline"], "point_in_time": False, "coverage": False,
        "executable_prices": False, "original_conditions": False}

    def reply(identity, *, path=None):
        decision = (AgentDecision(say="", status="continue", tools=[{"name": "research_control",
            "arguments": {"action": "read_stage_file", "path": path, "offset": 0}}]) if path else
            AgentDecision(say="", status="complete", artifacts=[{"title": "Synthetic assessment",
                "content": json.dumps(blocked)}]))
        return ProviderResponse(request_id=identity, provider="fixture", decision=decision)

    h.company.prepare_turn(turn_id)
    assert h.company.commit_turn(turn_id, reply(turn_id))["incomplete_data_evidence"]
    for path in [*required, stage["context"]["evidence_sources"][0]["file"]]:
        with h.company.db.transaction() as conn:
            current = str(conn.execute("SELECT id FROM turns WHERE task_id=%s AND status='queued'",
                (stage["task_id"],)).fetchone()["id"])
        h.company.prepare_turn(current)
        h.company.commit_turn(current, reply(current, path=path))
    with h.company.db.transaction() as conn:
        final = str(conn.execute("SELECT id FROM turns WHERE task_id=%s AND status='queued'",
            (stage["task_id"],)).fetchone()["id"])
    h.company.prepare_turn(final)
    assert h.company.commit_turn(final, reply(final))["state"] == "completed"
    assert controller.tick()["state"] == "completed"
    with h.company.db.transaction() as conn:
        task = conn.execute("SELECT * FROM research_program_tasks WHERE id=%s", (task_id,)).fetchone()
        assert task["data_assessment"]["decision"] == "blocked"
        assert conn.execute("SELECT count(*) AS n FROM research_stage_reads WHERE stage_id=%s",
            (stage["id"],)).fetchone()["n"] == len(required) + 1
    assert packet["program_digest"] == h.digest


def test_data_packet_rejects_changed_files_and_unapproved_input_identity(program):
    h = program
    packet = provision_data_packet(h)
    with h.company.db.transaction() as conn:
        program_row = conn.execute("SELECT * FROM research_programs WHERE id=%s", (h.program_id,)).fetchone()
    envelopes = {"etf": h.program_spec.envelopes[0]}
    report = Path(packet["reports"]["limitations.txt"]["path"])
    report.write_text("Changed after registration")
    with pytest.raises(PolicyError, match="changed"):
        load_packets(h.company, program_row, envelopes)
    packet["reports"]["limitations.txt"]["sha256"] = hashlib.sha256(report.read_bytes()).hexdigest()
    packet["input_files"]["development.csv"]["sha256"] = "0" * 64
    h.company.settings.research_data_evidence_file.write_text(json.dumps({"packets": [packet]}))
    with pytest.raises(PolicyError, match="approved program"):
        load_packets(h.company, program_row, envelopes)


def test_real_profile_ready_rejects_missing_or_blocked_packet(program, monkeypatch):
    h = program
    with h.company.db.transaction() as conn:
        task_id = h.program_store.propose(conn, h.program_id, task_proposal(), actor="researcher_kr")
    monkeypatch.setattr("quant_company.research.programs.profile_for",
        lambda *_: SimpleNamespace(public_profile=SimpleNamespace(fixture_only=False)))
    with pytest.raises(PolicyError, match="verified input evidence packet"):
        with h.company.db.transaction() as conn:
            h.program_store.assess(conn, h.program_id, task_id, ready(), actor="data")
    provision_data_packet(h)
    with pytest.raises(PolicyError, match="blocking gaps"):
        with h.company.db.transaction() as conn:
            h.program_store.assess(conn, h.program_id, task_id, ready(), actor="data")


def test_data_packet_rejects_file_outside_managed_store(program):
    h = program
    packet = provision_data_packet(h)
    outside = h.company.settings.research_artifact_dir.parent / "evaluator.py"
    outside.write_bytes(Path(packet["engine"]["path"]).read_bytes())
    packet["engine"]["path"] = str(outside)
    h.company.settings.research_data_evidence_file.write_text(json.dumps({"packets": [packet]}))
    with h.company.db.transaction() as conn:
        program_row = conn.execute("SELECT * FROM research_programs WHERE id=%s", (h.program_id,)).fetchone()
    with pytest.raises(PolicyError, match="outside the managed store"):
        load_packets(h.company, program_row, {"etf": h.program_spec.envelopes[0]})


def test_concurrent_budget_reservations_are_serialized_and_uncertain_jobs_hold_budget(program):
    h = program
    create_task(h)
    # Exercise the real resource ledger independently of model/worker execution.
    with h.company.db.transaction() as conn:
        proposal = h.proposal()
        h.store.add_proposal(conn, h.mission_id, proposal, actor="researcher_kr")
        trial_id = uuid4()
        conn.execute("""INSERT INTO research_mission_trials(id,mission_id,proposal_id,cycle,selection,selection_digest)
            VALUES(%s,%s,%s,1,'{}','fixture')""", (trial_id, h.mission_id, proposal.id))
        mission = conn.execute("SELECT * FROM research_missions WHERE id=%s", (h.mission_id,)).fetchone()
    def attempt(_):
        try:
            with h.company.db.transaction() as conn:
                h.company._project(conn, h.project["project_id"])
                job = uuid4()
                conn.execute("""INSERT INTO research_jobs(id,project_id,task_id,revision,recipe_id,manifest,manifest_digest,
                    company_commit,state) VALUES(%s,%s,%s,1,'kr-research-python-v2',%s,%s,%s,'queued')""",
                    (job, h.project["project_id"], h.project["task_id"], Jsonb({}), str(job), "b"*40))
                h.program_store.reserve(conn, mission, job, trial_id, 6000)
            return True
        except PolicyError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [False, True]
    with h.company.db.transaction() as conn:
        assert h.program_store.usage(conn, h.program_id)["compute_seconds"] == 6000
        assert h.program_store.usage(conn, h.program_id)["outstanding"] == 1


def test_new_evaluation_fields_do_not_change_legacy_mission_bytes():
    from .test_research_missions import spec_payload

    spec = MissionSpec.model_validate(spec_payload())
    assert "evaluation" not in spec.model_dump(mode="json") and "market" not in spec.model_dump(mode="json")
