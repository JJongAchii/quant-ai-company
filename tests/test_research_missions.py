"""Real PostgreSQL lifecycle; all numerical values are synthetic contract fixtures, not research."""

import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from quant_company.company import Company, PolicyError, fingerprint, now
from quant_company.research.mission_contracts import (
    AuditPublication,
    Challenge,
    HypothesisProposal,
    Interpretation,
    MissionSpec,
    TrialOutcome,
    TrialPlan,
)
from quant_company.research.missions import EXECUTION_PROFILE, MissionStore


def spec_payload():
    return {
        "title": "Synthetic contract exercise", "kind": "strategy",
        "objective": {"metric": "stress-net-absolute-cagr", "direction": "maximize", "unit": "fraction-per-year"},
        "base_cost_bps": 10, "stress_cost_bps": 30,
        "risk_constraints": [{"metric": "max_drawdown", "maximum": 0.3, "unit": "fractional-loss"}],
        "development": {"start": "2020-01-01", "end": "2020-12-31"},
        "sealed": [{"start": "2021-01-01", "end": "2021-12-31"}],
        "data": {"lake_id": "synthetic-contract-data", "input_files": {"fixture/rows.json": "a" * 64}},
        "code": {"repository": "quant-lab", "base_commit": "b" * 40, "write_paths": ["strategies/fixture"]},
        "allowed_changes": ["implementation", "features"], "execution_profile": EXECUTION_PROFILE,
        "execution_profile_digest": "e" * 64, "resources": {"worker_id": "worker", "priority": "owner"},
        "search": {"max_trials_per_cycle": 3, "patience": 2, "min_improvement": 0.001, "continuous": True},
        "baseline_source_ids": ["fixture:baseline"],
    }


