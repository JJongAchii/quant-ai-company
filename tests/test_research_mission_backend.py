"""Real PG/Git/files/qlab; model responses and zero-return artifacts are labelled synthetic."""

import copy
import csv
import io
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest

from quant_company.company import Company, PolicyError, now
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.research.adaptive_contracts import (
    AdaptiveExecutionReceipt,
    AdaptiveManifest,
    AdaptiveQualification,
    AdaptiveResult,
    digest_model,
)
from quant_company.research.builds import ServerResearchProfile
from quant_company.research.contracts import WorkerUpdate
from quant_company.research.controller import MissionController
from quant_company.research.mission_backend import MissionBackend
from quant_company.research.mission_contracts import MissionSpec
from quant_company.research.missions import EXECUTION_PROFILE
from quant_company.research.store import ResearchStore
from quant_company.research.worker import sha_file

from .test_research_adaptive_report import encoded, producer, sha, write_archive
from .test_research_audit import qlab_profile  # noqa: F401 -- actual pinned qlab fixture
from .test_research_missions import Harness


@pytest.fixture
def backend_fixture(company, tmp_path, qlab_profile):  # noqa: F811
    company.settings.company_research_enabled = True
    company.settings.company_autonomous_research_enabled = True
    company.settings.research_artifact_dir = (tmp_path / "artifacts").resolve()
    company.settings.research_artifact_dir.mkdir()
    company.settings.company_code_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    for role in ("engineer", "validator"):
        company.roles[role] = company.roles["data"].model_copy(update={"id": role, "name": role, "active": False})
    with company.db.transaction() as conn:
        conn.execute("""INSERT INTO sources(id,title,uri,content,available_at,approved,synthetic)
            VALUES ('fixture:baseline','Synthetic baseline','fixture:baseline','Synthetic protocol evidence',%s,true,true)""",
                     (now() - timedelta(days=1),))
    fixture_root = tmp_path / "producer"
    fixture_root.mkdir()
    original = producer(fixture_root)
    _, _, manifest, public, _ = original
    public = public.model_copy(update={"id": EXECUTION_PROFILE})
    spec = MissionSpec.model_validate(manifest.spec.model_dump(mode="json") | {
        "execution_profile": EXECUTION_PROFILE, "execution_profile_digest": digest_model(public),
    })
    bundle = fixture_root / "base.bundle"
    subprocess.run(["git", "-C", str(fixture_root / "repo"), "bundle", "create", str(bundle), "HEAD"], check=True,
                   capture_output=True)
    profile = ServerResearchProfile(public_profile=public, source_bundle=bundle.resolve(), source_bundle_sha256=sha_file(bundle),
        base_commit=spec.code.base_commit, config_path="config.json", config_files=["config.json"],
        allowed_write_paths=spec.code.write_paths)
    profiles = company.settings.research_artifact_dir / "profiles.json"
    profiles.write_text(json.dumps({EXECUTION_PROFILE: profile.model_dump(mode="json")}))
    company.settings.research_profiles_file = profiles
    qlab_path = company.settings.research_artifact_dir / "qlab.json"
    qlab_path.write_text(json.dumps({"root": str(qlab_profile.root), "commit": qlab_profile.commit,
                                    "python_executable": str(qlab_profile.python_executable)}))
    company.settings.research_qlab_profile_file = qlab_path
    h = BackendHarness(company, original, public, qlab_profile)
    h.mission(spec=spec.model_dump(mode="json"))
    # The authenticated setup messages have already been handled by the fixture.
    # They must not remain pending owner work ahead of the research stage.
    with company.db.transaction() as conn:
        conn.execute("UPDATE turns SET status='stale'")
        conn.execute("UPDATE tasks SET status='completed'")
    return h


