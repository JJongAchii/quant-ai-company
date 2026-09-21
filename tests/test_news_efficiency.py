"""Real PostgreSQL; synthetic articles/model replies and simulated Slack only."""

import json
from datetime import UTC, timedelta

import httpx
import pytest

from quant_company.contracts import AgentDecision, ProviderResponse
from quant_company.news import schedule
from quant_company.news.digest import NewsDigestStore
from quant_company.news.discovery import NewsDiscoveryStore
from quant_company.news.runner import NewsEditor
from quant_company.news.screening import NewsScreeningStore
from quant_company.slack import SlackIngress, SlackOutbox
from tests.test_news import CONTENT, ready_article, reply
from tests.test_news import news as news_fixture

news = news_fixture


@pytest.fixture
def efficient(news, monkeypatch):
    news.company.settings.news_optimization_enabled = True
    news.company.settings.news_delivery_window_enabled = True
    clock = [schedule.opening().astimezone(UTC)]
    monkeypatch.setattr(schedule, "utcnow", lambda: clock[0])
    return news, clock


def overnight(store, **kwargs):
    article = ready_article(store, **kwargs)
    with store.db.transaction() as conn:
        conn.execute("UPDATE news_articles SET published_at=%s,collected_at=%s WHERE id=%s",
                     (schedule.opening()-timedelta(hours=1), schedule.opening()-timedelta(hours=1), article["id"]))
    return article


def screen_reply(request, *, keep=True, related=None):
    bundle = json.loads(request["prompt"].split("SCREEN DATA JSON:\n")[1])
    items = [{"article_id": a["id"], "disposition": "keep" if keep else "ignore", "importance": 4,
              "reason": "잠재적으로 중요한 정책 결정", "related_ids": related or []} for a in bundle["articles"]]
    return ProviderResponse(request_id=request["request_id"], provider="fixture", decision=AgentDecision(
        say="", status="complete", artifacts=[{"title": "Screen", "content": json.dumps({"items": items}), "source_ids": []}]))


def select(store, **kwargs):
    screen = NewsScreeningStore(store.company)
    request = screen.prepare()["request"]
    assert request["model"] == "gpt-5.6-luna" and request["reasoning_effort"] == "low"
    result = screen.commit(screen_reply(request, **kwargs))
    return request, result


def publisher(store, transport):
    return SlackOutbox(store.company, {"reporter": {"bot_token": "fake"}}, httpx.MockTransport(transport))


def due(store):
    with store.db.transaction() as conn:
        conn.execute("UPDATE outbox SET next_at=now() WHERE status='pending'")


@pytest.mark.parametrize(("hour", "minute", "expected"), [(0, 0, True), (5, 59, True), (6, 0, False), (23, 59, False)])
def test_kst_boundaries(efficient, hour, minute, expected):
    store, clock = efficient
    clock[0] = schedule.opening().replace(hour=hour, minute=minute)
    assert bool(schedule.quiet(store.company.settings)) is expected


def test_screen_recovery_coverage_and_related_context(efficient):
    store, _ = efficient
    held = overnight(store, suffix="held")
    with store.db.transaction() as conn:
        conn.execute("UPDATE news_articles SET state='held',content=%s WHERE id=%s", (CONTENT*30, held["id"]))
    article = overnight(store, suffix="new")
    screen = NewsScreeningStore(store.company)
    request = screen.prepare()["request"]
    with store.db.transaction() as conn:
        conn.execute("UPDATE news_triages SET next_at=%s", (schedule.utcnow(),))
    assert NewsScreeningStore(store.company).prepare()["request"] == request
    bad = screen_reply(request, related=["invented"])
    with pytest.raises(ValueError, match="input_coverage"):
        screen.commit(bad)
    response = screen_reply(request, related=[held["id"]])
    assert screen.commit(response)["selected"] == 1
    assert screen.commit(response)["duplicate"]
    review = store.prepare_review()["request"]
    bundle = json.loads(review["prompt"].split("NEWS DATA JSON:\n")[1])
    assert set(a["id"] for a in bundle["articles"]) == {article["id"], held["id"]}
    assert max(len(a["content"]) for a in bundle["articles"]) <= 3000
    assert next(a for a in bundle["articles"] if a["id"] == held["id"])["excerpt_truncated"]
    assert bundle["morning_day"] == str(schedule.opening().date())
    assert review["model"] == "gpt-6-astra"


