import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from html import escape
from pathlib import Path

import httpx
import pytest

from quant_company.api import create_app
from quant_company.cli import manifests
from quant_company.company import Company, load_roles
from quant_company.config import Settings
from quant_company.contracts import Role
from quant_company.news.feeds import fetch_feed
from quant_company.slack import SlackOutbox
from quant_company.tech_feed import schedule
from quant_company.tech_feed.contracts import TECH_FEED_AGENT, TechFeedSource, load_sources
from quant_company.tech_feed.feeds import MAX_FEED_BYTES, parse_feed, render
from quant_company.tech_feed.runner import TechFeedCollector
from quant_company.tech_feed.store import TechFeedStore

from .test_slack import event, signed


def spec(identity="example", **overrides):
    return TechFeedSource(id=identity, publisher=identity, topic="기술", feed_url="https://example.org/rss",
                          article_hosts=["example.org"], **overrides)


def rss(at, key="new", *, url=None, title="New technical release", body="A short publisher description."):
    url = url or f"https://example.org/{key}"
    return (f'<rss><channel><item><guid>{key}</guid><title>{escape(title)}</title>'
            f'<link>{escape(url)}</link><pubDate>{format_datetime(at)}</pubDate>'
            f'<description>{escape(body)}</description></item></channel></rss>').encode()


@pytest.fixture
def tech(company, tmp_path, monkeypatch):
    company.settings.tech_feed_enabled = True
    company.settings.tech_feed_publish_enabled = True
    company.settings.tech_feed_channel_id = "CTECH"
    company.settings.tech_feed_owner_user = "UHUMAN"
    company.settings.slack_allowed_channels.append("CTECH")
    company.settings.tech_feed_sources_file = tmp_path / "tech-sources.json"
    company.settings.tech_feed_sources_file.write_text(json.dumps([spec().model_dump()]))
    company.roles[TECH_FEED_AGENT] = Role(id=TECH_FEED_AGENT, name="Tech Scout", mission="Feed fixture",
                                          model="unused", instructions="No models", tools=[], can_delegate_to=[])
    clock = [datetime(2026, 9, 21, 3, tzinfo=UTC)]
    monkeypatch.setattr(schedule, "utcnow", lambda: clock[0])
    store = TechFeedStore(company)
    store.clock = clock
    return store


def ingest(store, raw=None, *, identity="example"):
    with store.db.transaction() as conn:
        conn.execute("UPDATE tech_feed_sources SET next_at=%s", (store.clock[0],))
    claimed = next(row for row in store.claim_sources() if row["id"] == identity)
    source = TechFeedSource.model_validate(claimed["config"])
    entries, skipped = parse_feed(raw or rss(store.clock[0]), source)
    return store.save(claimed, {"ok": True, "entries": entries, "skipped": skipped})


def queue(store, key="new"):
    ingest(store, rss(store.clock[0], "baseline"))
    return ingest(store, rss(store.clock[0], key))


def due(store):
    with store.db.transaction() as conn:
        conn.execute("UPDATE outbox SET next_at=now()-interval '1 second' WHERE status='pending'")


def outgoing(store):
    with store.db.transaction() as conn:
        return conn.execute("SELECT * FROM outbox ORDER BY created_at,id").fetchall()


def tech_scout_credentials(credentials):
    return {**credentials, TECH_FEED_AGENT: {"app_id": "ATECHSCOUT", "bot_user_id": "UBOTTECHSCOUT",
                                             "bot_token": "fixture-tech-scout",
                                             "signing_secret": "fixture-signing"}}


def test_catalog_and_source_filters():
    sources = {s.id: s for s in load_sources()}
    assert len(sources) == 12 and all(s.enabled for s in sources.values())
    assert sources["openai"].selects("https://openai.com/index/release", "Release", ["Research"])
    assert not sources["openai"].selects("https://openai.com/index/company", "Company", ["Company"])
    d2 = sources["naver-d2"]
    assert d2.selects("https://d2.naver.com/helloworld/1", "Technical article", [])
    assert d2.selects("https://d2.naver.com/news/1", "FE News 이번 달", [])
    assert not d2.selects("https://d2.naver.com/news/1", "Company event", [])


