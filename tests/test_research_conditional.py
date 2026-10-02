"""Conditional authority on real PostgreSQL; synthetic inputs and scripted employees only."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from psycopg.types.json import Jsonb
from pydantic import ValidationError as ModelValidationError

from quant_company.company import Company, PolicyError, fingerprint, now
from quant_company.research.adaptive_contracts import (
    AdaptiveAssignment,
    AdaptiveExecutionProfile,
    AdaptiveExecutionReceipt,
    AdaptiveManifest,
    AdaptiveQualification,
    AdaptiveResult,
    digest_model,
)
from quant_company.research.adaptive_executor import (
    bind_producer_record,
    qualification_from_file,
    validate_result_files,
)
from quant_company.research.adaptive_report import build_adaptive_report
from quant_company.research.audit import audit_publication, prepare_audit_package, verify_audit_package
from quant_company.research.builds import ServerResearchProfile, profile_for
from quant_company.research.data_evidence import DataEvidencePacket
from quant_company.research.domestic_profile import prepare_domestic_profile
from quant_company.research.executor import ExecutionBlocked
from quant_company.research.feedback import resolve_challenges
from quant_company.research.mission_contracts import (
    AuditPublication,
    EvidenceRef,
    Interpretation,
    MissionSpec,
    TrialOutcome,
    TrialPlan,
)
from quant_company.research.policy_contracts import (
    ResearchDataPolicy,
    ScientificLineageAuthority,
    scoped_kwargs,
)
from quant_company.research.program_contracts import DataAssessment, ResearchProgram
from quant_company.research.program_controller import program_tool
from quant_company.research.programs import ProgramStore
from quant_company.research.reference.domestic_engine import run
from quant_company.research.report import ValidationError
from quant_company.research.scientific_lineages import bind_experiment, history, overview, usage

from .test_research_adaptive_report import (
    encoded,
    history_for,
    mutate,
    producer,
    sha,
    validate,
    write_archive,
)
from .test_research_audit import binding_for, qlab_profile, write  # noqa: F401
from .test_research_domestic_profile import prepare_case
from .test_research_programs import program, task_proposal  # noqa: F401


def policy_for(inputs):
    return ResearchDataPolicy(mode="frozen_vintage_retrospective", input_files=inputs,
        evidence_refs=[{"name": "limitations.txt", "sha256": sha(b"Synthetic historical timing is unverified.")}],
        availability_status="unverified_historical", assumed_available_at_rule="trade_day_T23:59:00+09:00",
        result_scope="conditional_retrospective_development",
        acknowledged_gap_codes=["historical_publication_unverified", "historical_revision_vintage_unverified"])


def scoped_spec(original, public, authority):
    policy = policy_for(original.data.input_files)
    return MissionSpec.model_validate(original.model_dump(mode="json") | {
        "schema_version": 3, "market": "kr_etf",
        "evaluation": {"kind": "strategy", "metric": "stress-net-absolute-cagr"},
        "data_policy": policy.model_dump(mode="json"), "scientific_lineage": authority,
        "execution_profile": public.id, "execution_profile_digest": digest_model(public),
        "search": {"max_trials_per_cycle": 2, "max_total_trials": 4, "patience": 2,
                   "min_improvement": .001, "continuous": False}})


def scoped_profile(original, policy):
    return AdaptiveExecutionProfile.model_validate(original.model_dump(mode="json") | {
        "schema_version": 2, "id": "synthetic-retrospective-v1", "data_policy_digest": fingerprint(policy.model_dump(
            mode="json")), "result_scope": policy.result_scope})


def conditional_producer(tmp_path):
    path, old_assignment, old, old_profile, contents = producer(tmp_path)
    profile = scoped_profile(old_profile, policy_for(old.spec.data.input_files))
    authority = {"id": str(uuid4()), "max_total_trials": 4, "history_digest": "8" * 64,
                 "originating_task_refs": []}
    spec = scoped_spec(old.spec, profile, authority)
    plan = TrialPlan.model_validate(old.plan.model_dump(mode="json") | scoped_kwargs(spec) | {
        "execution_profile": profile.id, "mission_digest": fingerprint(spec.model_dump(mode="json"))})
    manifest = AdaptiveManifest.model_validate(old.model_dump(mode="json") | {
        "id": "kr-research-python-v2", "spec": spec.model_dump(mode="json"), "plan": plan.model_dump(mode="json"),
        "mission_digest": plan.mission_digest, "plan_digest": fingerprint(plan.model_dump(mode="json"))})
    assignment = AdaptiveAssignment.model_validate(old_assignment.model_dump(mode="json") | {
        "recipe_id": manifest.id, "manifest": manifest.model_dump(mode="json"), "manifest_digest": digest_model(manifest)})
    for name, cls in (("qualification.json", AdaptiveQualification), ("result.json", AdaptiveResult)):
        payload = json.loads(contents[name]) | {"plan_digest": manifest.plan_digest}
        original_bytes = encoded(payload)
        source = tmp_path / ("producer-" + name)
        source.write_bytes(original_bytes)
        contents["producer-" + name] = original_bytes
        contents[name] = bind_producer_record(cls.model_validate_json(original_bytes), source, manifest).model_dump_json().encode()
    contents["manifest.json"] = manifest.model_dump_json().encode()
    contents["profile.json"] = profile.model_dump_json().encode()
    for name in ("runtime.json", "sandbox-qualification.json", "sandbox-evaluation.json"):
        payload = json.loads(contents[name]) | {"execution_profile_digest": digest_model(profile),
                                               "research_scope": spec.research_scope.model_dump(mode="json")}
        if name.startswith("sandbox-"):
            payload.update(profile_id=profile.id, manifest_digest=digest_model(manifest))
        contents[name] = encoded(payload)
    receipt = json.loads(contents["receipt.json"]) | scoped_kwargs(spec) | {
        "recipe_id": manifest.id, "manifest_digest": digest_model(manifest), "mission_digest": manifest.mission_digest,
        "plan_digest": manifest.plan_digest, "execution_profile_digest": digest_model(profile)}
    for name, field in (("qualification.json", "qualification_sha256"), ("result.json", "result_sha256"),
                        ("runtime.json", "runtime_sha256"), ("sandbox-qualification.json", "sandbox_qualification_sha256"),
                        ("sandbox-evaluation.json", "sandbox_evaluation_sha256")):
        receipt[field] = sha(contents[name])
    contents["receipt.json"] = AdaptiveExecutionReceipt.model_validate(receipt).model_dump_json().encode()
    write_archive(path, contents)
    return path, assignment, manifest, profile, contents


def prepare_conditional_case(tmp_path, runtime=None, company_commit="c" * 40):
    """Real protected engine with generated data; never reads market snapshots."""
    case = prepare_case(tmp_path, "kr_etf", "strategy", runtime, company_commit)
    policy = policy_for(case.manifest.spec.data.input_files)
    report = tmp_path / "limitations.txt"
    report.write_bytes(b"Synthetic historical timing is unverified.")
    runtime = json.loads((case.destination / "worker-profile.json").read_text())["runtime"]
    runtime["profile_id"] = "kr-etf-retrospective-v1"
    destination = tmp_path / "conditional-release"
    identity = prepare_domestic_profile(source_bundle=case.server.source_bundle,
        source_sha256=case.server.source_bundle_sha256, base_commit=case.server.base_commit, destination=destination,
        runtime=runtime, input_sources={name: case.inputs / name for name in policy.input_files}, market="kr_etf",
        qualification_seconds=30, evaluation_seconds=30, fixture_only=True, data_policy=policy,
        policy_files={"limitations.txt": report})
    server = ServerResearchProfile.model_validate_json((destination / "server-profile.json").read_text())
    authority = {"id": str(uuid4()), "max_total_trials": 4, "history_digest": "8" * 64,
                 "originating_task_refs": []}
    spec = scoped_spec(case.manifest.spec, server.public_profile, authority)
    plan = TrialPlan.model_validate(case.manifest.plan.model_dump(mode="json") | scoped_kwargs(spec) | {
        "execution_profile": server.public_profile.id, "mission_digest": fingerprint(spec.model_dump(mode="json"))})
    manifest = AdaptiveManifest.model_validate(case.manifest.model_dump(mode="json") | {
        "spec": spec.model_dump(mode="json"), "plan": plan.model_dump(mode="json"), "mission_digest": plan.mission_digest,
        "plan_digest": fingerprint(plan.model_dump(mode="json"))})
    case.manifest, case.server, case.destination, case.receipt = manifest, server, destination, identity
    case.path.write_text(manifest.model_dump_json())
    return case


def lineage_history(trial):
    return {"scientific_lineage_id": str(trial.manifest.spec.scientific_lineage.id), "project_id": str(uuid4()),
        "trial_limit": 4, "charged_trials": 2, "completed_trials": 2, "technical_failures": 1,
        "origins": [{"prior_negative": True}], "trials": [{"prior_negative": True}],
        "experiments": [], "publications": []}


@pytest.fixture
def conditional(program):  # noqa: F811
    h = program
    old_spec = h.program_spec.envelopes[0].template
    public = scoped_profile(h.public, policy_for(old_spec.data.input_files))
    profiles = json.loads(h.company.settings.research_profiles_file.read_text())
    raw = next(iter(profiles.values())) | {"public_profile": public.model_dump(mode="json")}
    profiles[public.id] = ServerResearchProfile.model_validate(raw).model_dump(mode="json")
    h.company.settings.research_profiles_file.write_text(json.dumps(profiles))
    with h.company.db.transaction() as conn:
        conn.execute("UPDATE research_programs SET state='cancelled' WHERE id=%s", (h.program_id,))
        authority = ScientificLineageAuthority(id=uuid4(), max_total_trials=4, history_digest="0" * 64,
                                               originating_task_refs=[])
        _, digest = history(conn, UUID(h.project["project_id"]), authority)
    authority = authority.model_copy(update={"history_digest": digest})
    spec = scoped_spec(old_spec, public, authority)
    h.program_spec = ResearchProgram(schema_version=2, title="Synthetic conditional bounded program",
        objective="Verify conditional authority; no market research", envelopes=[{"name": "etf", "market": "kr_etf",
            "template": spec}], max_total_trials=4, max_compute_seconds=7200, max_missions=1,
        max_parallel_missions=1, source_ids=["fixture:baseline"], include_quant_feed=False)
    with h.company.db.transaction() as conn:
        row = h.program_store.create(conn, h.company._project(conn, h.project["project_id"]), h.program_spec)
    h.program_id, h.digest, h.mission_id = row["id"], row["manifest_digest"], row["id"]
    event = h.owner_event("approve", target={"kind": "program", "target_id": h.program_id,
                                             "revision": 1, "manifest_digest": h.digest})
    h.approval = event
    with h.company.db.transaction() as conn:
        h.program_store.owner_command(conn, h.program_id, **h.owner_args(event), action="approve")
        conn.execute("UPDATE turns SET status='stale'")
        conn.execute("UPDATE tasks SET status='completed'")
    root = h.company.settings.research_artifact_dir / "provisioned/data-evidence"
    root.mkdir(parents=True)

    def put(name, content):
        target = root / name
        target.write_bytes(content)
        return {"path": str(target), "sha256": sha(content)}

    inputs = {"qualification.json": put("qualification.json", b'{"synthetic_fixture":true}'),
              "development.csv": put("development.csv", b"synthetic\n")}
    h.packet = DataEvidencePacket(**scoped_kwargs(spec), program_digest=h.digest, envelope="etf",
        execution_profile_digest=spec.execution_profile_digest, lake_id=spec.data.lake_id, input_files=inputs,
        engine=put("evaluator.py", h.original[4]["code/evaluator.py"]),
        reports={"limitations.txt": put("limitations.txt", b"Synthetic historical timing is unverified.")},
        blocking_gaps=[], gaps=[{"code": code, "description": "Synthetic timing limitation", "report_names": [
            "limitations.txt"], "input_files": spec.data.input_files} for code in spec.data_policy.acknowledged_gap_codes])
    h.company.settings.research_data_evidence_file = root / "registry.json"
    h.company.settings.research_data_evidence_file.write_text(json.dumps({"packets": [h.packet.model_dump(mode="json")]}))
    h.public = public
    return h


def conditional_ready(h, **changes):
    spec = h.program_spec.envelopes[0].template
    return {**scoped_kwargs(spec), "decision": "conditional_ready", "rationale": "Synthetic evaluation contract verified",
        "source_ids": ["fixture:baseline"], "point_in_time": False, "coverage": True, "executable_prices": False,
        "original_conditions": False, "evaluation_price_contract_verified": True,
        "packet_digest": fingerprint(h.packet.model_dump(mode="json")), **changes}


def conditional_task(h):
    with h.company.db.transaction() as conn:
        task_id = h.program_store.propose(conn, h.program_id, task_proposal(mode="novel_hypothesis"), actor="researcher_kr")
        h.program_store.assess(conn, h.program_id, task_id, conditional_ready(h), actor="data")
        h.program_store.decide(conn, h.program_id, task_id, {"decision": "accept", "rationale": "Conditional fixture only"},
                               actor="director")
        mission = conn.execute("""SELECT * FROM research_missions WHERE id=(SELECT mission_id FROM research_program_tasks
            WHERE id=%s)""", (task_id,)).fetchone()
    h.mission_id, h.digest, h.spec = str(mission["id"]), mission["manifest_digest"], MissionSpec.model_validate(mission["spec"])
    return task_id, mission


def reserved_trial(h, signature="a" * 64):
    proposal = h.add()
    challenge = h.challenge(proposal)
    with h.company.db.transaction() as conn:
        resolve_challenges(h.company, conn, h.snapshot(), proposal.id, {
            "decision": "execute", "rationale": "Retain independent fixture test", "responses": [{
                "challenge_id": str(challenge.id), "disposition": "test", "rationale": "Typed identity test",
                "test_plan": "Verify scoped producer and consumer reject tampering", "source_ids": ["fixture:baseline"]}]},
            "director")
    trial = str(uuid4())
    h.invoke("select", h.mission_id, proposal.id, actor="director", challenge_ids=[challenge.id],
             rationale="Synthetic scoped test", trial_id=trial)
    plan = TrialPlan(**scoped_kwargs(h.spec), trial_id=trial, proposal_id=proposal.id, mission_digest=h.digest,
        execution_profile=h.spec.execution_profile, implementer="engineer", repository="quant-lab",
        code_commit=h.spec.code.base_commit, changed_paths=["candidate.py"], config_files={"config.json": "d" * 64},
        input_files=h.spec.data.input_files, lake_id=h.spec.data.lake_id, development=h.spec.development,
        worker_id="worker", hostname="DESKTOP-5T00NAF", gpu="NVIDIA GeForce RTX 3070")
    h.invoke("set_plan", h.mission_id, trial, plan, actor="engineer")
    with h.company.db.transaction() as conn:
        h.company._project(conn, h.project["project_id"])
        bind_experiment(conn, h.project["project_id"], h.spec.scientific_lineage.id, trial, signature)
    job = str(uuid4())
    with h.company.db.transaction() as conn:
        h.company._project(conn, h.project["project_id"])
        mission = conn.execute("SELECT * FROM research_missions WHERE id=%s", (h.mission_id,)).fetchone()
        payload = {"mission_id": h.mission_id, "mission_digest": h.digest, "trial_id": trial,
                   "plan_digest": fingerprint(plan.model_dump(mode="json"))}
        conn.execute("""INSERT INTO research_jobs(id,project_id,task_id,revision,recipe_id,manifest,manifest_digest,
            company_commit,state,approval_event_id,approved_by) VALUES(%s,%s,%s,1,'kr-research-python-v2',%s,%s,%s,
            'queued',%s,'UHUMAN')""", (job, mission["project_id"], h.project["task_id"], Jsonb(payload),
            fingerprint(payload), "c" * 40, h.approval))
        h.program_store.reserve(conn, mission, job, trial, 30)
        h.store.attach_job(conn, h.mission_id, trial, job)
    return trial, plan, job


def fixture_outcome(h, plan, *, technical=False):
    payload = {**scoped_kwargs(h.spec), "id": uuid4(), "plan": plan, "started_at": now() - timedelta(seconds=3),
        "finished_at": now() - timedelta(seconds=1), "status": "technical_failure" if technical else "result",
        "evidence_files": {"qualification.json": "1" * 64, "result.json": "2" * 64}}
    if technical:
        payload.update(failure_code="synthetic_failure", resume_condition="Correct the recorded fixture failure")
    else:
        payload.update(qualification=EvidenceRef(path="qualification.json", sha256="1" * 64),
            result=EvidenceRef(path="result.json", sha256="2" * 64), metrics={
                "primary": {**h.spec.objective.model_dump(), "value": -.01}, "risks": {"max_drawdown": .01},
                "sample_count": 4, "sample_window": h.spec.development})
    return TrialOutcome.model_validate(payload)


def finish_negative_fixture(h, trial, plan):
    outcome = fixture_outcome(h, plan)
    h.invoke("record_outcome", h.mission_id, trial, outcome)
    interpretation = Interpretation(**scoped_kwargs(h.spec), trial_id=trial, author="researcher_kr",
        outcome_digest=fingerprint(outcome.model_dump(mode="json")), conclusion="Negative synthetic fixture only",
        next_hypothesis="Inspect a distinct synthetic configuration", source_ids=["fixture:baseline"])
    h.invoke("interpret", h.mission_id, trial, interpretation, actor="researcher_kr")
    source_id = "fixture:conditional:" + trial
    # Internal checkpoint test seal; genuine qlab-file conversion is tested separately.
    with h.company.db.transaction() as conn:
        conn.execute("""INSERT INTO sources(id,title,uri,content,available_at,project_id,approved,synthetic,metadata)
            VALUES(%s,'Synthetic negative result','fixture://negative','Negative synthetic fixture only',now(),%s,true,
            true,%s)""", (source_id, h.project["project_id"], Jsonb({"research_scope": h.spec.research_scope.model_dump(mode="json")})))
    publication = AuditPublication(**scoped_kwargs(h.spec), trial_id=trial, mission_digest=h.digest,
        outcome_digest=fingerprint(outcome.model_dump(mode="json")), source_id=source_id,
        audit_files={"verification.json": "4" * 64}, verification=EvidenceRef(path="verification.json", sha256="4" * 64),
        report=EvidenceRef(path="report.html", sha256="5" * 64), verifier_role="validator", verifier_commit="6" * 40,
        published_at=now() - timedelta(seconds=1))
    h.invoke("checkpoint", h.mission_id, trial, verify=lambda: publication)
    with h.company.db.transaction() as conn:
        conn.execute("""UPDATE research_jobs SET state='completed' WHERE id=(SELECT job_id FROM research_mission_trials
            WHERE id=%s)""", (trial,))
    return outcome


def successor_program(h, prior_task, *, cap=2):
    old_program = h.program_id
    with h.company.db.transaction() as conn:
        task = conn.execute("SELECT * FROM research_program_tasks WHERE id=%s", (prior_task,)).fetchone()
        conn.execute("UPDATE research_programs SET state='cancelled' WHERE id=%s", (old_program,))
        conn.execute("UPDATE research_missions SET state='cancelled' WHERE program_id=%s", (old_program,))
        authority = ScientificLineageAuthority(id=h.spec.scientific_lineage.id, max_total_trials=cap, history_digest="0" * 64,
            originating_task_refs=[{"program_id": old_program, "task_id": prior_task, "task_digest": task["digest"]}])
        _, digest = history(conn, UUID(h.project["project_id"]), authority)
    authority = authority.model_copy(update={"history_digest": digest})
    template = h.program_spec.envelopes[0].template.model_dump(mode="json")
    template["scientific_lineage"] = authority.model_dump(mode="json")
    template["search"]["max_total_trials"] = cap
    spec = ResearchProgram.model_validate(h.program_spec.model_dump(mode="json") | {
        "title": "Successor synthetic program retains negative history", "max_total_trials": cap,
        "envelopes": [{"name": "etf", "market": "kr_etf", "template": template}]})
    with h.company.db.transaction() as conn:
        row = h.program_store.create(conn, h.company._project(conn, h.project["project_id"]), spec)
    h.program_spec, h.program_id, h.digest, h.mission_id = spec, row["id"], row["manifest_digest"], row["id"]
    event = h.owner_event("approve", target={"kind": "program", "target_id": h.program_id,
                                             "revision": 1, "manifest_digest": h.digest})
    h.approval = event
    with h.company.db.transaction() as conn:
        h.program_store.owner_command(conn, h.program_id, **h.owner_args(event), action="approve")
    h.packet = DataEvidencePacket.model_validate(h.packet.model_dump(mode="json") | {"program_digest": h.digest})
    h.company.settings.research_data_evidence_file.write_text(json.dumps({"packets": [h.packet.model_dump(mode="json")]}))
    return spec


def test_frozen_legacy_program_and_profile_encodings_remain_exact():
    root = Path(__file__).resolve().parents[1]
    original = json.loads((root / "docs/project/evidence/research-programs-20260928/first-program-draft.json").read_text())["program"]
    value = ResearchProgram.model_validate(original)
    assert value.model_dump(mode="json") == original
    assert fingerprint(original) == "53392822414ca32e89fab0f3a9a1093a350196345bd13084654a45510297159b"
    assessment = {"decision": "ready", "rationale": "Original", "source_ids": ["original"], "point_in_time": True,
                  "coverage": True, "executable_prices": True, "original_conditions": False}
    assert DataAssessment.model_validate(assessment).model_dump(mode="json") == assessment


@pytest.mark.parametrize("change", [{"decision": "ready", "point_in_time": True, "executable_prices": True},
    {"point_in_time": True}, {"original_conditions": True}, {"executable_prices": True}, {"coverage": False},
    {"evaluation_price_contract_verified": False}, {"packet_digest": None}, {"schema_version": 1}])
def test_conditional_facts_cannot_be_upgraded_or_weakened(conditional, change):
    with pytest.raises(ModelValidationError):
        DataAssessment.model_validate(conditional_ready(conditional, **change))


@pytest.mark.parametrize("mode", ["exact_replication", "market_transfer"])
def test_conditional_program_rejects_replication_and_transfer(conditional, mode):
    h = conditional
    with pytest.raises(PolicyError, match="novel hypotheses only"):
        with h.company.db.transaction() as conn:
            h.program_store.propose(conn, h.program_id, task_proposal(mode=mode), actor="researcher_kr")


@pytest.mark.parametrize("gap", ["coverage_gap", "causal_code_violation", "price_estimand_unverified", "unknown_problem"])
def test_unknown_or_hard_gap_cannot_become_a_scope_limitation(conditional, gap):
    h = conditional
    packet = h.packet.model_dump(mode="json")
    packet["gaps"].append({"code": gap, "description": "Unresolved problem", "report_names": ["limitations.txt"],
                           "input_files": h.spec.data.input_files if hasattr(h, "spec") else packet["gaps"][0]["input_files"]})
    h.packet = DataEvidencePacket.model_validate(packet)
    h.company.settings.research_data_evidence_file.write_text(json.dumps({"packets": [h.packet.model_dump(mode="json")]}))
    with pytest.raises(PolicyError, match="Unclassified"):
        conditional_task(h)


def test_assessment_rechecks_actual_packet_at_selection(conditional):
    h = conditional
    with h.company.db.transaction() as conn:
        task = h.program_store.propose(conn, h.program_id, task_proposal(mode="novel_hypothesis"), actor="researcher_kr")
        h.program_store.assess(conn, h.program_id, task, conditional_ready(h), actor="data")
    report = Path(h.packet.reports["limitations.txt"].path)
    report.write_text("Changed after independent assessment")
    with pytest.raises(PolicyError, match="changed"):
        with h.company.db.transaction() as conn:
            h.program_store.decide(conn, h.program_id, task, {"decision": "accept", "rationale": "Wrong"}, actor="director")


def test_assessment_cannot_cite_a_different_packet(conditional):
    h = conditional
    with pytest.raises(PolicyError, match="different evidence packet"):
        with h.company.db.transaction() as conn:
            task = h.program_store.propose(conn, h.program_id, task_proposal(mode="novel_hypothesis"), actor="researcher_kr")
            h.program_store.assess(conn, h.program_id, task, conditional_ready(h, packet_digest="0" * 64), actor="data")


def test_old_profile_cannot_run_a_new_policy_even_when_its_own_digest_matches(conditional):
    h = conditional
    old = h.original[3]
    profiles = json.loads(h.company.settings.research_profiles_file.read_text())
    old = next(ServerResearchProfile.model_validate(raw).public_profile for raw in profiles.values()
               if raw["public_profile"]["schema_version"] == 1)
    payload = h.program_spec.envelopes[0].template.model_dump(mode="json") | {
        "execution_profile": old.id, "execution_profile_digest": digest_model(old)}
    with pytest.raises(PolicyError, match="policy or result scope"):
        profile_for(h.company, MissionSpec.model_validate(payload))


def test_conditional_template_cannot_hide_replication_in_a_novel_task(conditional):
    payload = conditional.program_spec.envelopes[0].template.model_dump(mode="json")
    payload.update(kind="claim", risk_constraints=[],
        objective={"metric": "absolute-replication-error", "direction": "minimize", "unit": "fraction"},
        evaluation={"kind": "replication", "metric": "absolute-replication-error", "target": .1, "tolerance": .01})
    with pytest.raises(ModelValidationError, match="replication estimand"):
        MissionSpec.model_validate(payload)


def test_read_only_lineage_tool_exposes_preimage_without_authorizing_it(conditional):
    h = conditional
    identity = uuid4()
    with h.company.db.transaction() as conn:
        project = h.company._project(conn, h.project["project_id"])
        value = program_tool(h.company, conn, project, {"agent": "director"}, {
            "action": "program_lineage_history", "scientific_lineage_id": str(identity), "originating_task_refs": []})
        assert value["history_digest"] == fingerprint(value["history"])
        assert value["history"]["trial_limit"] is None and value["history"]["trials"] == []
        assert not conn.execute("SELECT 1 FROM research_scientific_lineages WHERE id=%s", (identity,)).fetchone()


def test_conditional_acceptance_keeps_false_facts_and_survives_store_restart(conditional):
    h = conditional
    task, mission = conditional_task(h)
    restored = Company(h.company.settings, h.company.roles)
    with restored.db.transaction() as conn:
        ProgramStore(restored).require_authorized(conn, mission)
        assessment = conn.execute("SELECT data_assessment FROM research_program_tasks WHERE id=%s", (task,)).fetchone()["data_assessment"]
        lineage = overview(conn, UUID(h.project["project_id"]), h.spec.scientific_lineage.id)
        assert assessment["decision"] == "conditional_ready"
        assert assessment["point_in_time"] is assessment["original_conditions"] is assessment["executable_prices"] is False
        assert str(lineage["origins"][0]["id"]) == task
    assert h.snapshot(public=True)["research_scope"] == h.spec.research_scope.model_dump(mode="json")


def test_duplicate_create_after_owner_approval_is_idempotent(conditional):
    h = conditional
    with h.company.db.transaction() as conn:
        value = h.program_store.create(conn, h.company._project(conn, h.project["project_id"]), h.program_spec)
        assert value["id"] == h.program_id and value["state"] == "active"
        assert conn.execute("SELECT count(*) AS n FROM research_program_lineage_authorizations").fetchone()["n"] == 1


def test_direct_mission_approval_cannot_bypass_scoped_program_authority(conditional):
    h = conditional
    spec = h.program_spec.envelopes[0].template
    h.mission_id = h.invoke("create", h.project["project_id"], "UHUMAN", 1, spec)["id"]
    h.digest = fingerprint(spec.model_dump(mode="json"))
    event = h.owner_event("approve")
    with pytest.raises(PolicyError, match="program"):
        h.invoke("approve", h.mission_id, **h.owner_args(event))


def test_unchanged_configuration_cannot_move_to_new_title_or_lineage(conditional):
    h = conditional
    _, mission = conditional_task(h)
    trial, _, _ = reserved_trial(h)
    with h.company.db.transaction() as conn:
        h.company._project(conn, h.project["project_id"])
        bind_experiment(conn, h.project["project_id"], h.spec.scientific_lineage.id, trial, "a" * 64)
    with pytest.raises(PolicyError, match="cannot reset"):
        with h.company.db.transaction() as conn:
            h.company._project(conn, mission["project_id"])
            bind_experiment(conn, mission["project_id"], uuid4(), uuid4(), "a" * 64)


def test_uncertain_reservation_counts_and_atomic_lineage_cap(conditional):
    h = conditional
    _, mission = conditional_task(h)
    trial, _, first_job = reserved_trial(h)
    with h.company.db.transaction() as conn:
        conn.execute("UPDATE research_jobs SET state='uncertain' WHERE id=%s", (first_job,))
        conn.execute("UPDATE research_scientific_lineages SET trial_limit=1")
        h.program_store.reserve(conn, mission, first_job, trial, 30)

    def attempt(trial):
        try:
            with h.company.db.transaction() as conn:
                h.company._project(conn, mission["project_id"])
                job = uuid4()
                conn.execute("""INSERT INTO research_jobs(id,project_id,task_id,revision,recipe_id,manifest,manifest_digest,
                    company_commit,state) VALUES(%s,%s,%s,1,'kr-research-python-v2','{}',%s,%s,'queued')""",
                    (job, mission["project_id"], h.project["task_id"], str(job), "c" * 40))
                h.program_store.reserve(conn, mission, job, trial, 30)
            return True
        except PolicyError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(attempt, [trial, trial])) == [False, False]
    with h.company.db.transaction() as conn:
        assert usage(conn, h.spec.scientific_lineage.id) == 1
        assert h.program_store.usage(conn, h.program_id)["outstanding"] == 1


def test_negative_results_and_budget_persist_across_new_program_and_signature(conditional):
    h = conditional
    prior_task, _ = conditional_task(h)
    trial, plan, _ = reserved_trial(h)
    old_outcome = finish_negative_fixture(h, trial, plan)
    successor = successor_program(h, prior_task)
    with h.company.db.transaction() as conn:
        lineage = overview(conn, UUID(h.project["project_id"]), h.spec.scientific_lineage.id)
        assert lineage["completed_trials"] == lineage["charged_trials"] == 1
        assert lineage["trials"][0]["outcome_digest"] == fingerprint(old_outcome.model_dump(mode="json"))
        assert h.program_store.usage(conn, h.program_id)["trials"] == 0
        assert successor.envelopes[0].template.scientific_lineage.max_total_trials == 2
    conditional_task(h)
    next_trial, next_plan, _ = reserved_trial(h, signature="b" * 64)
    finish_negative_fixture(h, next_trial, next_plan)
    with h.company.db.transaction() as conn:
        lineage = overview(conn, UUID(h.project["project_id"]), h.spec.scientific_lineage.id)
        assert lineage["completed_trials"] == lineage["charged_trials"] == 2
        assert len(lineage["origins"]) == 2 and len(lineage["publications"]) == 2
    assert h.snapshot()["stage"]["stage"] == "owner_review"
    with pytest.raises(PolicyError):
        h.invoke("advance_cycle", h.mission_id, cycle=1, actor="director", rationale="Unauthorized extra cycle",
                 source_ids=["fixture:baseline"], predecessor_trial_ids=[next_trial])


def test_technical_failure_releases_science_slot_keeps_charge_and_scope(conditional):
    h = conditional
    conditional_task(h)
    trial, plan, job = reserved_trial(h)
    outcome = fixture_outcome(h, plan, technical=True)
    h.invoke("record_outcome", h.mission_id, trial, outcome)
    with h.company.db.transaction() as conn:
        assert usage(conn, h.spec.scientific_lineage.id) == 0
        receipt = conn.execute("SELECT * FROM research_program_reservations WHERE job_id=%s", (job,)).fetchone()
        assert receipt["settled_at"] and receipt["actual_seconds"] > 0 and not receipt["scientific_trial"]
        assert conn.execute("SELECT count(*) AS n FROM research_scientific_lineage_experiments").fetchone()["n"] == 1
    assert h.snapshot()["outcomes"][0]["payload"]["research_scope"] == h.spec.research_scope.model_dump(mode="json")


def test_cancelled_terminal_technical_failure_can_inherit_without_budget_reset(conditional):
    h = conditional
    prior, _ = conditional_task(h)
    trial, plan, job = reserved_trial(h)
    outcome = fixture_outcome(h, plan, technical=True)
    h.invoke("record_outcome", h.mission_id, trial, outcome)
    with h.company.db.transaction() as conn:
        conn.execute("UPDATE research_jobs SET state='failed' WHERE id=%s", (job,))
    successor_program(h, prior)
    with h.company.db.transaction() as conn:
        lineage = overview(conn, UUID(h.project["project_id"]), h.spec.scientific_lineage.id)
        assert lineage["completed_trials"] == lineage["charged_trials"] == 0
        assert lineage["technical_failures"] == 1
        assert lineage["trials"][0]["outcome_digest"] == fingerprint(outcome.model_dump(mode="json"))


def test_successor_cannot_omit_registered_prior_task_or_reassign_lineage(conditional):
    h = conditional
    prior_task, _ = conditional_task(h)
    trial, plan, _ = reserved_trial(h)
    finish_negative_fixture(h, trial, plan)
    with h.company.db.transaction() as conn:
        conn.execute("UPDATE research_missions SET state='cancelled' WHERE id=%s", (h.mission_id,))
    with pytest.raises(PolicyError, match="omitted"):
        with h.company.db.transaction() as conn:
            history(conn, UUID(h.project["project_id"]), h.spec.scientific_lineage.model_copy(update={"originating_task_refs": []}))
    with pytest.raises(PolicyError, match="cannot reset"):
        with h.company.db.transaction() as conn:
            task = conn.execute("SELECT digest,program_id FROM research_program_tasks WHERE id=%s", (prior_task,)).fetchone()
            history(conn, UUID(h.project["project_id"]), ScientificLineageAuthority(id=uuid4(), max_total_trials=4,
                history_digest="0" * 64, originating_task_refs=[{"program_id": task["program_id"], "task_id": prior_task,
                                                               "task_digest": task["digest"]}]))


def test_worker_wrapper_preserves_producer_bytes_and_consumer_scope(tmp_path):
    fixture = conditional_producer(tmp_path)
    trial = validate(fixture)
    assert trial.result.research_scope == trial.manifest.spec.research_scope
    assert trial.result.producer_sha256 == sha(trial.contents["producer-result.json"])
    assert trial.qualification.producer_sha256 == sha(trial.contents["producer-qualification.json"])
    assert json.loads(trial.contents["producer-result.json"])["schema_version"] == 1
    report = build_adaptive_report((trial,), audit=None, history=replace(history_for(trial), scientific_lineage=lineage_history(trial)))
    assert "조건부 사후 연구" in report["html"] and not report["summary"]["performance_visible"]
    assert report["summary"]["research_scope"]["result_scope"] == "conditional_retrospective_development"
    assert report["summary"]["point_in_time"] is False


def test_protected_engine_original_protocol_works_with_versioned_policy_factory(tmp_path, monkeypatch):
    case = prepare_conditional_case(tmp_path)
    monkeypatch.syspath_prepend(str(case.code / "labs/company-domestic-research"))
    for action in ("qualify", "evaluate"):
        output = tmp_path / action
        output.mkdir()
        run(action, case.code / case.manifest.config_path, case.path, output, input_root=case.inputs)
    qualification_path = tmp_path / "qualify/qualification.json"
    qualification = qualification_from_file(qualification_path, case.manifest, case.server.public_profile, producer=True)
    result = validate_result_files(tmp_path / "evaluate", case.manifest, producer=True)
    assert qualification.schema_version == result.schema_version == 1
    assert qualification.research_scope is result.research_scope is None
    assert case.receipt["qualified"] is case.receipt["activated"] is False
    assert bind_producer_record(qualification, qualification_path, case.manifest).research_scope == case.manifest.spec.research_scope
    assert bind_producer_record(result, tmp_path / "evaluate/result.json", case.manifest).research_scope == case.manifest.spec.research_scope


def test_candidate_cannot_declare_the_worker_scope(tmp_path):
    fixture = conditional_producer(tmp_path)
    manifest = fixture[2]
    value = AdaptiveResult.model_validate_json(fixture[4]["result.json"])
    with pytest.raises(ExecutionBlocked, match="cannot-declare"):
        bind_producer_record(value, tmp_path / "producer-result.json", manifest)


@pytest.mark.parametrize("name", ["receipt.json", "qualification.json", "result.json", "runtime.json",
                                  "sandbox-qualification.json", "sandbox-evaluation.json"])
def test_consumer_rejects_scoped_identity_loss_even_with_updated_file_hashes(tmp_path, name):
    fixture = conditional_producer(tmp_path)
    mutate(fixture, name, lambda value: value["research_scope"].update(scientific_lineage_id=str(uuid4())))
    with pytest.raises(ValidationError):
        validate(fixture)


def test_consumer_rejects_forged_original_with_updated_envelope_hash(tmp_path):
    fixture = conditional_producer(tmp_path)
    mutate(fixture, "producer-result.json", lambda value: value.update(code_commit="1" * 40))
    mutate(fixture, "result.json", lambda value: value.update(producer_sha256=sha(fixture[4]["producer-result.json"])))
    with pytest.raises(ValidationError, match="producer_scope_binding"):
        validate(fixture)


@pytest.mark.parametrize("change", [{"json_dates": ["2025-01-02"], "json_datetimes": ["2025-01-02T00:00:00Z"]},
    {"json_dates": ["2024-01-01"]}, {"sample_count": 2}, {"typed_schema": {"date": "string", "timestamp": "string"}}])
def test_consumer_rechecks_producer_warmup_dates_samples_and_schema(tmp_path, change):
    fixture = conditional_producer(tmp_path)
    mutate(fixture, "producer-qualification.json", lambda value: value.update(change))
    mutate(fixture, "qualification.json", lambda value: value.update(**change,
        producer_sha256=sha(fixture[4]["producer-qualification.json"])))
    with pytest.raises(ValidationError, match="qualification_"):
        validate(fixture)


def test_audit_report_publication_preserve_scope_and_prior_negative_history(tmp_path, qlab_profile):  # noqa: F811
    trial = validate(conditional_producer(tmp_path))
    binding = binding_for(trial)
    binding = type(binding).model_validate(binding.model_dump(mode="json") | scoped_kwargs(trial.manifest.spec))
    report_history = replace(history_for(trial), scientific_lineage=lineage_history(trial))
    package = prepare_audit_package(tmp_path / "package", (trial,), mission_spec=trial.manifest.spec,
                                    binding=binding, qlab_profile=qlab_profile, history=report_history)
    audit = write(package, binding, qlab_profile)
    verified = verify_audit_package(package.root, audit, expected=binding, qlab_profile=qlab_profile)
    report = build_adaptive_report((trial,), audit=verified, history=report_history)
    assert report["summary"]["research_scope"] == verified.public_receipt()["research_scope"]
    assert "prior_negative" in (package.root / "scope/history.json").read_text()
    assert report["summary"]["deployment_ready"] is False
    content = report["html"].encode()
    (package.root / "report.html").write_bytes(content)
    (package.root / "report.html").chmod(0o444)
    publication = audit_publication(verified, trial.manifest.trial_id, source_id="fixture:conditional-report",
        report=EvidenceRef(path="report.html", sha256=sha(content)), published_at=now())
    assert publication.research_scope == trial.manifest.spec.research_scope
    with pytest.raises(ValidationError, match="reported_history_not_audited"):
        build_adaptive_report((trial,), audit=verified, history=replace(report_history, scientific_lineage={
            **report_history.scientific_lineage, "completed_trials": 0}))