def test_unrelated_held_not_repeated_and_screen_cannot_publish(efficient):
    store, _ = efficient
    held = overnight(store, suffix="unrelated")
    with store.db.transaction() as conn:
        conn.execute("UPDATE news_articles SET state='held' WHERE id=%s", (held["id"],))
    article = overnight(store)
    select(store)
    with store.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0
    request = store.prepare_review()["request"]
    bundle = json.loads(request["prompt"].split("NEWS DATA JSON:\n")[1])
    assert [a["id"] for a in bundle["articles"]] == [article["id"]]


async def test_night_light_screen_only_and_hourly_day_search(efficient):
    store, clock = efficient
    overnight(store)
    clock[0] = schedule.opening()-timedelta(minutes=1)
    calls = []

    class Provider:
        async def run(self, request):
            calls.append(request)
            return screen_reply(request.model_dump())

    editor = NewsEditor(store.company, Provider())
    assert (await editor.tick())["selected"] == 1
    assert (await editor.tick())["state"] == "quiet"
    assert len(calls) == 1
    store.company.settings.company_web_enabled = store.company.settings.news_search_enabled = True
    discovery = NewsDiscoveryStore(store.company)
    assert discovery.prepare()["state"] == "quiet"
    clock[0] = schedule.opening()
    # No media in this fixture: the important boundary is enforced before preparing a request.
    assert discovery.prepare()["state"] == "idle"


def test_screen_unknown_result_owns_articles(efficient):
    store, _ = efficient
    article = overnight(store)
    screen = NewsScreeningStore(store.company)
    request = screen.prepare()["request"]
    screen.screen_fault(request["request_id"], "delivery_outcome_unknown")
    assert screen.prepare()["state"] == "idle"
    with store.db.transaction() as conn:
        assert conn.execute("SELECT state FROM news_articles WHERE id=%s", (article["id"],)).fetchone()["state"] == "screen_blocked"
        assert conn.execute("SELECT count(*) AS n FROM news_triages").fetchone()["n"] == 1


async def test_morning_digest_one_receipt_questions_and_followup(efficient):
    store, _ = efficient
    article = overnight(store)
    digest = NewsDigestStore(store.company)
    assert digest.flush()["state"] == "awaiting_review"
    select(store)
    request = store.prepare_review()["request"]
    assert digest.flush()["state"] == "awaiting_review"
    store.commit_review(reply(request))
    posts = []

    def slack(request):
        posts.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "ts": f"1000.{len(posts):06d}"})

    sender = publisher(store, slack)
    assert not await sender.send_one()  # individual publication waits for the collection
    assert digest.flush()["members"] == 1
    assert digest.flush()["state"] == "already_prepared"
    assert await sender.send_one()
    assert not await sender.send_one()
    assert len(posts) == 1 and "밤사이 주요 뉴스" in posts[0]["text"]
    with store.db.transaction() as conn:
        event = conn.execute("SELECT * FROM news_events").fetchone()
        member = conn.execute("SELECT o.*,p.digest_id FROM outbox o JOIN news_publications p ON p.id=o.id").fetchone()
        assert member["status"] == "delivered" and member["attempts"] == 0 and member["sent_ts"] == "1000.000001"
        project = conn.execute("SELECT * FROM projects WHERE thread_ts='1000.000001'").fetchone()
        assert conn.execute("SELECT count(*) AS n FROM sources WHERE project_id=%s", (project["id"],)).fetchone()["n"] == 1
    ingress = SlackIngress(store.company.settings, store.company,
                           {"reporter": {"bot_user_id": "UREPORTER", "app_id": "AREPORTER"}})
    outcome = ingress.accept("reporter", {"team_id": "TTEST", "api_app_id": "AREPORTER", "event": {"type": "message", "user": "UHUMAN",
        "channel": "CQUANT", "ts": "1001.0", "thread_ts": "1000.000001", "text": "이 뉴스의 근거를 알려줘"}}, {"app_id": "AREPORTER"})
    assert not outcome.get("ignored")
    # Clear the synthetic user task so the background editor may run.
    with store.db.transaction() as conn:
        conn.execute("UPDATE turns SET status='completed'")
        conn.execute("UPDATE news_articles SET state='selected' WHERE id=%s", (article["id"],))
    follow = store.prepare_review()["request"]
    store.commit_review(reply(follow, event_id=str(event["id"]), facts="중앙은행이 새 정책 경로를 밝혔습니다.", change="새로운 정책 경로"))
    digest.flush()
    due(store)
    assert await sender.send_one()
    assert posts[-1]["thread_ts"] == "1000.000001"