class BackendHarness(Harness):
    def __init__(self, company, original, profile, qlab):
        super().__init__(company)
        self.backend = MissionBackend(company)
        self.controller = MissionController(company, backend=self.backend)
        self.original, self.public, self.qlab = original, profile, qlab

    def stage_row(self):
        with self.company.db.transaction() as conn:
            return conn.execute("SELECT * FROM research_mission_stages WHERE mission_id=%s ORDER BY created_at DESC LIMIT 1",
                                (self.mission_id,)).fetchone()

    def schedule(self):
        snapshot = self.snapshot()
        with self.company.db.transaction() as conn:
            old = conn.execute("SELECT * FROM research_mission_stages WHERE mission_id=%s AND stage_key=%s",
                               (self.mission_id, self.controller._key(snapshot))).fetchone()
        context = self.backend.context(snapshot, old)
        with self.company.db.transaction() as conn:
            project = self.company._project(conn, self.project["project_id"])
            self.controller._schedule(conn, project, snapshot, context)
        return self.stage_row(), snapshot

    def respond(self, row, payload=None, *, read_path=None, offset=0):
        with self.company.db.transaction() as conn:
            turn = conn.execute("SELECT id FROM turns WHERE task_id=%s AND status='queued' ORDER BY sequence LIMIT 1",
                                (row["task_id"],)).fetchone()
        identity = str(turn["id"])
        assert self.company.prepare_turn(identity)["state"] == "ready"
        if read_path:
            decision = AgentDecision(say="", status="continue", tools=[{"name": "research_control", "arguments": {
                "action": "read_stage_file", "path": read_path, "offset": offset}}])
        else:
            decision = AgentDecision(say="", status="complete", artifacts=[{"title": "Synthetic internal stage",
                                      "content": json.dumps(payload), "source_ids": []}])
        self.company.commit_turn(identity, ProviderResponse(request_id=identity, provider="fixture", decision=decision))
        return self.stage_row()

    def build(self, *, replacement="SYNTHETIC_FIXTURE = False\n", repair=False):
        if not repair:
            self.select()
        row, snapshot = self.schedule()
        response = {"patches": [{"path": "candidate.py", "expected_text": "SYNTHETIC_FIXTURE = True\n",
                                 "replacement_text": replacement}], "rationale": "Synthetic scoped protocol change"}
        if repair:
            response["repair_source_ids"] = ["fixture:baseline"]
        row = self.respond(row, response)
        self.backend.apply_stage(row, snapshot)
        return self.backend.enqueue(self.snapshot())

    def job(self, identity=None):
        with self.company.db.transaction() as conn:
            if identity:
                return conn.execute("SELECT * FROM research_jobs WHERE id=%s", (identity,)).fetchone()
            return conn.execute("SELECT * FROM research_jobs WHERE mission_id=%s ORDER BY created_at DESC LIMIT 1",
                                (self.mission_id,)).fetchone()

    def deliver(self, *, invalid=False):
        assignment = ResearchStore(self.company).poll()["assignment"]
        manifest = AdaptiveManifest.model_validate(assignment["manifest"])
        contents = copy.deepcopy(self.original[4])
        code_root = Path(self.stage_row()["result"]["build_receipt"]["bundle_path"]).parent / "code"
        for name in manifest.code_files:
            contents["code/" + name] = (code_root / name).read_bytes()
        qualification = json.loads(contents["qualification.json"])
        qualification.update(trial_id=str(manifest.trial_id), plan_digest=manifest.plan_digest,
                             code_commit=manifest.plan.code_commit, config_files=manifest.plan.config_files,
                             input_files={name: manifest.plan.input_files[name] for name in self.public.qualification_input_names})
        qualification = AdaptiveQualification.model_validate(qualification)
        result = json.loads(contents["result.json"])
        outputs = {}
        for cost in ("base", "stress"):
            original_rows = list(csv.DictReader(io.StringIO(contents[f"outputs/{cost}.csv"].decode())))
            stream = io.StringIO(newline="")
            writer = csv.writer(stream)
            writer.writerow(["date", "net_return"])
            writer.writerows((row["date"], 0) for row in original_rows)
            outputs[cost + ".csv"] = stream.getvalue().encode()
            result[cost + "_returns"]["sha256"] = sha(outputs[cost + ".csv"])
        outputs["details.json"] = encoded({"synthetic_fixture": True})
        result.update(trial_id=str(manifest.trial_id), plan_digest=manifest.plan_digest,
                      code_commit=manifest.plan.code_commit, output_files={name: sha(data) for name, data in outputs.items()})
        result["metrics"]["primary"]["value"] = 0.0
        result["metrics"]["risks"]["max_drawdown"] = 0.0
        result = AdaptiveResult.model_validate(result)
        runtime = json.loads(contents["runtime.json"])
        runtime.update(execution_profile_digest=digest_model(self.public), code_commit=manifest.plan.code_commit,
                       company_commit=manifest.company_commit)
        contents.update({"manifest.json": manifest.model_dump_json().encode(), "profile.json": self.public.model_dump_json().encode(),
                         "qualification.json": qualification.model_dump_json().encode(), "result.json": result.model_dump_json().encode(),
                         "runtime.json": encoded(runtime), **{"outputs/" + name: data for name, data in outputs.items()}})
        for phase in ("qualification", "evaluation"):
            value = json.loads(contents[f"sandbox-{phase}.json"])
            value.update(code_commit=manifest.plan.code_commit, profile_id=self.public.id,
                         execution_profile_digest=digest_model(self.public), manifest_digest=digest_model(manifest))
            contents[f"sandbox-{phase}.json"] = encoded(value)
        receipt = json.loads(contents["receipt.json"])
        receipt.update(job_id=assignment["job_id"], project_id=assignment["project_id"], revision=assignment["revision"],
                       approval_event_id=assignment["approval_event_id"], mission_id=str(manifest.mission_id),
                       mission_digest=manifest.mission_digest, trial_id=str(manifest.trial_id), plan_digest=manifest.plan_digest,
                       manifest_digest=digest_model(manifest), execution_profile_digest=digest_model(self.public),
                       code_commit=manifest.plan.code_commit, company_commit=manifest.company_commit,
                       config_files=manifest.plan.config_files, output_files=result.output_files,
                       qualification_sha256=sha(contents["qualification.json"]), result_sha256=sha(contents["result.json"]),
                       runtime_sha256=sha(contents["runtime.json"]),
                       sandbox_qualification_sha256=sha(contents["sandbox-qualification.json"]),
                       sandbox_evaluation_sha256=sha(contents["sandbox-evaluation.json"]))
        contents["receipt.json"] = AdaptiveExecutionReceipt.model_validate(receipt).model_dump_json().encode()
        if invalid:
            contents["outputs/stress.csv"] += b"2024-04-01,NaN\n"
        path = self.company.settings.research_artifact_dir / assignment["job_id"] / "artifact.zip"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_archive(path, contents)
        ResearchStore(self.company).artifact_received(assignment["job_id"], assignment["lease_token"], path, sha_file(path))
        return path

    def interpret_current(self):
        state = self.snapshot()
        outcome = state["outcomes"][-1]
        with self.company.db.transaction() as conn:
            self.store.interpret(conn, self.mission_id, outcome["trial_id"], {
                "trial_id": outcome["trial_id"], "author": "researcher_kr", "outcome_digest": outcome["digest"],
                "conclusion": "Synthetic zero-return fixture only", "next_hypothesis": "Try a different scoped fixture",
                "source_ids": ["fixture:baseline"]}, actor="researcher_kr")

    def audit_response(self, *, read=True, verdict="pass"):
        row, snapshot = self.schedule()
        if read:
            for name, item in row["context"]["_audit"]["required_reads"].items():
                for offset in range(0, max(1, item["characters"]), 12000):
                    row = self.respond(row, read_path=name, offset=offset)
        value = row["context"]["audit"]
        frontmatter = {key: value[key] for key in ("judge", "target", "issued", "scope", "scope_digest", "objective_digest")}
        frontmatter.update(verdict=verdict, findings=[] if verdict == "pass" else [
            {"severity": "minor", "location": "fixture.py:1", "claim": "Synthetic unresolved fixture condition"}])
        markdown = "---\n" + json.dumps(frontmatter, indent=2) + "\n---\n# Synthetic fixture\nNo research judgment.\n"
        return self.respond(row, {"markdown": markdown}), snapshot


