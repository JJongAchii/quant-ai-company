"""Requested exact-commit paths must remain readable under the unchanged repository byte budget."""
import base64
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from quant_company.maintenance.github import GitHub, blob_sha
from quant_company.maintenance.policy import CodeQuery, MaintenanceConfig
from quant_company.maintenance.runner import Maintainer
from quant_company.system_state import record_repository

TARGET = "tests/test_maintenance_repair_path.py"


def fixture_github():
    files = {f"src/large_{index:02}.py": "# " + "x" * 99_997 + "\n" for index in range(20)}
    files[TARGET] = "def test_owner_progress():\n    assert True\n"
    files["secrets/private.py"] = "private = True\n"
    snapshot = {"commit": "a" * 40, "tree": "b" * 40, "paths": [],
                "entries": {path: {"type": "blob", "mode": "100644", "size": len(body.encode()),
                                    "sha": blob_sha(body)} for path, body in files.items()}}
    blobs = {blob_sha(body): body for body in files.values()}
    reads = []

    def handler(request):
        sha = request.url.path.rsplit("/", 1)[-1]
        reads.append(sha)
        return httpx.Response(200, json={"encoding": "base64",
                              "content": base64.b64encode(blobs[sha].encode()).decode()})

    config = MaintenanceConfig(app_id=1, installation_id=2, private_key_file=Path("unused-fixture-key"),
                               allowed_owners=["UHUMAN"])
    github = GitHub(config, transport=httpx.MockTransport(handler))
    github._token, github._expires = "fixture-token", datetime.now().timestamp() + 3600
    return github, snapshot, files, reads


def test_requested_test_is_fetched_first_without_raising_read_limits():
    github, snapshot, files, _ = fixture_github()
    initial, coverage = github.read_repository(snapshot)
    assert TARGET not in initial and TARGET in coverage["omitted_paths"]
    assert hasattr(github, "read_inspection_files"), "Explicit inspection must hydrate omitted safe paths"
    hydrated, refreshed = github.read_inspection_files(
        snapshot, [CodeQuery(path=TARGET, start_line=1, line_count=2)], previous=initial)
    assert hydrated[TARGET] == files[TARGET]
    assert refreshed["read_bytes"] <= 2_000_000 and refreshed["read_files"] <= 20
    assert "secrets/private.py" not in hydrated


@pytest.mark.integration
def test_read_only_prefetch_reuses_exact_commit_and_preserves_recorded_requests(company):
    github, snapshot, files, _ = fixture_github()
    initial, coverage = github.read_repository(snapshot)
    with company.db.transaction() as conn:
        record_repository(conn, snapshot, initial, {"coverage": coverage})
        before = conn.execute("SELECT count(*) AS n FROM turns").fetchone()["n"]
    maintenance = Maintainer(company, github.config, github=github)
    assert hasattr(maintenance, "prefetch_inspections"), "The live consumer must hydrate before inspecting"
    maintenance.prefetch_inspections(snapshot, [CodeQuery(path=TARGET, line_count=2)])
    from quant_company.maintenance.investigation import inspect_code
    with company.db.transaction() as conn:
        evidence = inspect_code(conn, snapshot, [CodeQuery(path=TARGET, line_count=2)])
        saved = conn.execute("SELECT files FROM repository_evidence WHERE commit=%s", (snapshot["commit"],)).fetchone()
        assert saved["files"][TARGET] == files[TARGET]
        assert all(saved["files"][path] == body for path, body in initial.items())
        assert conn.execute("SELECT count(*) AS n FROM turns").fetchone()["n"] == before
    assert evidence[0]["content"].startswith("1: def test_owner_progress()")
    assert evidence[0]["key"].startswith("code:" + snapshot["commit"] + ":")


def test_protected_or_unknown_paths_cannot_trigger_a_blob_fetch():
    github, snapshot, _, reads = fixture_github()
    initial, _ = github.read_repository(snapshot)
    reads.clear()
    assert hasattr(github, "read_inspection_files")
    hydrated, _ = github.read_inspection_files(snapshot, [
        CodeQuery(path="secrets/private.py"), CodeQuery(path="tests/unknown.py")], previous=initial)
    assert "secrets/private.py" not in hydrated and "tests/unknown.py" not in hydrated
    assert not reads



@pytest.mark.integration
def test_filename_lookup_hydrates_and_returns_requested_test_body(company):
    github, snapshot, files, _ = fixture_github()
    initial, coverage = github.read_repository(snapshot)
    with company.db.transaction() as conn:
        record_repository(conn, snapshot, initial, {"coverage": coverage})
    maintenance = Maintainer(company, github.config, github=github)
    query = CodeQuery(query="test_maintenance_repair_path", line_count=2)
    maintenance.prefetch_inspections(snapshot, [query])
    from quant_company.maintenance.investigation import inspect_code
    with company.db.transaction() as conn:
        evidence = inspect_code(conn, snapshot, [query])
    assert evidence and evidence[0]["path"] == TARGET, "Path lookup must reach the hydrated blob"
    assert evidence[0]["content"].startswith("1: def test_owner_progress()")