async def test_midnight_between_claim_and_http_preserves_message(efficient, monkeypatch):
    store, clock = efficient
    overnight(store)
    select(store)
    clock[0] = schedule.opening().replace(hour=23, minute=59, second=59)
    request = store.prepare_review()["request"]
    store.commit_review(reply(request))
    with store.db.transaction() as conn:
        conn.execute("UPDATE news_publications SET morning_day=NULL")
    sender = publisher(store, lambda _: pytest.fail("No HTTP request allowed after midnight"))
    claim = sender.claim

    def crossing_midnight():
        row = claim()
        assert row
        clock[0] += timedelta(seconds=2)
        return row

    monkeypatch.setattr(sender, "claim", crossing_midnight)
    assert not await sender.send_one()
    with store.db.transaction() as conn:
        row = conn.execute("SELECT * FROM outbox").fetchone()
        assert row["status"] == "pending" and row["attempts"] == 0
        assert row["next_at"] == schedule.opening()
        assert conn.execute("SELECT morning_day FROM news_publications").fetchone()["morning_day"] == schedule.opening().date()


@pytest.mark.parametrize("failure", ["network", "lease", "missing_ts"])
async def test_digest_ambiguous_delivery_never_replays_individually(efficient, failure):
    store, _ = efficient
    overnight(store)
    select(store)
    store.commit_review(reply(store.prepare_review()["request"]))
    digest = NewsDigestStore(store.company)
    digest.flush()

    def fail(request):
        if failure == "network":
            raise httpx.ReadTimeout("simulated", request=request)
        return httpx.Response(200, json={"ok": True})

    sender = publisher(store, fail)
    if failure == "lease":
        assert sender.claim()["message_kind"] == "news_digest"
        with store.db.transaction() as conn:
            conn.execute("UPDATE outbox SET started_at=now()-interval '3 minutes' WHERE status='sending'")
        sender.recover_uncertain()
    else:
        await sender.send_one()
    assert not await sender.send_one()
    assert digest.flush()["state"] == "already_prepared"
    with store.db.transaction() as conn:
        assert {r["status"] for r in conn.execute("SELECT status FROM outbox").fetchall()} == {"uncertain"}
        assert conn.execute("SELECT count(*) AS n FROM news_digests").fetchone()["n"] == 1


async def test_large_digest_uses_one_root_and_receipt_linked_replies(efficient):
    store, _ = efficient
    for index in range(3):
        overnight(store, suffix=f"story-{index}")
    select(store)
    request = store.prepare_review()["request"]
    response = reply(request)
    data = json.loads(response.decision.artifacts[0].content)
    ids = data["items"][0]["article_ids"]
    data["items"] = [{**data["items"][0], "article_ids": [identity], "facts": f"별도 정책 발표 {i}",
                      "evidence": [{"article_id": identity, "quote": CONTENT[:76]}]} for i, identity in enumerate(ids)]
    response.decision.artifacts[0].content = json.dumps(data)
    store.commit_review(response)
    with store.db.transaction() as conn:
        conn.execute("UPDATE outbox SET text=repeat('Synthetic long fixture. ',550)")
    digest = NewsDigestStore(store.company)
    assert digest.flush()["parts"] == 2
    calls = []

    def send(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "ts": f"2000.{len(calls):06d}"})

    sender = publisher(store, send)
    assert await sender.send_one()
    assert await sender.send_one()
    assert not await sender.send_one()
    assert "thread_ts" not in calls[0] and calls[1]["thread_ts"] == "2000.000001"
    with store.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM projects WHERE thread_ts IS NOT NULL").fetchone()["n"] == 1
        assert {r["status"] for r in conn.execute("SELECT status FROM outbox").fetchall()} == {"delivered"}


