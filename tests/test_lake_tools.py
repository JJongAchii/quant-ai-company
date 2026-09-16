import json
import subprocess

import pytest

from quant_company.company import PolicyError
from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.lake_tools import query_lake

from .conftest import queued_turns


@pytest.mark.parametrize("name,args", [
    ("lake_catalog", {"uri": "s3://other"}),
    ("lake_describe", {"dataset": "../raw/private"}),
    ("lake_describe", {"dataset": "krx_etf", "limit": 1}),
    ("lake_sample", {"dataset": "krx_etf", "limit": True}),
    ("lake_sample", {"dataset": "krx_etf", "limit": 21}),
    ("lake_sample", {"dataset": "krx_etf", "columns": ["__import__('os')"]}),
])
def test_lake_inputs_cannot_select_other_paths_or_exceed_bounds(name, args, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("reader started"))
    with pytest.raises(ValueError):
        query_lake("s3://example/qdata", name, args)


def test_reader_receives_only_data_credentials_and_times_out(monkeypatch):
    for key in ["DATABASE_URL", "TEMPORAL_API_KEY", "OPERATOR_TOKEN", "MODEL_RUNTIME_TOKEN", "AWS_SECRET_ACCESS_KEY"]:
        monkeypatch.setenv(key, "private-canary")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/run/secrets/lake_read_credentials")
    monkeypatch.setenv("QDATA_CODE_COMMIT", "a" * 40)

    def run(command, **kwargs):
        assert command[-1] == "quant_company.lake_reader"
        assert kwargs["timeout"] == 18
        assert "private-canary" not in json.dumps(kwargs)
        assert kwargs["env"]["AWS_SHARED_CREDENTIALS_FILE"] == "/run/secrets/lake_read_credentials"
        assert kwargs["env"]["QDATA_LAKE"] == "s3://example/qdata"
        assert kwargs["env"]["QDATA_CODE_COMMIT"] == "a" * 40
        raise subprocess.TimeoutExpired(command, 18)

    monkeypatch.setattr(subprocess, "run", run)
    assert not query_lake("s3://example/qdata", "lake_catalog", {})["ok"]


def complete_with_tool(company, event, owner, agent="data", tools=None):
    request = company.ingest(event_key=event, text="Inspect dataset", owner=owner, agent=agent)
    turn = queued_turns(company, request["project_id"])[0]
    company.prepare_turn(turn)
    response = ProviderResponse(request_id=turn, provider="fixture", decision=AgentDecision(
        say="Inspecting actual source", status="continue", tools=tools or [
            {"name": "lake_describe", "arguments": {"dataset": "krx_etf"}},
        ]))
    company.commit_turn(turn, response)
    return request


def test_lake_receipts_become_project_scoped_sources_and_keep_dates_separate(company, monkeypatch):
    company.roles["data"].tools.append("lake_describe")
    result = {"ok": True, "data": {"dataset": "krx_etf", "observed_at": "2026-09-16T01:00:00Z",
              "source": {"uri": "s3://example/qdata/clean/krx_etf.parquet", "etag": "fixture-etag",
                         "last_modified": "2026-09-15T11:04:52Z"},
              "date_bounds": {"date": {"min": "2012-01-02", "max": "2026-09-15"}}}}
    monkeypatch.setattr("quant_company.company.query_lake", lambda *args: json.loads(json.dumps(result)))
    ids = []
    for i in range(2):
        request = complete_with_tool(company, f"lake-{i}", f"owner-{i}")
        with company.db.transaction() as conn:
            source = conn.execute("SELECT * FROM sources WHERE project_id=%s", (request["project_id"],)).fetchone()
            assert source["approved"] and not source["synthetic"]
            stored = json.loads(source["content"])["data"]
            assert stored["date_bounds"]["date"]["max"] == "2026-09-15"
            assert stored["source"]["last_modified"] == "2026-09-15T11:04:52Z"
            ids.append(source["id"])
        state = company.project_state(request["project_id"])
        receipt = json.loads(next(m["text"] for m in state["messages"] if m["kind"] == "tool"))
        assert receipt["receipt"]["source_id"] == source["id"]
        assert state["tasks"][0]["status"] == "pending"
    assert ids[0] != ids[1]


def test_only_authorized_role_can_query_and_failure_does_not_create_a_source(company, monkeypatch):
    with pytest.raises(PolicyError, match="Unauthorized tool"):
        complete_with_tool(company, "forbidden", "user", agent="director")
    company.roles["data"].tools.append("lake_describe")
    monkeypatch.setattr("quant_company.company.query_lake", lambda *args: {"ok": False, "error": "unavailable"})
    request = complete_with_tool(company, "unavailable", "user")
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM sources WHERE project_id=%s",
                            (request["project_id"],)).fetchone()["n"] == 0


def test_lake_turn_cannot_queue_multiple_scans(company):
    company.roles["data"].tools.append("lake_describe")
    with pytest.raises(PolicyError, match="one lake query"):
        complete_with_tool(company, "many-scans", "user", tools=[
            {"name": "lake_describe", "arguments": {"dataset": "krx_etf"}},
            {"name": "lake_describe", "arguments": {"dataset": "us_prices"}},
        ])


def test_compact_lake_id_collision_cannot_reuse_another_project_source(company, monkeypatch):
    company.roles["data"].tools.append("lake_describe")
    monkeypatch.setattr("quant_company.company.fingerprint", lambda value: "a" * 64)
    monkeypatch.setattr("quant_company.company.query_lake", lambda *args: {
        "ok": True, "data": {"source": {"uri": "s3://example/qdata/clean/krx_etf.parquet"}},
    })
    complete_with_tool(company, "collision-first", "owner-one")
    with pytest.raises(PolicyError, match="identifier collision"):
        complete_with_tool(company, "collision-second", "owner-two")
    with company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM sources WHERE id LIKE 'lake:%'").fetchone()["n"] == 1
