"""Real PostgreSQL regression checks; model/GitHub responses are explicit fixtures."""
import copy
import json

import pytest

from quant_company.maintenance import investigation
from quant_company.maintenance.policy import Triage, digest
from quant_company.maintenance.runner import proposal_material
from quant_company.system_state import record_repository

from .test_maintenance import ORIGINAL, SOURCE, make_maintainer

COMMIT = "a" * 40
inspect_code = investigation.inspect_code


def append_inspections(*args):
    assert hasattr(investigation, "append_inspections"), "Stored evidence must avoid duplicate appends"
    return investigation.append_inspections(*args)


def inspection_context(*args):
    assert hasattr(investigation, "inspection_context"), "Prior exact ranges require a visible index"
    return investigation.inspection_context(*args)


def snapshot(files, commit=COMMIT):
    from quant_company.maintenance.github import blob_sha
    return {"commit": commit, "entries": {path: {"sha": blob_sha(content), "type": "blob", "mode": "100644"}
                                       for path, content in files.items()}, "paths": list(files)}


class NoRead:
    def execute(self, *args):
        raise AssertionError("An exact positive query receipt must bypass repository loading")


@pytest.mark.integration
def test_exact_query_reuses_full_positive_result_and_preserves_original(company):
    files = {SOURCE: ORIGINAL}
    snap = snapshot(files)
    query = {"path": SOURCE, "start_line": 1, "line_count": 2}
    with company.db.transaction() as conn:
        record_repository(conn, snap, files, {})
        first = inspect_code(conn, snap, [query])
    receipts = [{"requests": [query], "evidence": first}]
    before = digest(receipts)
    repeated = inspect_code(NoRead(), snap, [query], previous=receipts)
    assert [{k: v for k, v in row.items() if k != "cache_hit"} for row in repeated] == first
    assert repeated[0]["cache_hit"] is True and digest(receipts) == before
    payload = {"investigation_evidence": copy.deepcopy(first)}
    append_inspections(payload, repeated)
    assert payload["investigation_evidence"] == first


@pytest.mark.integration
def test_new_ranges_and_commits_do_not_reuse_old_query(company):
    files = {SOURCE: ORIGINAL}
    old, changed = snapshot(files), snapshot({SOURCE: ORIGINAL.replace("left - right", "left + right")}, "b" * 40)
    query = {"path": SOURCE, "start_line": 1, "line_count": 1}
    with company.db.transaction() as conn:
        record_repository(conn, old, files, {})
        record_repository(conn, changed, {SOURCE: ORIGINAL.replace("left - right", "left + right")}, {})
        first = inspect_code(conn, old, [query])
        prior = [{"requests": [query], "evidence": first}]
        fresh_range = inspect_code(conn, old, [{**query, "start_line": 2}], previous=prior)
        fresh_commit = inspect_code(conn, changed, [query], previous=prior)
    assert "cache_hit" not in fresh_range[0] and fresh_range[0]["start_line"] == 2
    assert "cache_hit" not in fresh_commit[0] and fresh_commit[0]["key"].startswith("code:" + "b"*40)
    payload = {"investigation_evidence": first.copy()}
    append_inspections(payload, fresh_range)
    assert len(payload["investigation_evidence"]) == 2


@pytest.mark.integration
def test_missing_query_retries_after_exact_snapshot_coverage_expands(company):
    files = {SOURCE: ORIGINAL}
    snap = snapshot(files)
    query = {"path": SOURCE}
    with company.db.transaction() as conn:
        record_repository(conn, snap, {}, {})
        missing = inspect_code(conn, snap, [query])
        record_repository(conn, snap, files, {})
        fresh = inspect_code(conn, snap, [query], previous=[{"requests": [query], "evidence": missing}])
    assert missing[0]["status"] == "not_in_readable_snapshot"
    assert fresh[0]["content"] and "cache_hit" not in fresh[0]


@pytest.mark.integration
def test_search_cache_keeps_every_match_not_only_last_four_excerpts(company):
    files = {f"src/part{i}.py": "def needle():\n    return 1\n" for i in range(6)}
    snap = snapshot(files)
    query = {"query": "needle", "line_count": 2}
    with company.db.transaction() as conn:
        record_repository(conn, snap, files, {})
        first = inspect_code(conn, snap, [query])
    repeated = inspect_code(NoRead(), snap, [query], previous=[{"evidence": first}])
    assert len(repeated) == 4
    assert [row["path"] for row in repeated] == sorted(files)[:4]


