"""Real PostgreSQL/pytest; model, GitHub and Slack responses are explicit fixtures."""
import json

import pytest
from psycopg.types.json import Jsonb

from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.maintenance import investigation
from quant_company.maintenance.policy import Triage, digest
from quant_company.maintenance.requests import progress_text, submit
from quant_company.maintenance.runner import Maintainer, proposal_material
from quant_company.staff.independent_review import IndependentReviewStore

from . import test_maintenance_engineering as engineering
from .test_maintenance import SOURCE, config, prepare
from .test_staff_independent_review import completed  # noqa: F401

COMMIT = "a" * 40


def excerpt(start, lines):
    return {"key": f"code:{COMMIT}:{SOURCE}", "path": SOURCE, "start_line": start,
            "total_lines": 300, "content": "\n".join(f"{start+i}: {line}" for i, line in enumerate(lines)),
            "excerpted": True}


def test_earlier_caller_and_later_consumer_survive_the_next_stateless_call():
    caller = excerpt(1, ["def caller():", "    return required_input"])
    consumer = excerpt(200, ["def consumer():", "    return caller()"])
    payload = {"investigation_evidence": [caller, consumer], "investigation_requests": [
        {"requests": [{"path": SOURCE, "start_line": 1}], "evidence": [caller]},
        {"requests": [{"path": SOURCE, "start_line": 200}], "evidence": [consumer]}]}
    before = digest(payload)
    context = investigation.inspection_context(payload)
    shown, prompt = proposal_material(context, Triage)
    bodies = "\n".join(row["content"] for row in shown["investigated_code"])
    assert "1: def caller():" in bodies, "Previously read callers must remain visible to the next stateless model"
    assert "200: def consumer():" in bodies
    assert len(shown["investigated_code"]) == 2  # no invented lines across the unread gap
    assert all(row["shown"] for row in shown["inspection_catalog"])
    assert len(prompt) <= 88000 and digest(payload) == before


def test_requested_code_is_not_truncated_when_the_provider_budget_has_room():
    row = excerpt(1, [f"required_code_{i} = 'caller and consumer evidence'" for i in range(160)])
    before = digest(row)
    shown, prompt = proposal_material({"investigated_code": [row]}, Triage)
    assert shown["investigated_code"][0]["content"] == row["content"], "A requested 160-line body must survive when under budget"
    assert shown["investigated_code"][0]["shown_line_count"] == 160
    assert len(prompt) <= 88000 and digest(row) == before


def test_overlapping_ranges_are_joined_without_duplicates_or_original_mutation():
    first = excerpt(1, ["def caller():", "    value = 1", "    return value"])
    second = excerpt(2, ["    value = 1", "    return value", "def consumer():", "    return caller()"])
    payload = {"investigation_evidence": [first, second], "investigation_requests": [{"evidence": [second], "requests": [{"path": SOURCE}]}]}
    before = digest(payload)
    shown = investigation.inspection_context(payload)["investigated_code"]
    assert len(shown) == 1 and shown[0]["content"].count("2:     value = 1") == 1
    assert shown[0]["start_line"] == 1 and "5:     return caller()" in shown[0]["content"]
    assert digest(payload) == before


def test_large_system_descriptions_do_not_displace_required_code():
    row = excerpt(1, [f"needed_caller_{i} = 'read this complete range'" for i in range(160)])
    material = {"investigated_code": [row], "current_implementation": {
        "system": {"unrelated_history": [{"description": "irrelevant " * 20000} for _ in range(8)]}}}
    before = digest(material)
    shown, prompt = proposal_material(material, Triage)
    assert len(prompt) <= 88000 and shown["investigated_code"][0]["content"] == row["content"]
    assert shown["prompt_system_compaction"] >= 1 and digest(material) == before


