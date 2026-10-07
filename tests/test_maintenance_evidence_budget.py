"""Real PostgreSQL; GitHub and model responses are explicitly simulated."""

import copy
import json

import pytest

from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.maintenance.github import READ_ORDER_VERSION, GitHub, blob_sha
from quant_company.maintenance.policy import Triage, digest
from quant_company.maintenance.runner import diagnostic_history, proposal_material
from quant_company.system_state import record_repository

from .test_maintenance import ORIGINAL, SOURCE, config, make_maintainer


class InspectThenFinish:
    def __init__(self):
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        output = {"finding": None, "reason": "Inspect original caller" if len(self.requests) == 1 else "No reproducible defect"}
        if len(self.requests) == 1:
            output["inspect"] = [{"path": SOURCE, "start_line": 1, "line_count": 3}]
        return ProviderResponse(request_id=request.request_id, provider="fixture",
            decision=AgentDecision(status="complete", say="fixture", artifacts=[
                {"title": "fixture", "content": json.dumps(output), "source_ids": []}]))


def move_documentation(runner):
    old = runner.github.snapshot()
    runner.github.snapshot = lambda: {**old, "commit": "f"*40, "entries": {
        **old["entries"], "docs/project/status.md": {"sha": "e"*40, "type": "blob", "mode": "100644"}}}


@pytest.mark.integration
async def test_documentation_change_continues_exact_original_investigation(company):
    runner = make_maintainer(company)
    runner.provider = InspectThenFinish()
    await runner.tick()
    with company.db.transaction() as conn:
        job = conn.execute("SELECT * FROM maintenance_jobs WHERE state='triage'").fetchone()
        before = copy.deepcopy(job["payload"])
        original = conn.execute("SELECT id,request,response FROM maintenance_calls ORDER BY id").fetchall()
    move_documentation(runner)
    await runner.tick()
    with company.db.transaction() as conn:
        saved = conn.execute("SELECT * FROM maintenance_jobs WHERE id=%s", (job["id"],)).fetchone()
        assert saved["state"] == "done" and saved["payload"]["snapshot"]["commit"] == before["snapshot"]["commit"]
        assert saved["payload"]["investigation_evidence"] == before["investigation_evidence"]
        assert saved["payload"]["diagnostic_revision"] == before["diagnostic_revision"]
        assert conn.execute("SELECT count(*) AS n FROM maintenance_revisions").fetchone()["n"] == 0
        for row in original:
            assert conn.execute("SELECT id,request,response FROM maintenance_calls WHERE id=%s", (row["id"],)).fetchone() == row
    assert runner.provider.requests[1].request_id.endswith("-triage-i1")


@pytest.mark.integration
@pytest.mark.parametrize("change", ["code", "configuration"])
async def test_behavior_or_configuration_change_restarts_investigation(company, change):
    runner = make_maintainer(company)
    runner.provider = InspectThenFinish()
    await runner.tick()
    if change == "code":
        old = runner.github.snapshot()
        runner.github.snapshot = lambda: {**old, "commit": "f"*40,
            "entries": {SOURCE: {**old["entries"][SOURCE], "sha": "e"*40}}}
    else:
        company.settings.company_max_depth = 2
    await runner.tick()
    with company.db.transaction() as conn:
        job = conn.execute("SELECT * FROM maintenance_jobs WHERE kind='triage'").fetchone()
        assert job["payload"]["diagnostic_revision"] == 1
        assert "investigation_evidence" not in job["payload"]
        assert conn.execute("SELECT count(*) AS n FROM maintenance_revisions").fetchone()["n"] == 1
    assert "-r1-triage" in runner.provider.requests[-1].request_id


@pytest.mark.integration
async def test_patch_still_rechecks_documentation_only_base_movement(company):
    runner = make_maintainer(company)
    await runner.tick()
    with company.db.transaction() as conn:
        repair = conn.execute("SELECT * FROM maintenance_jobs WHERE kind='repair'").fetchone()
    move_documentation(runner)
    await runner.tick()
    with company.db.transaction() as conn:
        assert conn.execute("SELECT state FROM maintenance_jobs WHERE id=%s", (repair["id"],)).fetchone()["state"] == "superseded"
    assert len(runner.provider.requests) == 1 and runner.github.published == 0


def test_repository_budget_keeps_provider_code_before_large_operational_archives():
    source = "src/quant_company/providers/claude_runner.py"
    files = {source: ORIGINAL}
    files.update({f"docs/project/evidence/archive-{i:02d}.py": "#" + "x"*99000 for i in range(25)})
    snapshot = {"entries": {path: {"sha": blob_sha(content), "type": "blob", "mode": "100644",
                                   "size": len(content.encode())} for path, content in files.items()}}
    github = GitHub(config())
    github.request = lambda *args: (_ for _ in ()).throw(AssertionError("Exact blobs must be reused"))
    observed, coverage = github.read_repository(snapshot, files)
    assert source in observed and coverage["read_bytes"] <= 2_000_000
    assert coverage["omitted_paths"] and coverage["read_order_version"] == READ_ORDER_VERSION


@pytest.mark.integration
def test_expanding_same_commit_coverage_preserves_prior_exact_records(company):
    runner = make_maintainer(company)
    snapshot = runner.github.snapshot()
    with company.db.transaction() as conn:
        record_repository(conn, snapshot, {SOURCE: ORIGINAL}, {"coverage": {"read_order_version": 1}})
        record_repository(conn, snapshot, {"docs/project/example.md": "Retained evidence"}, {"coverage": {"read_order_version": 2}})
        row = conn.execute("SELECT files FROM repository_evidence WHERE commit=%s", (snapshot["commit"],)).fetchone()
    assert row["files"] == {SOURCE: ORIGINAL, "docs/project/example.md": "Retained evidence"}


def test_technical_case_only_uses_linked_history_without_modifying_stored_inputs():
    review = {"evidence": [{"key": "message:related", "project_id": "one", "text": "Original request"},
                            {"key": "message:unrelated", "project_id": "two", "text": "Private unrelated thread"}]}
    payload = {"observations": [{"key": "failure:one", "kind": "research_contract_failure", "project_id": "one"}],
               "review": review}
    before = digest(payload)
    focused = diagnostic_history(payload)
    assert focused["evidence"] == review["evidence"][:1] and focused["case_context"]["omitted"] == 1
    assert digest(payload) == before
    payload["observations"][0]["kind"] = "human"
    assert diagnostic_history(payload) == review


@pytest.mark.integration
def test_bounded_model_facts_preserve_full_bound_system_evidence(company):
    runner = make_maintainer(company)
    runner.store.collect()
    job = runner.store.next_job()
    snapshot = runner.refresh_repository()
    from quant_company.system_state import diagnosis_context
    with company.db.transaction() as conn:
        diagnosis = diagnosis_context(conn, company, ["UHUMAN"], snapshot, "")
    full_digest = digest(diagnosis)
    _, prompt = proposal_material({"observations": job["payload"]["observations"],
                                  "current_implementation": diagnosis}, Triage)
    shown = json.loads(prompt.split("EVIDENCE JSON:\n", 1)[1])["current_implementation"]
    assert "prompt_system" not in shown and digest(diagnosis) == full_digest
    assert shown["system"]["runtime"]["code_commit"] == diagnosis["system"]["runtime"]["code_commit"]
    assert shown["system"]["assessments"] == diagnosis["system"]["assessments"]