def test_catalog_retains_old_ranges_and_explicit_lookup_over_broad_search_tail():
    rows = [{"key": f"code:{COMMIT}:src/p{i}.py", "path": f"src/p{i}.py",
             "start_line": 1, "total_lines": 2, "content": f"1: caller_{i}\n2: consumer_{i}"} for i in range(13)]
    explicit = rows[0]
    prior = {"evidence": rows[:8], "requests": [{"query": "caller"}]}
    recent = {"evidence": rows, "requests": [{"path": explicit["path"]}, {"query": "consumer"}]}
    payload = {"investigation_evidence": rows + [rows[0]], "investigation_requests": [prior, recent]}
    before = digest(payload)
    context = inspection_context(payload)
    assert context["investigated_code"][0] == explicit
    assert len(context["investigated_code"]) == 4 and len(context["inspection_catalog"]) == 13
    assert sum(row["shown"] for row in context["inspection_catalog"]) == 4
    assert all("content" not in row for row in context["inspection_catalog"])
    assert "not model memory" in context["inspection_lookup"] and digest(payload) == before


def test_prompt_canonicalizes_nested_order_and_keeps_full_original_evidence():
    source = {"path": SOURCE, "key": "code:original", "content": "long_source\n"*800, "start_line": 1}
    payload = {"requested_diagnosis": "Inspect this case", "observations": [{"key": "original", "text": "human"}],
               "history": {"evidence": [{"key": "kept", "replay_input": {"employee_context": {"instruction": "original"}}}]},
               "current_implementation": {"source_files": [source], "system": {"z": 1, "a": 2}},
               "inspection_catalog": [{"key": "code:prior", "path": "src/old.py", "shown": False}]}
    before = digest(payload)
    shown, prompt = proposal_material(payload, Triage)
    reversed_order = json.loads(json.dumps(payload), object_pairs_hook=lambda pairs: dict(reversed(pairs)))
    assert proposal_material(reversed_order, Triage)[1] == prompt
    assert len(shown["current_implementation"]["source_files"][0]["content"]) == 2000
    assert shown["current_implementation"]["source_files"][0]["excerpted"]
    assert shown["history"] == payload["history"] and shown["observations"] == payload["observations"]
    assert shown["inspection_catalog"] == payload["inspection_catalog"] and digest(payload) == before


@pytest.mark.integration
async def test_repeated_fixture_inspection_preserves_calls_and_original_receipts(company):
    from quant_company.contracts import AgentDecision, ProviderResponse
    runner = make_maintainer(company)
    class Repeating:
        def __init__(self):
            self.requests = []
        async def run(self, request):
            self.requests.append(request)
            result = {"finding": None, "reason": "Fixture reads same caller"}
            if len(self.requests) < 3:
                result["inspect"] = [{"path": SOURCE, "line_count": 2}]
            return ProviderResponse(request_id=request.request_id, provider="fixture",
                                    decision=AgentDecision(status="complete", say="fixture", artifacts=[
                                        {"title": "fixture", "content": json.dumps(result), "source_ids": []}]))
    runner.provider = Repeating()
    await runner.tick()
    with company.db.transaction() as conn:
        original = conn.execute("SELECT id,request,response FROM maintenance_calls ORDER BY id").fetchall()
    result = await runner.tick()
    with company.db.transaction() as conn:
        state = conn.execute('SELECT state,error FROM maintenance_jobs').fetchall()
    assert result['state'] == 'advanced', state
    second = json.loads(runner.provider.requests[1].prompt.split("EVIDENCE JSON:\n", 1)[1])
    assert second.get("inspection_catalog") and second["investigated_code"][0]["path"] == SOURCE
    with company.db.transaction() as conn:
        job = conn.execute("SELECT * FROM maintenance_jobs WHERE state='triage'").fetchone()
        assert len(job["payload"]["investigation_evidence"]) == 1
        assert len(job["payload"]["investigation_requests"]) == 2
        assert job["payload"]["investigation_requests"][-1]["evidence"][0]["cache_hit"]
        for row in original:
            assert conn.execute("SELECT id,request,response FROM maintenance_calls WHERE id=%s", (row["id"],)).fetchone() == row
    await runner.tick()
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM maintenance_jobs WHERE state='done'").fetchone()["n"] == 1
    assert len(runner.provider.requests) == 3