def test_context_has_only_managed_hash_bound_files_and_compact_history(backend_fixture):
    h = backend_fixture
    h.select()
    snapshot = h.snapshot()
    context = h.backend.context(snapshot, None)
    assert context["available_files"] and "code/candidate.py" in context["_private_files"]
    public = json.dumps({key: value for key, value in context.items() if not key.startswith("_")})
    assert len(public) < 35000 and str(h.backend.root) not in public
    for entry in context["available_files"]:
        path = Path(context["_private_files"][entry["name"]]["path"])
        assert path.is_relative_to(h.backend.root) and sha_file(path) == entry["sha256"]
    # A large append-only history becomes a readable immutable file, not a giant model prompt.
    large = copy.deepcopy(snapshot)
    large["proposals"] *= 500
    assert len(json.dumps(h.backend.context(large, None)["mission"])) < 30000


def test_real_git_build_and_atomic_enqueue_are_idempotent(backend_fixture):
    h = backend_fixture
    result = h.build()
    current = h.snapshot()
    assert result["state"] == "queued" and h.job()["approved_by"] == "UHUMAN"
    with ThreadPoolExecutor(2) as pool:
        answers = list(pool.map(lambda _: h.backend.enqueue(current), range(2)))
    assert {row["job_id"] for row in answers} == {result["job_id"]}
    with h.company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM research_jobs").fetchone()["n"] == 1
    assert len(current["attempts"]) == 1 and current["cumulative_trials"] == 0
    assert h.job()["priority"] == 100


