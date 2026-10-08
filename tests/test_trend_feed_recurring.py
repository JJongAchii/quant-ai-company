# ruff: noqa: F811
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from quant_company.api import create_app
from quant_company.slack import SlackIngress, SlackOutbox
from quant_company.socket_mode import accept_envelope, socket_main
from quant_company.trend_feed import schedule
from quant_company.trend_feed.runner import TrendFeedCollector

from .test_slack import signed
from .test_tech_feed import queue as queue_tech
from .test_tech_feed import tech  # noqa: F401
from .test_trend_feed import FixtureNaver, force_due, ingest, outgoing, response, trend  # noqa: F401


def recurring(store):
    store.company.settings.trend_feed_publication_hours = [8, 14, 20]
    store.company.settings.trend_feed_on_demand_enabled = True
    store.company.settings.trend_feed_model_daily_limit = 12


def clock(store, hour, minute=0):
    store.clock[0] = datetime(2026, 10, 7, hour, minute, tzinfo=schedule.KST).astimezone(UTC)


def inbox(store):
    credential = {"app_id": "ATREND", "bot_user_id": "UBOTTREND", "bot_token": "synthetic",
                  "signing_secret": "synthetic-signing-secret"}
    return TestClient(create_app(store.company.settings, store.company, {"trend_scout": credential})), credential


def payload(**changes):
    event = {"type": "message", "user": "UHUMAN", "channel": "CTRENDS", "ts": "200.001",
             "channel_type": "channel", "text": "실시간 검색어"}
    event.update(changes)
    return {"type": "event_callback", "team_id": "TTEST", "api_app_id": "ATREND",
            "event_id": "ETREND", "event": event}


def post(client, credential, body):
    raw, headers = signed(body, credential)
    return client.post("/slack/events/trend_scout", content=raw, headers=headers)


async def complete_request(store, event_key, *, finalize=True):
    requested = store.request(event_key, owner="UHUMAN", channel="CTRENDS", thread_ts=event_key)
    ingest(store, title="새로운 기술 공개")
    store.clock[0] += timedelta(seconds=1)
    collector = TrendFeedCollector(store.company, naver=FixtureNaver(), reader=lambda *_: {"ok": False})
    await collector.enrich(store.claim_enrichment())
    call = store.prepare_call()
    with store.db.transaction() as conn:
        row = conn.execute("SELECT * FROM trend_feed_digests WHERE id=%s", (requested["id"],)).fetchone()
    store.finish_call(call, response(call["id"], row["bundle"]))
    if finalize:
        assert store.finalize()["state"] == "queued"
    return requested, call


def test_three_daily_slots_keep_morning_id_and_commit_independently(trend):
    recurring(trend)
    identities = []
    for hour in (8, 14, 20):
        clock(trend, hour-1, 20)
        ingest(trend, title=f"{hour}시의 최신 기술")
        clock(trend, hour-1, 30)
        frozen = trend.freeze()
        identities.append(frozen["id"])
        assert frozen["bundle"]["candidates"][0]["title"] == f"{hour}시의 최신 기술"
        assert frozen["bundle"]["candidate_scope"] == "latest_rss"
        clock(trend, hour)
        assert trend.finalize()["state"] == "queued"
        assert trend.finalize()["state"] == "queued"
    assert len(set(identities)) == len(outgoing(trend)) == 3
    assert trend.status()["next_publication"].startswith("2026-10-08 08:00")


def test_signed_owner_request_and_mention_replay_share_one_durable_id(trend):
    recurring(trend)
    clock(trend, 10)
    client, credential = inbox(trend)
    accepted = post(client, credential, payload()).json()
    assert accepted["trend_request"] and not accepted["cached"]
    replay = post(client, credential, payload(type="app_mention", text="<@UBOTTREND> 실시간 검색어")).json()
    assert replay["duplicate"] and replay["id"] == accepted["id"]
    assert not outgoing(trend) and not trend.prepare_call()
    with trend.db.transaction() as conn:
        row = conn.execute("SELECT * FROM trend_feed_digests WHERE event_key IS NOT NULL").fetchone()
        assert row["thread_ts"] == "200.001" and row["kind"] == "on_demand"
        assert conn.execute("SELECT count(*) AS n FROM trend_feed_digests WHERE event_key IS NOT NULL").fetchone()["n"] == 1


@pytest.mark.parametrize("change", [{"user": "UOTHER"}, {"channel": "CQUANT"}, {"bot_id": "BBOT"},
                                   {"subtype": "message_changed"}, {"text": "<@UOTHER> 실시간 검색어"},
                                   {"text": "발송 시간을 바꿔줘"}])
def test_request_scope_blocks_untrusted_or_unrelated_events(trend, change):
    recurring(trend)
    client, credential = inbox(trend)
    assert post(client, credential, payload(**change)).json()["ignored"]
    with trend.db.transaction() as conn:
        assert not conn.execute("SELECT 1 FROM trend_feed_digests WHERE event_key IS NOT NULL").fetchone()