@pytest.fixture
def harness(company):
    for role in ("engineer", "validator"):
        company.roles[role] = company.roles["data"].model_copy(update={"id": role, "name": role})
    with company.db.transaction() as conn:
        conn.execute(files("quant_company.research").joinpath("mission_schema.sql").read_text())
        # The root integration owns this replay-compatible migration in db.py.
        conn.execute("ALTER TABLE research_jobs DROP CONSTRAINT IF EXISTS research_jobs_approval_event_id_key")
        conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS replay_approval_event ON research_jobs(approval_event_id)
            WHERE recipe_id='kr-etf-p11-replay-v1'""")
        conn.execute("""INSERT INTO sources(id,title,uri,content,available_at,approved,synthetic)
            VALUES ('fixture:baseline','Synthetic baseline','fixture:baseline','No market results',%s,true,true)""",
                     (now() - timedelta(days=1),))
    return Harness(company)


class Harness:
    def __init__(self, company):
        self.company = company
        self.store = MissionStore(company)
        self.project = company.ingest(event_key="fixture:project", text="Synthetic mission",
                                      owner="UHUMAN", channel="CQUANT", thread_ts="123.0")
        self.mission_id = None

    def invoke(self, method, *args, **kwargs):
        with self.company.db.transaction() as conn:
            return getattr(self.store, method)(conn, *args, **kwargs)

    def snapshot(self, public=False):
        return self.invoke("snapshot", self.mission_id, public=public)

    def mission(self, *, spec=None, approve=True):
        self.spec = MissionSpec.model_validate(spec or spec_payload())
        row = self.invoke("create", self.project["project_id"], "UHUMAN", 1, self.spec)
        self.mission_id = row["id"]
        self.digest = row["manifest_digest"]
        if approve:
            self.approval = self.owner_event("approve")
            self.invoke("approve", self.mission_id, **self.owner_args(self.approval))
        return self.mission_id

    def owner_args(self, event):
        return {"owner": "UHUMAN", "revision": 1, "manifest_digest": self.digest, "event_key": event}

    def owner_event(self, action, *, bound=True, target=None):
        event = "fixture:authenticated:" + str(uuid4())
        task = self.company.ingest(event_key=event, text=f"Synthetic owner {action}", owner="UHUMAN",
                                   project_id=self.project["project_id"], status_only=True)
        if bound:
            with self.company.db.transaction() as conn:
                self.company._event(conn, "research_approval_authorized", {
                    "schema_version": 1, "owner_event_id": event, "task_id": task["task_id"], "action": action,
                    "target": target or {"kind": "mission", "target_id": self.mission_id, "revision": 1,
                                         "manifest_digest": self.digest},
                    "provenance": {"owner": "UHUMAN", "channel": "CQUANT", "thread_ts": "123.0",
                                   "origin": "synthetic_authenticated_test_ingress"},
                }, self.project["project_id"])
        return event

    def proposal(self, **changes):
        previous = [trial["id"] for trial in self.snapshot()["trials"] if trial["result_id"]]
        return HypothesisProposal.model_validate({
            "id": str(uuid4()), "author": "researcher_kr", "hypothesis": "Fixture adjustment",
            "expected_effect": "Check a typed boundary", "falsification": "Consumer rejects a mismatched field",
            "comparison": "Previous fixture", "change_axes": ["implementation"],
            "source_ids": ["fixture:baseline"], "predecessor_trial_ids": previous[-1:], **changes,
        })

    def add(self, **changes):
        value = self.proposal(**changes)
        self.invoke("add_proposal", self.mission_id, value, actor=value.author)
        return value

    def challenge(self, proposal, **changes):
        value = Challenge.model_validate({"id": str(uuid4()), "proposal_id": str(proposal.id),
                                          "reviewer": "financial_strategist", "concern": "Check lineage",
                                          "test": "Verify producer and consumer identity",
                                          "source_ids": ["fixture:baseline"], **changes})
        self.invoke("add_challenge", self.mission_id, value, actor=value.reviewer)
        return value

    def select(self):
        proposal = self.add()
        challenge = self.challenge(proposal)
        trial_id = str(uuid4())
        self.invoke("select", self.mission_id, proposal.id, actor="director", challenge_ids=[challenge.id],
                    rationale="Synthetic comparison resolves the recorded critique", trial_id=trial_id)
        return trial_id, proposal

    def plan(self, trial_id, proposal):
        return TrialPlan.model_validate({
            "trial_id": trial_id, "proposal_id": str(proposal.id), "mission_digest": self.digest,
            "execution_profile": EXECUTION_PROFILE, "implementer": "engineer", "repository": "quant-lab",
            "code_commit": "c" * 40, "changed_paths": ["strategies/fixture/model.py"],
            "config_files": {"strategies/fixture/config.json": "d" * 64},
            "input_files": self.spec.data.input_files, "lake_id": self.spec.data.lake_id,
            "development": self.spec.development.model_dump(mode="json"), "worker_id": "worker",
            "hostname": "DESKTOP-5T00NAF", "gpu": "NVIDIA GeForce RTX 3070",
        })

    def enqueue(self, trial_id, plan):
        job_id = str(uuid4())
        payload = {"mission_id": self.mission_id, "mission_digest": self.digest,
                   "trial_id": trial_id, "plan_digest": fingerprint(plan.model_dump(mode="json"))}
        with self.company.db.transaction() as conn:
            conn.execute("""INSERT INTO research_jobs(id,project_id,task_id,revision,recipe_id,manifest,
                manifest_digest,company_commit,state,approval_event_id,approved_by) VALUES
                (%s,%s,%s,1,%s,%s,%s,%s,'queued',%s,'UHUMAN')""",
                         (job_id, self.project["project_id"], self.project["task_id"], EXECUTION_PROFILE,
                          Jsonb(payload), fingerprint([payload, job_id]), "f" * 40, self.approval))
        self.invoke("attach_job", self.mission_id, trial_id, job_id)
        return job_id

    def prepare(self):
        trial_id, proposal = self.select()
        plan = self.plan(trial_id, proposal)
        self.invoke("set_plan", self.mission_id, trial_id, plan, actor="engineer")
        self.enqueue(trial_id, plan)
        return trial_id, plan

    def outcome(self, plan, *, score=0.125, risk=0.2, technical=False):
        payload = {"id": str(uuid4()), "plan": plan.model_dump(mode="json"),
                   "status": "technical_failure" if technical else "result",
                   "started_at": now() - timedelta(minutes=2), "finished_at": now() - timedelta(seconds=2),
                   "evidence_files": {"receipt.json": "1" * 64}}
        if technical:
            payload.update(failure_code="qualification_failed", resume_condition="Verify the repaired dependency")
        else:
            payload.update(evidence_files={"qualification.json": "2" * 64, "result.json": "3" * 64},
                           qualification={"path": "qualification.json", "sha256": "2" * 64},
                           result={"path": "result.json", "sha256": "3" * 64},
                           metrics={"primary": {**self.spec.objective.model_dump(), "value": score},
                                    "risks": {"max_drawdown": risk}, "sample_count": 12,
                                    "sample_window": plan.development.model_dump(mode="json")})
        return TrialOutcome.model_validate(payload)

    def interpret(self, trial_id, outcome):
        value = Interpretation(trial_id=trial_id, author="researcher_kr",
                               outcome_digest=fingerprint(outcome.model_dump(mode="json")),
                               conclusion="Synthetic score 12.5% is not public before verification",
                               next_hypothesis="Synthetic candidate scored 0.125; check another field",
                               source_ids=["fixture:baseline"])
        self.invoke("interpret", self.mission_id, trial_id, value, actor="researcher_kr")
        return value

    def publication(self, trial_id, outcome):
        return AuditPublication(trial_id=trial_id, mission_digest=self.digest,
                                outcome_digest=fingerprint(outcome.model_dump(mode="json")),
                                source_id="fixture:baseline", audit_files={"verification.json": "4" * 64},
                                verification={"path": "verification.json", "sha256": "4" * 64},
                                report={"path": "report.html", "sha256": "5" * 64},
                                verifier_role="validator", verifier_commit="6" * 40,
                                published_at=now() - timedelta(seconds=1))

    def finish(self, *, score=0.125, risk=0.2):
        trial_id, plan = self.prepare()
        outcome = self.outcome(plan, score=score, risk=risk)
        self.invoke("record_outcome", self.mission_id, trial_id, outcome)
        self.interpret(trial_id, outcome)
        publication = self.publication(trial_id, outcome)
        self.invoke("checkpoint", self.mission_id, trial_id, verify=lambda: publication)
        return trial_id, outcome


def test_owner_binding_duplicate_and_immutable_spec(harness):
    h = harness
    h.mission(approve=False)
    assert h.snapshot()["state"] == "draft"
    unbound = h.owner_event("approve", bound=False)
    with pytest.raises(PolicyError, match="bound authenticated"):
        h.invoke("approve", h.mission_id, **h.owner_args(unbound))
    event = h.owner_event("approve")
    for overrides in ({"owner": "UOUTSIDE"}, {"revision": 2}, {"manifest_digest": "0" * 64}, {"event_key": "invented"}):
        with pytest.raises(PolicyError):
            h.invoke("approve", h.mission_id, **(h.owner_args(event) | overrides))
    h.invoke("approve", h.mission_id, **h.owner_args(event))
    h.invoke("approve", h.mission_id, **h.owner_args(event))
    assert h.snapshot()["spec"] == h.spec.model_dump(mode="json")
    changed = h.spec.model_dump(mode="json") | {"stress_cost_bps": 31}
    with pytest.raises(PolicyError, match="different content"):
        h.invoke("create", h.project["project_id"], "UHUMAN", 1, changed, mission_id=h.mission_id)
    with h.company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM research_mission_controls").fetchone()["n"] == 1


@pytest.mark.parametrize("kind,profile", [("model", EXECUTION_PROFILE), ("claim", EXECUTION_PROFILE), ("strategy", "unregistered")])
def test_unsupported_execution_remains_draft(harness, kind, profile):
    h = harness
    h.mission(spec=spec_payload() | {"kind": kind, "execution_profile": profile}, approve=False)
    with pytest.raises(PolicyError, match="draft-only"):
        h.invoke("approve", h.mission_id, **h.owner_args(h.owner_event("approve")))
    assert h.snapshot()["state"] == "draft"


def test_revision_change_and_pause_stop_new_work_without_deleting_history(harness):
    h = harness
    h.mission()
    proposal = h.add()
    pause = h.owner_event("pause")
    h.invoke("pause", h.mission_id, **h.owner_args(pause), reason="Owner priority")
    with pytest.raises(PolicyError, match="active and approved"):
        h.challenge(proposal)
    h.invoke("resume", h.mission_id, **h.owner_args(h.owner_event("resume")), reason="Resume approved scope")
    h.challenge(proposal)
    with h.company.db.transaction() as conn:
        conn.execute("UPDATE projects SET revision=2 WHERE id=%s", (h.project["project_id"],))
    assert h.invoke("next_stage", h.mission_id)["stage"] == "stale_revision"
    with pytest.raises(PolicyError, match="current active"):
        h.add()
    assert len(h.snapshot()["proposals"]) == 1


def test_proposal_evidence_authorization_lineage_scope_and_duplicates(harness):
    h = harness
    h.mission()
    for changes in ({"source_ids": ["unknown"]}, {"predecessor_trial_ids": [str(uuid4())]}, {"change_axes": ["portfolio"]}):
        with pytest.raises(PolicyError):
            h.add(**changes)
    with h.company.db.transaction() as conn:
        conn.execute("""INSERT INTO sources(id,title,uri,content,available_at,approved)
            VALUES ('future','future','fixture:future','future',now()+interval '1 day',true)""")
    with pytest.raises(PolicyError, match="Unavailable"):
        h.add(source_ids=["future"])
    proposal = h.add()
    assert h.invoke("add_proposal", h.mission_id, proposal, actor=proposal.author)["id"] == str(proposal.id)
    with pytest.raises(PolicyError, match="different content"):
        h.invoke("add_proposal", h.mission_id, proposal.model_copy(update={"hypothesis": "Changed"}), actor=proposal.author)
    with pytest.raises(PolicyError, match="different role"):
        h.challenge(proposal, reviewer=proposal.author)


def test_challenge_can_cause_a_revised_hypothesis_before_a_trial(harness):
    h = harness
    h.mission()
    proposal = h.add()
    challenge = h.challenge(proposal)
    args = {"actor": "director", "challenge_ids": [challenge.id], "rationale": "Resolve the concrete challenge first"}
    h.invoke("reject_proposal", h.mission_id, proposal.id, **args)
    h.invoke("reject_proposal", h.mission_id, proposal.id, **args)
    assert h.invoke("next_stage", h.mission_id)["stage"] == "proposal"
    with pytest.raises(PolicyError, match="Rejected"):
        h.invoke("select", h.mission_id, proposal.id, **args, trial_id=str(uuid4()))
    with pytest.raises(PolicyError, match="Superseded"):
        h.add(supersedes_proposal_id=str(uuid4()))
    revised = h.add(supersedes_proposal_id=proposal.id, hypothesis="Revision grounded in the recorded critique")
    h.challenge(revised)
    state = h.snapshot()
    assert state["cumulative_trials"] == 0 and len(state["rejections"]) == 1
    assert state["stage"]["proposal_id"] == str(revised.id)


def test_selection_requires_independent_current_evidence_and_single_flight(harness):
    h = harness
    h.mission()
    proposal = h.add()
    with pytest.raises(PolicyError, match="independent challenges"):
        h.invoke("select", h.mission_id, proposal.id, actor="director", challenge_ids=[], rationale="No critic", trial_id=str(uuid4()))
    challenge = h.challenge(proposal)
    ids = [str(uuid4()), str(uuid4())]

    def select(identity):
        try:
            return h.invoke("select", h.mission_id, proposal.id, actor="director", challenge_ids=[challenge.id],
                            rationale="A bounded fixture", trial_id=identity)["id"]
        except PolicyError:
            return None

    with ThreadPoolExecutor(2) as pool:
        answers = list(pool.map(select, ids))
    assert len([value for value in answers if value]) == 1
    assert len(h.snapshot()["trials"]) == 1


def test_plan_scope_identity_and_immutable_running_job(harness):
    h = harness
    h.mission()
    trial_id, proposal = h.select()
    plan = h.plan(trial_id, proposal)
    for changes in ({"changed_paths": ["core/src/qlab/ledger.py"]}, {"input_files": {"sealed.json": "a" * 64}},
                    {"mission_digest": "0" * 64}, {"config_files": {"docs/objective.md": "b" * 64}}):
        with pytest.raises(PolicyError):
            h.invoke("set_plan", h.mission_id, trial_id, plan.model_copy(update=changes), actor="engineer")
    h.invoke("set_plan", h.mission_id, trial_id, plan, actor="engineer")
    job = h.enqueue(trial_id, plan)
    h.invoke("attach_job", h.mission_id, trial_id, job)
    h.invoke("mark_running", h.mission_id, trial_id, job)
    with pytest.raises(PolicyError, match="immutable"):
        h.invoke("set_plan", h.mission_id, trial_id, plan.model_copy(update={"code_commit": "9" * 40}), actor="engineer")


def test_technical_failure_and_repair_do_not_consume_scientific_budget(harness):
    h = harness
    h.mission()
    trial_id, plan = h.prepare()
    failure = h.outcome(plan, technical=True)
    h.invoke("record_outcome", h.mission_id, trial_id, failure)
    h.invoke("record_outcome", h.mission_id, trial_id, failure)
    assert h.snapshot()["cumulative_trials"] == 0
    assert h.invoke("next_stage", h.mission_id)["stage"] == "repair"
    with pytest.raises(PolicyError, match="repair evidence"):
        h.invoke("set_plan", h.mission_id, trial_id, plan, actor="engineer")
    repaired = plan.model_copy(update={"code_commit": "9" * 40})
    h.invoke("set_plan", h.mission_id, trial_id, repaired, actor="engineer",
             repair_source_ids=["fixture:baseline"], repair_rationale="Repaired the fixture dependency")
    h.enqueue(trial_id, repaired)
    result = h.outcome(repaired)
    h.invoke("record_outcome", h.mission_id, trial_id, result)
    h.invoke("record_outcome", h.mission_id, trial_id, result)
    state = h.snapshot()
    assert state["cumulative_trials"] == state["cycle_trials"] == 1
    assert len(state["attempts"]) == len(state["outcomes"]) == 2
    assert state["attempts"][0]["plan"]["code_commit"] == "c" * 40
    with pytest.raises(PolicyError, match="different content"):
        h.invoke("record_outcome", h.mission_id, trial_id,
                 result.model_copy(update={"finished_at": result.finished_at - timedelta(seconds=1)}))


def test_unverified_prose_and_metrics_are_hidden_and_fake_audit_claims_fail(harness):
    h = harness
    h.mission()
    trial_id, plan = h.prepare()
    outcome = h.outcome(plan)
    h.invoke("record_outcome", h.mission_id, trial_id, outcome)
    h.interpret(trial_id, outcome)
    public = json.dumps(h.snapshot(public=True))
    assert "0.125" not in public and "12.5%" not in public and "conclusion" not in public
    assert "metrics" not in public and h.snapshot(public=True)["incumbent_trial_id"] is None
    for claim in (True, {"passed": True, "verdict": "pass"}):
        with pytest.raises(PolicyError, match="not a pass claim"):
            h.invoke("checkpoint", h.mission_id, trial_id, verify=lambda claim=claim: claim)
    publication = h.publication(trial_id, outcome)
    with pytest.raises(PolicyError, match="exact independent"):
        h.invoke("checkpoint", h.mission_id, trial_id,
                 verify=lambda: publication.model_copy(update={"outcome_digest": "0" * 64}))
    h.invoke("checkpoint", h.mission_id, trial_id, verify=lambda: publication)
    assert h.snapshot(public=True)["trials"][0]["performance_visible"]


def test_feedback_best_preservation_and_continuous_cycles_keep_cumulative_history(harness):
    h = harness
    h.mission()
    first, _ = h.finish(score=0.2)
    with pytest.raises(PolicyError, match="latest completed"):
        h.add(predecessor_trial_ids=[])
    second, _ = h.finish(score=0.2)  # The earlier equal incumbent remains selected.
    third, _ = h.finish(score=0.1)
    state = h.snapshot()
    assert state["incumbent_trial_id"] == first
    assert state["cumulative_trials"] == state["cycle_trials"] == 3
    assert state["stage"]["stage"] == "cycle_review"
    with pytest.raises(PolicyError, match="renewal"):
        proposal = h.add()
        challenge = h.challenge(proposal)
        h.invoke("select", h.mission_id, proposal.id, actor="director", challenge_ids=[challenge.id],
                 rationale="Beyond cycle", trial_id=str(uuid4()))
    args = {"cycle": 1, "actor": "director", "rationale": "Try a distinct evidence-grounded next direction",
            "source_ids": ["fixture:baseline"], "predecessor_trial_ids": [first, second, third]}
    h.invoke("advance_cycle", h.mission_id, **args)
    h.invoke("advance_cycle", h.mission_id, **args)
    state = h.snapshot()
    assert state["cycle"] == 2 and state["cycle_trials"] == 0 and state["cumulative_trials"] == 3
    assert state["incumbent_trial_id"] == first and len(state["outcomes"]) == 3
    fourth, _ = h.finish(score=0.9, risk=0.4)  # Infeasible fixture cannot displace the feasible incumbent.
    assert fourth != first and h.snapshot()["incumbent_trial_id"] == first
    assert h.snapshot()["cumulative_trials"] == 4


@pytest.mark.parametrize("mutation", [
    lambda p: p["objective"].update(metric="sharpe"),
    lambda p: p["objective"].update(direction="minimize"),
    lambda p: p["objective"].update(unit="percent"),
    lambda p: p["search"].update(min_improvement=float("nan")),
    lambda p: p["development"].update(start="2020-01-01T00:00:00Z"),
    lambda p: p["sealed"].append({"start": "2020-01-02", "end": "2020-02-01"}),
    lambda p: p["code"].update(write_paths=["../outside"]),
    lambda p: p["resources"].update(worker_id="worker5090"),
])
def test_contract_rejects_unsupported_metrics_nonfinite_dates_paths_and_workers(mutation):
    payload = spec_payload()
    mutation(payload)
    with pytest.raises(ValidationError):
        MissionSpec.model_validate(payload)


def test_qualification_captured_ingress_to_typed_artifact_postgres_restart_consumer(harness, tmp_path):
    """Captured real operational rows; zero-valued metric is explicitly synthetic, never finance evidence."""
    h = harness
    fixture_path = Path("docs/project/evidence/research-activation-20260921/owner-slack-message.json")
    raw = fixture_path.read_bytes()
    captured = json.loads(raw)
    assert captured and all(row["user"] and row["ts"] and row["thread_ts"] for row in captured)
    observed_dates = [datetime.fromtimestamp(float(row["ts"]), UTC).date() for row in captured]
    payload = spec_payload()
    payload.update(development={"start": min(observed_dates), "end": max(observed_dates)}, sealed=[])
    payload["data"] = {"lake_id": "captured-operational-fixture-not-market-data",
                       "input_files": {str(fixture_path): hashlib.sha256(raw).hexdigest()}}
    h.mission(spec=payload)
    trial_id, proposal = h.select()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    branch = subprocess.check_output(["git", "branch", "--show-current"], text=True).strip()
    assert subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "src/quant_company/research/mission_contracts.py",
                           "src/quant_company/research/missions.py", "src/quant_company/research/mission_schema.sql",
                           "tests/test_research_missions.py"]).returncode == 0, "Qualification requires committed code"
    plan = h.plan(trial_id, proposal).model_copy(update={"code_commit": head})
    h.invoke("set_plan", h.mission_id, trial_id, plan, actor="engineer")
    h.enqueue(trial_id, plan)
    producer = {"fixture_kind": "captured-operational-rows-with-synthetic-metric", "rows": captured,
                "sample_count": len(captured), "window": h.spec.development.model_dump(mode="json")}
    result_bytes = json.dumps(producer, sort_keys=True).encode()
    qualify_bytes = json.dumps({"fixture_sha256": hashlib.sha256(raw).hexdigest(), "exact_commit": head,
                                "schema_version": 1, "sample_count": len(captured)}).encode()
    (tmp_path / "result.json").write_bytes(result_bytes)
    (tmp_path / "qualification.json").write_bytes(qualify_bytes)
    evidence = {"result.json": hashlib.sha256(result_bytes).hexdigest(),
                "qualification.json": hashlib.sha256(qualify_bytes).hexdigest()}
    producer_outcome = h.outcome(plan, score=0).model_dump(mode="json")
    producer_outcome.update(evidence_files=evidence,
                            result={"path": "result.json", "sha256": evidence["result.json"]},
                            qualification={"path": "qualification.json", "sha256": evidence["qualification.json"]})
    producer_outcome["metrics"]["sample_count"] = len(captured)
    outcome = TrialOutcome.model_validate_json(json.dumps(producer_outcome))
    h.invoke("record_outcome", h.mission_id, trial_id, outcome)
    h.store = MissionStore(Company(h.company.settings, h.company.roles))
    consumer = TrialOutcome.model_validate(h.snapshot()["outcomes"][0]["payload"])
    assert consumer.model_dump(mode="json") == outcome.model_dump(mode="json")
    assert isinstance(consumer.metrics.sample_window.start, date)
    assert consumer.finished_at.utcoffset() == timedelta(0)
    assert consumer.plan.input_files[str(fixture_path)] == hashlib.sha256(raw).hexdigest()
    for bad in (0, -1):
        invalid = json.loads(json.dumps(producer_outcome))
        invalid["metrics"]["sample_count"] = bad
        with pytest.raises(ValidationError):
            TrialOutcome.model_validate(invalid)
    for bad in (float("nan"), float("inf")):
        invalid = json.loads(json.dumps(producer_outcome))
        invalid["metrics"]["primary"]["value"] = bad
        with pytest.raises(ValidationError):
            TrialOutcome.model_validate(invalid)
    invalid = json.loads(json.dumps(producer_outcome))
    invalid["metrics"]["primary"]["unit"] = "percent"
    with pytest.raises(ValidationError):
        TrialOutcome.model_validate(invalid)
    invalid = json.loads(json.dumps(producer_outcome))
    invalid["result_artifact"] = invalid.pop("result")
    with pytest.raises(ValidationError):
        TrialOutcome.model_validate(invalid)
    assert not h.snapshot(public=True)["trials"][0]["performance_visible"]
    with h.company.db.transaction() as conn:
        pg_version = conn.execute("SELECT version() AS version").fetchone()["version"]
    receipt = {"schema_version": 1, "test": "mission-store-producer-consumer-qualification", "exact_commit": head,
               "branch": branch, "database": "real isolated disposable PostgreSQL", "database_version": pg_version,
               "fixture_path": str(fixture_path), "fixture_sha256": hashlib.sha256(raw).hexdigest(),
               "fixture_label": producer["fixture_kind"], "captured_row_count": len(captured),
               "producer_consumer_roundtrip": True, "date_datetime_json_contract": True,
               "empty_nonfinite_wrong_unit_wrong_field_rejected": True, "public_result_withheld": True,
               "scientific_performance_measured": False, "producer_evidence_files": evidence,
               "outcome_digest": fingerprint(consumer.model_dump(mode="json"))}
    Path(".local/mission-store-qualification.json").write_text(json.dumps(receipt, indent=2) + "\n")