def test_build_response_is_not_applied_after_owner_revision_changes(backend_fixture):
    h = backend_fixture
    h.select()
    row, snapshot = h.schedule()
    row = h.respond(row, {"patches": [{"path": "candidate.py", "expected_text": "SYNTHETIC_FIXTURE = True\n",
                                       "replacement_text": "SYNTHETIC_FIXTURE = False\n"}], "rationale": "Fixture"})
    with h.company.db.transaction() as conn:
        conn.execute("UPDATE projects SET revision=revision+1 WHERE id=%s", (h.project["project_id"],))
    with pytest.raises(PolicyError):
        h.backend.apply_stage(row, snapshot)
    assert h.snapshot()["trials"][0]["plan"] is None and not h.snapshot()["attempts"]


def test_invalid_archive_withholds_every_result_and_uncertain_never_requeues(backend_fixture):
    h = backend_fixture
    h.build()
    h.deliver(invalid=True)
    assert h.backend.reconcile()["state"] == "awaiting_audit"
    assert h.snapshot()["cumulative_trials"] == 0 and not h.snapshot()["outcomes"]
    assert h.backend.reconcile()["state"] == "idle"
    with h.company.db.transaction() as conn:
        conn.execute("UPDATE research_jobs SET state='uncertain'")
    assert h.backend.reconcile()["state"] == "idle"
    assert h.backend.enqueue(h.snapshot())["state"] == "waiting"
    assert h.job()["state"] == "uncertain"


def test_actual_failure_receipt_is_separate_from_trials_and_unchanged_repair_is_rejected(backend_fixture):
    h = backend_fixture
    h.build()
    assignment = ResearchStore(h.company).poll()["assignment"]
    ResearchStore(h.company).heartbeat(assignment["job_id"], WorkerUpdate(
        lease_token=assignment["lease_token"], sequence=1, state="failed", reason="qualification_failed"))
    assert h.backend.reconcile()["state"] == "technical_waiting"
    state = h.snapshot()
    assert state["cumulative_trials"] == 0 and len(state["outcomes"]) == 1
    reference = next(iter(state["outcomes"][0]["payload"]["evidence_files"]))
    assert (h.backend.root / reference).is_file()
    with pytest.raises(PolicyError, match="unchanged_technical_retry"):
        h.build(repair=True)
    row = h.stage_row()
    h.controller._failure(row, "synthetic_unchanged_repair")
    with h.company.db.transaction() as conn:
        conn.execute("UPDATE research_mission_stages SET retry_at=now()-interval '1 second' WHERE id=%s", (row["id"],))
    h.build(repair=True, replacement="SYNTHETIC_FIXTURE = False\nREPAIRED = True\n")
    assert len(h.snapshot()["attempts"]) == 2 and h.snapshot()["cumulative_trials"] == 0


@pytest.mark.parametrize("failure", ["unread", "unverified", "missing_qlab"])
def test_missing_read_evidence_and_nonpass_audit_cannot_publish(backend_fixture, failure):
    h = backend_fixture
    h.build()
    h.deliver()
    assert h.backend.reconcile()["state"] == "received"
    h.interpret_current()
    row, snapshot = h.audit_response(read=failure != "unread", verdict="unverified" if failure == "unverified" else "pass")
    if failure == "missing_qlab":
        h.company.settings.research_qlab_profile_file = None
    with pytest.raises(ValueError):
        h.backend.apply_stage(row, snapshot)
    assert not h.snapshot()["publications"]
    with h.company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM sources WHERE id LIKE 'mission-report:%'").fetchone()["n"] == 0


