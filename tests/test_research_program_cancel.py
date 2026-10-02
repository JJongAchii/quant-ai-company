"""Real PostgreSQL and signed simulated Slack ingress; no live Slack or scientific execution."""

import hashlib
import hmac
import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from quant_company.api import create_app
from quant_company.research.approvals import short_command

from .test_research_exploration import prepare_exploratory_program
from .test_research_programs import program  # noqa: F401


def send(h, credentials, text, *, bad_signature=False, **changes):
    stamp = f"{time.time():.6f}"
    payload = {"type": "event_callback", "team_id": h.company.settings.slack_team_id,
               "api_app_id": credentials["director"]["app_id"], "event_id": str(uuid4()),
               "event": {"type": "message", "user": "UHUMAN", "channel": "CQUANT",
                         "thread_ts": "123.0", "ts": stamp, "text": text, **changes}}
    raw = json.dumps(payload).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(credentials["director"]["signing_secret"].encode(),
                         b"v0:" + timestamp.encode() + b":" + raw, hashlib.sha256).hexdigest()
    if bad_signature:
        signature = "0" * 64
    headers = {"content-type": "application/json", "x-slack-request-timestamp": timestamp,
               "x-slack-signature": "v0=" + signature}
    app = create_app(h.company.settings, h.company, credentials=credentials)
    with TestClient(app) as client:
        response = client.post("/slack/events/director", content=raw, headers=headers)
        repeated = client.post("/slack/events/director", content=raw, headers=headers)
    return response, repeated


def state(h, identity=None):
    with h.company.db.transaction() as conn:
        return h.program_store.snapshot(conn, identity or h.program_id)


def test_signed_cancel_selects_active_program_with_another_draft_and_is_idempotent(program, credentials):  # noqa: F811
    h = program
    old = h.program_id
    prepare_exploratory_program(h, cancel_prior=False, approve=False)
    response, repeated = send(h, credentials, f"연구 프로그램 취소 {old} {h.strict_digest}")
    assert response.status_code == repeated.status_code == 200
    assert repeated.json()["duplicate"]
    assert state(h, old)["state"] == "cancelled"
    assert state(h)["state"] == "draft"
    approved, _ = send(h, credentials, "승인")
    assert approved.status_code == 200
    assert state(h)["state"] == "active"
    with h.company.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM research_missions").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM events WHERE kind='research_program_cancel'").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM turns WHERE status='queued'").fetchone()["n"] == 0


@pytest.mark.parametrize("changes", [
    {"digest": "0" * 64}, {"identity": "00000000-0000-0000-0000-000000000000"},
    {"user": "UNAUTHORIZED"}, {"thread_ts": "999.0"}, {"bad_signature": True}, {"ts": "1.0"},
])
def test_cancel_rejects_wrong_target_or_unsigned_wrong_owner_thread(program, credentials, changes):  # noqa: F811
    h = program
    changes = dict(changes)
    digest = changes.pop("digest", h.digest)
    identity = changes.pop("identity", h.program_id)
    response, _ = send(h, credentials, f"연구 프로그램 취소 {identity} {digest}", **changes)
    assert response.status_code in {200, 401, 403}
    assert state(h)["state"] == "active"


def test_cancel_without_full_program_digest_cannot_route_to_a_model():
    assert short_command("연구 프로그램 취소") == {"action": "clarify"}
    assert short_command("연구 프로그램 취소 00000000-0000-0000-0000-000000000000 abc") == {"action": "clarify"}