async def test_accumulated_code_reaches_real_regression_repair_and_pr(company, tmp_path, monkeypatch):
    original = "def bounded_sum(left, right):\n    return left + right\n" + "\n" * 197 + "def format_label(value):\n    return value\n"
    monkeypatch.setattr(engineering, "BASE", original)

    class Model(engineering.EngineeringModel):
        async def run(self, request):
            data = json.loads(request.prompt.split("EVIDENCE JSON:\n")[1])
            if request.request_id.endswith("-triage"):
                output = {"reason": "Read the existing caller before selecting a repair.", "inspect": [{"path": SOURCE, "start_line": 1, "line_count": 2}]}
            elif request.request_id.endswith("-triage-i1"):
                output = {"reason": "Read the consumer while preserving the caller already inspected.", "inspect": [{"path": SOURCE, "start_line": 200, "line_count": 2}]}
            elif request.request_id.endswith("-triage-i2"):
                body = "\n".join(row["content"] for row in data["investigated_code"])
                assert "return left + right" in body, "Producer evidence was lost before selecting a consumer repair"
                assert "def format_label(value)" in body and "return value" in body
                output = {"reason": "The requested public formatter needs normalization and must preserve its existing arithmetic caller.",
                    "finding": {"problem_key": "formatter_retains_recorded_input", "title": "Normalize the public formatter",
                    "problem": "The existing public formatter returns the original whitespace and case.",
                    "reproduction": "format_label('  FED  ') returns the original value.",
                    "expected": "The public formatter returns fed and the existing sum returns 5 for 3 and 2.",
                    "category": "feature_request", "hypothesis": "Wire the normalizer through the existing public consumer.",
                    "evaluation": {"mode": "regression", "success_criterion": "The formatter normalizes text and the existing sum behavior remains correct."},
                    "evidence_keys": [data["observations"][0]["key"], data["investigated_code"][0]["key"]],
                    "paths": [SOURCE], "new_paths": [engineering.NEW]}}
            else:
                return await super().run(request)
            self.requests.append(request)
            return ProviderResponse(request_id=request.request_id, provider="fixture", decision=AgentDecision(
                status="complete", say="Simulated evidence-dependent proposal",
                artifacts=[{"title": "Proposal", "content": json.dumps(output)}]))

    prepare(company)
    runner = Maintainer(company, config(), github=engineering.LocalCI(tmp_path), provider=Model())
    runner.store.initialize()
    with company.db.transaction() as conn:
        project = conn.execute("SELECT * FROM projects LIMIT 1").fetchone()
        task = conn.execute("SELECT * FROM tasks WHERE project_id=%s", (project["id"],)).fetchone()
        assert submit(conn, company, project, task)["accepted"]
    for _ in range(14):
        await runner.tick()
        with company.db.transaction() as conn:
            repair = conn.execute("SELECT * FROM maintenance_jobs WHERE kind='repair'").fetchone()
        if repair and repair["state"] in {"pr_open", "blocked"}:
            break
    assert repair and repair["state"] == "pr_open", repair and repair["error"]
    assert runner.github.prs == 1 and runner.github.published == 2
    assert runner.github.results[0].returncode == 1 and runner.github.results[-1].returncode == 0
    assert repair["payload"]["patch_attempt"] == 2 and len(repair["payload"]["investigation_requests"]) == 2


async def test_uncertain_review_is_handed_off_before_spending_model_calls(completed):  # noqa: F811
    company, identity, _ = completed
    prepare(company)
    company.settings.company_improvements_enabled = True
    company.settings.improvements_channel_id = "CQUANT"
    company.roles["maintainer"] = company.roles["engineer"].model_copy(update={"id": "maintainer", "active": True})
    review_store = IndependentReviewStore(company)
    ready = review_store.prepare()
    review_id = ready["request"]["request_id"]
    with company.db.transaction() as conn:
        conn.execute("UPDATE staff_independent_reviews SET state='blocked',error='uncertain' WHERE id=%s", (review_id,))
        before = conn.execute("SELECT to_jsonb(r) value FROM staff_runs r WHERE id=%s", (identity,)).fetchone()["value"]
        original_review = conn.execute("SELECT to_jsonb(v) value FROM staff_independent_reviews v WHERE id=%s", (review_id,)).fetchone()["value"]
    runner = Maintainer(company, config(), github=engineering.LocalCI(__import__("pathlib").Path("/unused")), provider=engineering.EngineeringModel())
    runner.store.initialize()
    observation = {"key": "staff-review:" + review_id, "kind": "staff_independent_review_failure", "author": "financial_strategist",
        "detail": {"review_id": review_id, "run_id": str(identity), "error": "uncertain", "operator_reconciliation_required": True}}
    from uuid import uuid4
    job_id = str(uuid4())
    with company.db.transaction() as conn:
        conn.execute("INSERT INTO maintenance_jobs(id,kind,state,payload) VALUES (%s,'triage','triage',%s)",
            (job_id, Jsonb({"owners": ["UHUMAN"], "observations": [observation]})))
    assert (await runner.tick())["state"] == "advanced"
    with company.db.transaction() as conn:
        job = conn.execute("SELECT * FROM maintenance_jobs WHERE id=%s", (job_id,)).fetchone()
        assert job["state"] == "blocked" and job["error"] == "review_reconciliation_required", "Unknown original output needs operator evidence before code/model work"
        assert job["receipt"]["outcome"] == "operator_action_required"
        assert job["receipt"]["review_ids"] == [review_id]
        assert conn.execute("SELECT count(*) n FROM maintenance_calls").fetchone()["n"] == 0
        assert conn.execute("SELECT to_jsonb(r) value FROM staff_runs r WHERE id=%s", (identity,)).fetchone()["value"] == before
        assert conn.execute("SELECT to_jsonb(v) value FROM staff_independent_reviews v WHERE id=%s", (review_id,)).fetchone()["value"] == original_review
    assert not runner.provider.requests and runner.github.published == 0
    text = progress_text({"request_id": job_id, "state": job["state"], "error": job["error"]}, {})
    assert "대사" in text and "직원 평가" in text


