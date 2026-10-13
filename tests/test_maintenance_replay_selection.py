"""Real PostgreSQL replay-selection controls; model responses remain explicit fixtures."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from psycopg.types.json import Jsonb

from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.maintenance.observation import MAX_REPLAY_REQUEST_BYTES, review_snapshot

from .conftest import queued_turns
from .test_maintenance_behavior import runner_with_recorded_requests


def test_large_and_research_requests_do_not_crowd_the_same_replay_sample(company):
    runner_with_recorded_requests(company)
    valid = []
    excluded = []
    for index in range(11):
        owner = "UOTHER" if index == 10 else "UHUMAN"
        project = company.ingest(event_key=str(uuid4()), text="Recorded synthetic selector control",
                                 owner=owner, channel="CQUANT", thread_ts=str(uuid4()))
        turn = queued_turns(company, project["project_id"])[0]
        company.prepare_turn(turn)
        company.commit_turn(turn, ProviderResponse(request_id=turn, provider="fixture",
                            decision=AgentDecision(say="Synthetic recorded response", status="complete")))
        (valid if index < 8 else excluded).append(turn)
        with company.db.transaction() as conn:
            if index == 8:
                request = conn.execute("SELECT request FROM turns WHERE id=%s", (turn,)).fetchone()["request"]
                request["prompt"] = "x" * (MAX_REPLAY_REQUEST_BYTES + 1)
                conn.execute("UPDATE turns SET request=%s WHERE id=%s", (Jsonb(request), turn))
            if index == 9:
                conn.execute("UPDATE tasks SET kind='research_stage' WHERE id=%s", (project["task_id"],))
    at = datetime.now(UTC)
    with company.db.transaction() as conn:
        conn.execute("UPDATE turns SET created_at=%s", (at-timedelta(days=1),))
        for index, turn in enumerate(valid):
            conn.execute("UPDATE turns SET created_at=%s WHERE id=%s", (at-timedelta(seconds=index+1),turn))
        for index, turn in enumerate(excluded):
            conn.execute("UPDATE turns SET created_at=%s WHERE id=%s", (at-timedelta(milliseconds=index+1),turn))
        review, inputs, _ = review_snapshot(conn, company, ["UHUMAN"], at)
    assert list(inputs) == ["turn:" + turn for turn in valid[:6]]
    assert review["sampling"]["replay_limit"] == 6 and review["sampling"]["replays_truncated"] is True
    assert review["sampling"]["replay_request_max_bytes"] == MAX_REPLAY_REQUEST_BYTES
    assert not set(inputs).intersection("turn:" + turn for turn in excluded)