def test_request_rejects_bad_signature_and_workspace(trend):
    recurring(trend)
    client, credential = inbox(trend)
    raw, headers = signed(payload(), credential)
    headers["x-slack-signature"] = "v0:invalid"
    assert client.post("/slack/events/trend_scout", content=raw, headers=headers).status_code == 401
    wrong = {**payload(), "team_id": "TOTHER"}
    assert post(client, credential, wrong).status_code == 409


def test_dedicated_request_policy_survives_other_process_channel_differences(trend):
    recurring(trend)
    policy = trend.policy()
    # Existing API/socket processes need not have another feed's channel permission.
    trend.company.settings.slack_allowed_channels.append("COTHERFEED")
    assert trend.policy() == policy and trend.authorized()
    trend.company.settings.slack_allowed_channels.remove("CTRENDS")
    assert trend.policy() != policy and not trend.authorized()


async def test_on_demand_refresh_filters_out_disappeared_keywords_and_reuses_verified_draft(trend):
    recurring(trend)
    clock(trend, 9, 50)
    ingest(trend, title="오래된 관심 주제", traffic="500,000+")
    clock(trend, 10)
    requested, call = await complete_request(trend, "first-request")
    first = outgoing(trend)[0]
    assert first["thread_ts"] == "first-request" and "요청 브리핑" in first["text"]
    assert "새로운 기술 공개" in first["text"] and "오래된 관심 주제" not in first["text"]
    assert trend.company.settings.trend_feed_on_demand_enabled
    # Request replies are immediately eligible, while their edit deadline is still in the future.
    with trend.db.transaction() as conn:
        assert trend.gate(conn, first)
    trend.clock[0] += timedelta(minutes=1)
    cached = trend.request("second-request", owner="UHUMAN", channel="CTRENDS", thread_ts="second-request")
    assert cached["cached"] and cached["id"] != requested["id"]
    assert trend.finalize()["state"] == "queued"
    assert len(outgoing(trend)) == 2 and len(trend.status()["model_calls"]) == 1
    assert "스포츠 주제 제외" in outgoing(trend)[1]["text"]
    assert trend.status()["model_calls"][0]["id"] == call["id"]


def test_owner_request_during_an_active_fetch_keeps_the_refresh_due(trend):
    recurring(trend)
    clock(trend, 10)
    with trend.db.transaction() as conn:
        conn.execute("UPDATE trend_feed_source SET next_at=%s", (trend.clock[0],))
    claimed = trend.claim_source()
    trend.clock[0] += timedelta(seconds=1)
    trend.request("during-fetch", owner="UHUMAN", channel="CTRENDS", thread_ts="during-fetch")
    trend.save_snapshot(claimed, {"ok": True, "entries": []})
    # The fetch started before the owner's request, so enrichment waits for the next fetch.
    assert trend.claim_enrichment() is None
    next_fetch = trend.claim_source()
    assert next_fetch is not None
    trend.save_snapshot(next_fetch, {"ok": True, "entries": []})
    trend.clock[0] += timedelta(seconds=1)
    assert trend.claim_enrichment()["event_key"] == "during-fetch"


async def test_on_demand_cannot_consume_regular_publication_reserve(trend):
    recurring(trend)
    clock(trend, 10)
    for number in range(6):
        await complete_request(trend, f"request-{number}")
        trend.clock[0] += timedelta(minutes=11)
    requested = trend.request("request-over-budget", owner="UHUMAN", channel="CTRENDS", thread_ts="limit")
    ingest(trend)
    trend.clock[0] += timedelta(seconds=1)
    collector = TrendFeedCollector(trend.company, naver=FixtureNaver(), reader=lambda *_: {"ok": False})
    await collector.enrich(trend.claim_enrichment())
    assert trend.prepare_call() is None
    with trend.db.transaction() as conn:
        notice = conn.execute("SELECT bundle FROM trend_feed_digests WHERE id=%s", (requested["id"],)).fetchone()
        assert "편집 한도" in notice["bundle"]["editorial_notice"]
    clock(trend, 13, 20)
    ingest(trend)
    clock(trend, 13, 30)
    await collector.enrich(trend.claim_enrichment())
    assert trend.prepare_call() is not None


async def test_same_day_naver_cache_keeps_original_response_without_another_http_call(trend):
    recurring(trend)
    naver = FixtureNaver()
    collector = TrendFeedCollector(trend.company, naver=naver, reader=lambda *_: {"ok": False})
    for hour in (8, 14):
        clock(trend, hour-1, 20)
        ingest(trend)
        clock(trend, hour-1, 30)
        await collector.enrich(trend.claim_enrichment())
    assert sum(kind == "trend" for kind, _ in naver.calls) == 1
    with trend.db.transaction() as conn:
        rows = conn.execute("SELECT * FROM trend_feed_digests ORDER BY send_at").fetchall()
        assert rows[0]["bundle"]["candidates"][0]["naver"] == rows[1]["bundle"]["candidates"][0]["naver"]
        assert conn.execute("SELECT 1 FROM trend_feed_api_receipts WHERE receipt ? 'cache_source_id'").fetchone()