def test_atom_content_updated_date_and_plain_text_render():
    raw = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>stable-id</id>
      <title>Technical release</title><link rel="alternate" href="https://example.org/post"/>
      <updated>2026-09-21T01:00:00Z</updated><content type="html">&lt;p&gt;Source text&lt;/p&gt;</content>
      </entry></feed>'''
    entries, skipped = parse_feed(raw, spec())
    assert skipped == 0 and entries[0]["guid"] == "stable-id"
    assert entries[0]["description"] == "Source text" and entries[0]["time_kind"] == "updated"
    assert "수정 09-21 10:00 KST" in render(entries[0], spec())
    assert "발행" not in render(entries[0], spec())


def test_description_is_optional_bounded_and_never_invented():
    at = datetime.now(UTC)
    assert parse_feed(rss(at, body=""), spec())[0][0]["description"] == ""
    entry = parse_feed(rss(at, body="a" * 1000), spec())[0][0]
    assert len(entry["description"]) == 160 and entry["description"].endswith("…")
    assert parse_feed(rss(at, url="https://evil.example.net/post"), spec()) == ([], 1)


def test_markup_mentions_and_tracking_are_sanitized():
    entries, _ = parse_feed(rss(datetime.now(UTC), url="https://example.org/post?utm_source=rss&v=1",
                                title="Release <!channel> & <@UANY>", body="<b>Ready</b> <!here>"), spec())
    text = render(entries[0], spec())
    assert "<!channel>" not in text and "<@UANY>" not in text and "<!here>" not in text
    assert entries[0]["url"] == "https://example.org/post?v=1"
    assert "Ready" in text


@pytest.mark.parametrize("raw", [b'<!DOCTYPE rss [<!ENTITY x "boom">]><rss/>', b'<html/>', b'\0<rss/>',
                                  b'x' * (MAX_FEED_BYTES + 1)])
def test_unsafe_feed_is_rejected(raw):
    with pytest.raises(ValueError):
        parse_feed(raw, spec())


def test_code_example_doctype_in_cdata_is_not_an_xml_declaration():
    raw = b'''<rss><channel><item><guid>x</guid><title>HTML tooling</title>
    <link>https://example.org/x</link><description><![CDATA[Example: <!DOCTYPE html>]]></description>
    </item></channel></rss>'''
    assert len(parse_feed(raw, spec())[0]) == 1


def test_transport_reuses_conditional_get_and_checks_redirect_hosts():
    sent = []

    class Response:
        status = 304

        def getheader(self, key, default=None):
            return "https://other.example.org/rss" if key == "Location" else default

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, path, headers):
            sent.append(headers)

        def getresponse(self):
            return Response()

        def close(self):
            pass

    assert fetch_feed(spec(), "etag", connection_factory=Connection, parser=parse_feed)["not_modified"]
    assert sent[0]["If-None-Match"] == "etag"
    Response.status = 302
    assert not fetch_feed(spec(), connection_factory=Connection, parser=parse_feed)["ok"]


def test_baseline_duplicates_edits_and_old_entries_do_not_repost(tech):
    assert ingest(tech)["state"] == "baselined"
    assert outgoing(tech) == []
    ingest(tech, rss(tech.clock[0], body="Reworded description"))
    ingest(tech, rss(tech.clock[0] - timedelta(days=4), "old"))
    ingest(tech, rss(tech.clock[0] + timedelta(hours=1), "future"))
    assert outgoing(tech) == []
    assert ingest(tech, rss(tech.clock[0], "newer"))["queued"] == 1
    assert ingest(tech, rss(tech.clock[0], "newer", url="https://example.org/newer?utm_source=again"))["queued"] == 0
    assert len(outgoing(tech)) == 1


def test_same_url_in_two_feeds_has_one_publication(tech):
    tech.company.settings.tech_feed_sources_file.write_text(json.dumps([spec().model_dump(), spec("second").model_dump()]))
    claimed = tech.claim_sources()
    for row in claimed:
        tech.save(row, {"ok": True, "entries": []})
    # Only one feed is due at a time in this test; normal collection claims all due feeds.
    for identity in ("example", "second"):
        with tech.db.transaction() as conn:
            conn.execute("UPDATE tech_feed_sources SET next_at=%s WHERE id=%s", (tech.clock[0], identity))
        row = tech.claim_sources()[0]
        entries, _ = parse_feed(rss(tech.clock[0]), spec(identity))
        tech.save(row, {"ok": True, "entries": entries})
    assert len(outgoing(tech)) == 1


def test_preview_items_never_replay_after_publish_enable(tech):
    tech.company.settings.tech_feed_publish_enabled = False
    queue(tech)
    assert outgoing(tech) == []
    tech.company.settings.tech_feed_publish_enabled = True
    ingest(tech)
    assert outgoing(tech) == []
    assert ingest(tech, rss(tech.clock[0], "later"))["queued"] == 1


def test_failed_initial_fetch_does_not_establish_baseline_and_old_lease_cannot_commit(tech):
    first = tech.claim_sources()[0]
    tech.save(first, {"ok": False, "error": "http_status", "http_status": 503})
    with tech.db.transaction() as conn:
        source = conn.execute("SELECT * FROM tech_feed_sources").fetchone()
    assert source["initialized_at"] is None and source["failures"] == 1
    assert source["next_at"] > tech.clock[0]
    tech.clock[0] += timedelta(hours=1)
    second = tech.claim_sources()[0]
    assert tech.save(first, {"ok": True, "entries": []})["state"] == "stale"
    assert tech.save(second, {"ok": True, "entries": []})["state"] == "baselined"


def test_policy_change_during_collection_drops_obsolete_response(tech):
    row = tech.claim_sources()[0]
    tech.company.settings.tech_feed_sources_file.write_text(json.dumps([spec(enabled=False).model_dump()]))
    assert tech.save(row, {"ok": True, "entries": []})["state"] == "stale"
    assert outgoing(tech) == []


def test_tech_feed_cannot_replace_hot_news_channel(tech):
    tech.company.settings.company_news_enabled = True
    tech.company.settings.news_channel_id = tech.company.settings.tech_feed_channel_id
    assert not tech.authorized()


async def test_end_to_end_has_zero_model_calls_tasks_turns_and_usage(tech, credentials, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("The tech feed must never construct or call a model provider")

    monkeypatch.setattr("quant_company.execution.provider_for", forbidden)
    body = [rss(tech.clock[0], "baseline")]
    collector = TechFeedCollector(tech.company, lambda source, *_: {"ok": True, "entries": parse_feed(body[0], source)[0]})
    await collector.tick()
    tech.clock[0] += timedelta(minutes=10)
    body[0] = rss(tech.clock[0], "new")
    await collector.tick()
    captured = []

    def deliver(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "ts": "200.123"})

    due(tech)
    outbox = SlackOutbox(tech.company, tech_scout_credentials(credentials), httpx.MockTransport(deliver))
    assert await outbox.send_one()
    assert captured[0]["channel"] == "CTECH" and "thread_ts" not in captured[0]
    assert not captured[0]["unfurl_links"] and "지시 v" not in captured[0]["text"]
    assert captured[0]["client_msg_id"] == str(outgoing(tech)[0]["id"])
    assert outgoing(tech)[0]["status"] == "delivered" and outgoing(tech)[0]["agent"] == TECH_FEED_AGENT
    with tech.db.transaction() as conn:
        for table in ("tasks", "turns", "daily_usage", "news_reviews", "news_searches"):
            assert conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0


@pytest.mark.parametrize("hour,minute,allowed", [(23, 59, True), (0, 0, False), (5, 59, False), (6, 0, True)])
async def test_kst_boundary(tech, credentials, hour, minute, allowed):
    queue(tech)
    kst = datetime(2026, 9, 22, hour, minute, tzinfo=schedule.ZoneInfo("Asia/Seoul"))
    tech.clock[0] = kst.astimezone(UTC)
    due(tech)
    sent = []

    def deliver(request):
        sent.append(request)
        return httpx.Response(200, json={"ok": True, "ts": "1.2"})

    outbox = SlackOutbox(tech.company, tech_scout_credentials(credentials), httpx.MockTransport(deliver))
    assert bool(await outbox.send_one()) is allowed
    assert bool(sent) is allowed


def test_midnight_crossing_after_claim_is_deferred(tech, credentials):
    queue(tech)
    tech.clock[0] = datetime(2026, 9, 21, 14, 59, 59, tzinfo=UTC)
    due(tech)
    outbox = SlackOutbox(tech.company, tech_scout_credentials(credentials))
    row = outbox.claim()
    assert row is not None
    tech.clock[0] += timedelta(seconds=1)
    assert not outbox.before_send(row)
    saved = outgoing(tech)[0]
    assert saved["status"] == "pending" and saved["attempts"] == 0
    assert saved["next_at"] == datetime(2026, 9, 21, 21, tzinfo=UTC)


def test_two_dispatchers_cannot_burst_and_restart_preserves_spacing(tech, credentials):
    queue(tech)
    ingest(tech, rss(tech.clock[0], "second"))
    due(tech)
    outbox = SlackOutbox(tech.company, tech_scout_credentials(credentials))
    first, second = outbox.claim(), outbox.claim()
    assert first and second
    assert outbox.before_send(first)
    restarted = SlackOutbox(tech.company, tech_scout_credentials(credentials))
    assert not restarted.before_send(second)
    assert any(r["next_at"] == tech.clock[0] + timedelta(minutes=1) for r in outgoing(tech))


@pytest.mark.parametrize("mutation", ["publish", "enabled", "source", "channel", "owner", "expired"])
def test_policy_and_expiry_rechecked_immediately_before_send(tech, credentials, mutation):
    queue(tech)
    due(tech)
    outbox = SlackOutbox(tech.company, tech_scout_credentials(credentials))
    row = outbox.claim()
    if mutation == "publish":
        tech.company.settings.tech_feed_publish_enabled = False
    elif mutation == "enabled":
        tech.company.settings.tech_feed_enabled = False
    elif mutation == "source":
        tech.company.settings.tech_feed_sources_file.write_text(json.dumps([spec(enabled=False).model_dump()]))
    elif mutation == "channel":
        tech.company.settings.slack_allowed_channels = []
    elif mutation == "owner":
        tech.company.settings.slack_allowed_users = []
    else:
        tech.clock[0] += timedelta(days=4)
    assert not outbox.before_send(row)
    assert outgoing(tech)[0]["status"] == "stale"


@pytest.mark.parametrize("outcome", ["timeout", "server_error", "missing_ts"])
async def test_ambiguous_slack_result_is_preserved_not_replayed(tech, credentials, outcome):
    queue(tech)
    due(tech)
    calls = []

    def deliver(request):
        calls.append(request)
        if outcome == "timeout":
            raise httpx.ReadTimeout("Response lost", request=request)
        if outcome == "server_error":
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    outbox = SlackOutbox(tech.company, tech_scout_credentials(credentials), httpx.MockTransport(deliver))
    await outbox.send_one()
    tech.clock[0] += timedelta(minutes=10)
    await outbox.send_one()
    ingest(tech)
    assert len(calls) == 1 and outgoing(tech)[0]["status"] == "uncertain"


async def test_slack_rate_limit_retries_same_id_after_delay(tech, credentials):
    queue(tech)
    due(tech)
    calls = []

    def deliver(request):
        calls.append(json.loads(request.content))
        return (httpx.Response(429, headers={"Retry-After": "90"}) if len(calls) == 1
                else httpx.Response(200, json={"ok": True, "ts": "1.2"}))

    outbox = SlackOutbox(tech.company, tech_scout_credentials(credentials), httpx.MockTransport(deliver))
    await outbox.send_one()
    assert outgoing(tech)[0]["status"] == "pending"
    tech.clock[0] += timedelta(seconds=90)
    due(tech)
    await outbox.send_one()
    assert calls[0]["client_msg_id"] == calls[1]["client_msg_id"]
    assert outgoing(tech)[0]["status"] == "delivered"


def test_tech_scout_events_never_create_model_work(tech, credentials):
    from fastapi.testclient import TestClient

    credentials = tech_scout_credentials(credentials)
    client = TestClient(create_app(tech.company.settings, tech.company, credentials))
    for extra in ({}, {"type": "message", "thread_ts": "1.2"}, {"bot_id": "BREPORTER"}):
        payload = event(credentials, role=TECH_FEED_AGENT, channel="CTECH",
                        text="<@UBOTTECHSCOUT> explain", **extra)
        body, headers = signed(payload, credentials[TECH_FEED_AGENT])
        result = client.post("/slack/events/tech_scout", content=body, headers=headers).json()
        assert result == {"ok": True, "ignored": True,
                          "reason": "tech_feed_delivery_identity_is_not_interactive"}
    with tech.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM turns").fetchone()["n"] == 0


def test_packaged_tech_scout_is_inactive_and_manifest_has_only_outbound_permission(tmp_path):
    roles = load_roles(Settings(tech_feed_enabled=True))
    assert not roles[TECH_FEED_AGENT].active
    assert not roles["reporter"].active
    assert load_roles(Settings(company_news_enabled=True))["reporter"].active
    runtime = Company(Settings(), roles).runtime_context()
    assert TECH_FEED_AGENT not in {employee["id"] for employee in runtime["employees"]}
    manifests(type("Roster", (), {"roles": roles})(), None, tmp_path, include_tech_scout=True)
    app = json.loads((tmp_path / "tech_scout.json").read_text())
    assert app["display_information"]["name"] == "Tech Scout"
    assert app["features"]["bot_user"]["display_name"] == "tech-scout"
    assert not app["features"]["bot_user"]["always_online"]
    assert app["oauth_config"]["scopes"]["bot"] == ["chat:write"]
    assert "event_subscriptions" not in app["settings"] and not app["settings"]["socket_mode_enabled"]
    assert app == json.loads(Path("slack-apps/tech_scout.json").read_text())


def test_tech_feed_fails_closed_if_delivery_identity_becomes_interactive(tech):
    tech.company.roles[TECH_FEED_AGENT] = tech.company.roles[TECH_FEED_AGENT].model_copy(update={"active": True})
    assert not tech.authorized()


def test_migration_moves_only_pending_tech_feed_delivery_off_reporter(tech):
    queue(tech)
    ingest(tech, rss(tech.clock[0], "second"))
    delivered, pending = outgoing(tech)
    with tech.db.transaction() as conn:
        conn.execute("UPDATE messages SET author='reporter' WHERE id IN (%s,%s)",
                     (delivered["id"], pending["id"]))
        conn.execute("UPDATE outbox SET agent='reporter' WHERE id IN (%s,%s)",
                     (delivered["id"], pending["id"]))
        conn.execute("UPDATE outbox SET status='delivered' WHERE id=%s", (delivered["id"],))
    tech.db.migrate()
    with tech.db.transaction() as conn:
        rows = conn.execute("""SELECT m.author,o.agent,o.status FROM messages m
            JOIN outbox o ON o.id=m.id WHERE m.id IN (%s,%s) ORDER BY o.status""",
                            (delivered["id"], pending["id"])).fetchall()
    assert rows == [
        {"author": "reporter", "agent": "reporter", "status": "delivered"},
        {"author": TECH_FEED_AGENT, "agent": TECH_FEED_AGENT, "status": "pending"},
    ]