def test_no_candidate_completion_explicitly_reports_no_repair():
    text = progress_text({"request_id": "a" * 36, "state": "done", "reason": "No supported candidate."}, {})
    assert "수정 후보 없음" in text
    assert "점검을 마쳤습니다" in text


@pytest.mark.parametrize("change", ["foreign_owner", "changed_request", "has_response", "recorded_call", "explicit_review", "other_failure"])
def test_reconciliation_handoff_cannot_hide_other_work_or_bypass_source_binding(completed, change):  # noqa: F811
    from uuid import uuid4

    from quant_company.maintenance.store import Store

    company, identity, _ = completed
    prepare(company)
    company.settings.company_improvements_enabled = True
    ready = IndependentReviewStore(company).prepare()
    review_id = ready["request"]["request_id"]
    observation = {"key": "staff-review:" + review_id, "kind": "staff_independent_review_failure", "author": "financial_strategist",
        "detail": {"review_id": review_id, "run_id": str(identity), "error": "uncertain", "operator_reconciliation_required": True}}
    if change == "other_failure":
        observation["kind"] = "staff_assessment"
    job_id = str(uuid4())
    store = Store(company, config())
    store.initialize()
    with company.db.transaction() as conn:
        conn.execute("UPDATE staff_independent_reviews SET state='blocked',error='uncertain' WHERE id=%s", (review_id,))
        if change == "foreign_owner":
            conn.execute("UPDATE staff_runs SET owner_user='UOTHER' WHERE id=%s", (identity,))
        elif change == "changed_request":
            conn.execute("UPDATE staff_independent_reviews SET request=%s WHERE id=%s", (Jsonb({"changed": True}), review_id))
        elif change == "has_response":
            conn.execute("UPDATE staff_independent_reviews SET response=%s WHERE id=%s", (Jsonb({"existing_response": True}), review_id))
        conn.execute("INSERT INTO maintenance_jobs(id,kind,state,payload) VALUES (%s,%s,'triage',%s)",
            (job_id, "review" if change == "explicit_review" else "triage", Jsonb({"owners": ["UHUMAN"], "observations": [observation]})))
        if change == "recorded_call":
            conn.execute("INSERT INTO maintenance_calls(id,job_id,request,response) VALUES (%s,%s,%s,%s)",
                ("original-unchanged", job_id, Jsonb({"original_request": True}), Jsonb({"original_result": True})))
        job = conn.execute("SELECT * FROM maintenance_jobs WHERE id=%s", (job_id,)).fetchone()
        before = conn.execute("SELECT to_jsonb(j) value FROM maintenance_jobs j WHERE id=%s", (job_id,)).fetchone()["value"]
    assert not store.hold_for_reconciliation(job)
    with company.db.transaction() as conn:
        assert conn.execute("SELECT to_jsonb(j) value FROM maintenance_jobs j WHERE id=%s", (job_id,)).fetchone()["value"] == before