async def test_request_delivery_policy_change_blocks_the_outbox(trend):
    recurring(trend)
    clock(trend, 10)
    await complete_request(trend, "request-policy")
    first = outgoing(trend)[0]
    trend.company.settings.trend_feed_on_demand_enabled = False
    with trend.db.transaction() as conn:
        assert not trend.gate(conn, first)
    assert outgoing(trend)[0]["status"] == "stale"


def paced_backlog(tech, count):
    for number in range(count):
        queue_tech(tech, f"paced-{number}")
    with tech.db.transaction() as conn:
        conn.execute("""UPDATE outbox SET created_at=now()-interval '2 days',next_at=now()-interval '1 second'
            WHERE agent='tech_scout'""")
        conn.execute("""INSERT INTO tech_feed_delivery(channel,next_at) VALUES('CTECH',now()+interval '5 minutes')
            ON CONFLICT(channel) DO UPDATE SET next_at=excluded.next_at""")


def sender(store):
    return SlackOutbox(store.company, {
        "trend_scout": {"bot_token": "synthetic-trend"},
        "tech_scout": {"bot_token": "synthetic-tech"},
    })


async def test_ready_owner_request_overtakes_older_paced_broadcasts(trend, tech):
    recurring(trend)
    with trend.db.transaction() as conn:
        at = conn.execute("SELECT now() AS at").fetchone()["at"]
    # Keep scheduled enrichment outside this on-demand delivery regression.
    trend.clock[0] = tech.clock[0] = at.astimezone(schedule.KST).replace(
        hour=10, minute=0, second=0, microsecond=0).astimezone(UTC)
    paced_backlog(tech, 2)
    requested, _ = await complete_request(trend, "urgent-owner-request")
    force_due(trend)
    row = sender(trend).claim()
    assert row["id"] == requested["id"] and row["thread_ts"] == "urgent-owner-request"
    with trend.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox WHERE agent='tech_scout' AND status='pending' AND attempts=0").fetchone()["n"] == 2


def test_paced_backlog_is_skipped_with_bounded_work_and_without_a_feed_burst(trend, tech):
    with trend.db.transaction() as conn:
        at = conn.execute("SELECT now() AS at").fetchone()["at"]
    trend.clock[0] = at.astimezone(schedule.KST).replace(hour=7, minute=20, second=0, microsecond=0).astimezone(UTC)
    tech.clock[0] = trend.clock[0]
    paced_backlog(tech, 40)
    ingest(trend)
    trend.clock[0] += timedelta(minutes=10)
    frozen = trend.freeze()
    trend.clock[0] += timedelta(minutes=30)
    assert trend.finalize()["state"] == "queued"
    force_due(trend)
    box = sender(trend)
    assert box.claim() is None  # Work is bounded even with more than one tick's deferrals.
    with trend.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox WHERE agent='tech_scout' AND next_at>now()").fetchone()["n"] == 32
    row = box.claim()
    assert row["id"] == str(frozen["id"]) and row["message_kind"] == "trend_feed"
    with trend.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox WHERE agent='tech_scout' AND status='pending' AND attempts=0").fetchone()["n"] == 40


async def test_authenticated_socket_request_ack_and_reconnect_keep_the_same_commit(trend):
    recurring(trend)
    client, credential = inbox(trend)
    ingress = SlackIngress(trend.company.settings, trend.company, {"trend_scout": credential})
    acked = []

    class Socket:
        async def send_socket_mode_response(self, response):
            with trend.db.transaction() as conn:
                assert conn.execute("SELECT count(*) AS n FROM trend_feed_digests WHERE event_key IS NOT NULL").fetchone()["n"] == 1
            acked.append(response.envelope_id)

    request = SimpleNamespace(type="events_api", envelope_id="trend-envelope", payload=payload())
    await accept_envelope(ingress, "trend_scout", Socket(), request)
    await accept_envelope(ingress, "trend_scout", Socket(), request)
    assert acked == ["trend-envelope", "trend-envelope"]
    assert not outgoing(trend)


async def test_socket_connects_the_command_identity_without_enabling_model_permissions(trend):
    import asyncio

    recurring(trend)
    for role in trend.company.roles.values():
        role.active = False
    credential = {"app_id": "ATREND", "bot_user_id": "UBOTTREND", "bot_token": "synthetic", "app_token": "xapp-synthetic"}
    clients = []

    class Socket:
        def __init__(self, **kwargs):
            self.socket_mode_request_listeners = []
            clients.append(self)

        async def connect(self):
            pass

        async def close(self):
            pass

    stop = asyncio.Event()
    stop.set()
    await socket_main(trend.company.settings, company=trend.company, credentials={"trend_scout": credential},
                      client_factory=Socket, stop=stop)
    assert len(clients) == 1
    scout = trend.company.roles["trend_scout"]
    assert not scout.active and not scout.tools and not scout.can_delegate_to