def test_qualification_real_git_pg_restart_audit_report_and_no_duplicate_experiment(backend_fixture):
    h = backend_fixture
    owned = ["src/quant_company/research/mission_backend.py", "tests/test_research_mission_backend.py"]
    subprocess.run(["git", "ls-files", "--error-unmatch", *owned], check=True, capture_output=True)
    assert subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *owned]).returncode == 0
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    h.build()
    archive = h.deliver()
    assert h.backend.reconcile()["state"] == "received"
    h.backend = MissionBackend(Company(h.company.settings, h.company.roles))
    assert h.backend.reconcile()["state"] == "idle"
    state = h.snapshot(public=True)
    assert not state["trials"][0]["performance_visible"]
    h.interpret_current()
    row, snapshot = h.audit_response()
    result = h.backend.apply_stage(row, snapshot)
    assert result["state"] == "completed"
    state = h.snapshot(public=True)
    assert state["trials"][0]["performance_visible"]
    job = h.job()
    assert job["state"] == "completed"
    with h.company.db.transaction() as conn:
        source = conn.execute("SELECT * FROM sources WHERE id=%s", (result["source_id"],)).fetchone()
        director = conn.execute("SELECT * FROM tasks WHERE id=%s", (job["report"]["director_task_id"],)).fetchone()
        assert source["approved"] and source["synthetic"] and director["agent"] == "director"
        source_json = json.loads(source["content"])
        assert source_json["summary"]["synthetic"] and source_json["summary"]["cumulative_scientific_trials"] == 0
        assert "read_source" in director["instruction"] and "태그" in director["instruction"]
    report_path = Path(job["report"]["uri"].removeprefix("file://"))
    assert report_path.is_file() and sha_file(report_path) == job["report"]["html_sha256"]
    assert "Synthetic engineering fixture" in report_path.read_text()
    # Consume the actual verified source through the ordinary director path before
    # allowing another background stage. Notification is rendered by the server.
    for decision in (
        AgentDecision(say="", status="continue", tools=[{"name": "read_source", "arguments": {
            "source_id": result["source_id"]}}]),
        AgentDecision(say="합성 fixture 연결 검증 보고서입니다. 실제 금융 성과가 아닙니다.", status="complete", artifacts=[
            {"title": "Synthetic engineering report", "content": job["report"]["uri"], "source_ids": [result["source_id"]]}]),
    ):
        with h.company.db.transaction() as conn:
            turn = conn.execute("SELECT id FROM turns WHERE task_id=%s AND status='queued'",
                                (director["id"],)).fetchone()
        identity = str(turn["id"])
        assert h.company.prepare_turn(identity)["state"] == "ready"
        h.company.commit_turn(identity, ProviderResponse(request_id=identity, provider="fixture", decision=decision))
    with h.company.db.transaction() as conn:
        assert conn.execute("SELECT 1 FROM outbox WHERE project_id=%s AND text LIKE '<@UHUMAN>%%'",
                            (h.project["project_id"],)).fetchone()
    assert h.backend.reconcile()["state"] == "idle"
    assert len(h.snapshot()["outcomes"]) == 1
    with pytest.raises(PolicyError, match="duplicate_scientific_configuration"):
        h.build()
    assert len(h.snapshot()["attempts"]) == 1
    receipt = {"schema_version": 1, "exact_commit": head, "qualification": "real-git-pg-restart-qlab-publication",
               "fixture": "synthetic-zero-return-not-research", "scientific_performance_measured": False,
               "candidate_code_executed": False, "qlab_commit": h.qlab.commit,
               "worker_archive_sha256": sha_file(archive), "report_sha256": sha_file(report_path),
               "independent_task_bound": True, "all_validator_text_files_read": True,
               "unverified_metrics_withheld": True, "post_restart_outcome_count": 1,
               "verified_source_read_by_director": True, "director_final_owner_mention": True,
               "renamed_duplicate_experiment_rejected": True,
               "report_archive_sha256": job["report"]["report_archive_sha256"],
               "audit_scope_digest": source["metadata"]["audit_scope_digest"]}
    Path(".local/mission-backend-qualification.json").write_text(json.dumps(receipt, indent=2) + "\n")