async def test_digest_rate_limit_retries_same_container_and_empty_night_is_silent(efficient):
    store, _ = efficient
    digest = NewsDigestStore(store.company)
    assert digest.flush()["state"] == "idle"
    overnight(store)
    select(store)
    store.commit_review(reply(store.prepare_review()["request"]))
    digest.flush()
    calls = []

    def send(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, json={"ok": True, "ts": "3000.1"})

    sender = publisher(store, send)
    assert await sender.send_one()
    with store.db.transaction() as conn:
        assert conn.execute("SELECT status FROM outbox o JOIN news_publications p ON p.id=o.id").fetchone()["status"] == "bundled"
    due(store)
    assert await sender.send_one()
    assert calls[0]["client_msg_id"] == calls[1]["client_msg_id"]


def test_optimized_search_model_interval_and_user_work_priority(efficient):
    from tests.test_news import source
    from tests.test_news_scope import register, search_reply

    store, clock = efficient
    register(store, [source(kind="media", allow_attributed_reporting=True)])
    store.company.settings.news_search_enabled = store.company.settings.company_web_enabled = True
    search = NewsDiscoveryStore(store.company)
    ready = search.prepare()["request"]
    assert ready["model"] == "gpt-5.6-luna" and ready["reasoning_effort"] == "low"
    assert "at most 1 native searches" in ready["prompt"]
    search.commit(search_reply({"request": ready}))
    with store.db.transaction() as conn:
        conn.execute("UPDATE news_searches SET completed_at=now()-interval '45 minutes'")
    assert search.prepare()["state"] == "idle"
    with store.db.transaction() as conn:
        conn.execute("UPDATE news_searches SET completed_at=now()-interval '61 minutes'")
    assert search.prepare()["state"] == "ready"
    store.company.ingest(event_key="efficiency-user", text="연구 상태를 알려줘", owner="UHUMAN", agent="director", channel="CQUANT", thread_ts="88.1")
    assert NewsScreeningStore(store.company).prepare()["state"] == "defer"
    assert store.prepare_review()["state"] == "defer"
    assert search.prepare()["state"] == "defer"


async def test_quiet_news_does_not_block_user_reply(efficient):
    store, clock = efficient
    overnight(store)
    select(store)
    store.commit_review(reply(store.prepare_review()["request"]))
    clock[0] = schedule.opening().replace(hour=1)+timedelta(days=1)
    store.company.ingest(event_key="night-user", text="상태", owner="UHUMAN", agent="director", channel="CQUANT", thread_ts="77.1", status_only=True)
    sender = SlackOutbox(store.company, {"reporter": {"bot_token": "fake"}, "director": {"bot_token": "fake"}},
                         httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True, "ts": "77.2"})))
    assert not await sender.send_one()
    assert await sender.send_one()


def test_related_new_duplicate_remains_available_as_evidence(efficient):
    store, _ = efficient
    first = overnight(store, suffix="first-report")
    second = overnight(store, suffix="second-report")
    screen = NewsScreeningStore(store.company)
    request = screen.prepare()["request"]
    response = screen_reply(request)
    data = json.loads(response.decision.artifacts[0].content)
    for item in data["items"]:
        item["related_ids"] = [second["id"]] if item["article_id"] == first["id"] else []
        item["disposition"] = "keep" if item["article_id"] == first["id"] else "ignore"
    response.decision.artifacts[0].content = json.dumps(data)
    screen.commit(response)
    review = store.prepare_review()["request"]
    bundle = json.loads(review["prompt"].split("NEWS DATA JSON:\n")[1])
    assert bundle["primary_ids"] == [first["id"]]
    assert {a["id"] for a in bundle["articles"]} == {first["id"], second["id"]}
    # Both are merely offered evidence; publication still requires an exact source quote.
    with pytest.raises(ValueError, match="quote_not_in_original"):
        store.commit_review(reply(review, evidence=[{"article_id": first["id"], "quote": "An invented quote that is never present in either original."}]))


async def test_cold_restart_after_night_groups_old_pending_post(efficient):
    store, _ = efficient
    overnight(store)
    select(store)
    store.commit_review(reply(store.prepare_review()["request"]))
    with store.db.transaction() as conn:
        conn.execute("UPDATE news_publications SET morning_day=NULL")
        conn.execute("UPDATE outbox SET created_at=%s", (schedule.opening()-timedelta(hours=7),))
    sender = publisher(store, lambda _: httpx.Response(200, json={"ok": True, "ts": "4000.1"}))
    assert not await sender.send_one()
    assert NewsDigestStore(store.company).flush()["members"] == 1
    assert await sender.send_one()
