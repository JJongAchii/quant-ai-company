"""Reporter news published to Insight Invest without a Slack post."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from psycopg.types.json import Jsonb

from quant_company.market_desk.runner import MarketDesk
from quant_company.news.digest import NewsDigestStore
from quant_company.runtime import news_loops
from quant_company.slack import SlackOutbox
from tests.test_news import news, ready_article, reply, reporter_credentials  # noqa: F401
from tests.test_news_efficiency import efficient, overnight, select  # noqa: F401

FACTS = "중앙은행이 정책금리를 동결했습니다."
WHY = "금융 여건의 변화를 살펴볼 필요가 있습니다."


@pytest.fixture
def insight(news):  # noqa: F811
    news.company.settings.insight_market_bucket = "insight-test"
    news.company.settings.news_slack_delivery_enabled = False
    return news


def publish(store, **overrides):
    result = store.commit_review(reply(store.prepare_review()["request"], **overrides))
    assert result["items"][0]["state"] == "queued"
    with store.db.transaction() as conn:
        return conn.execute("""SELECT p.*,m.created_at,m.text FROM news_publications p JOIN messages m ON m.id=p.id
            ORDER BY m.created_at DESC LIMIT 1""").fetchone()


def board(store, at=None):
    class Storage:
        def market(self):
            raise FileNotFoundError

        def write(self, value):
            self.board = value

    storage = Storage()
    desk = MarketDesk(store.company, AsyncMock(), storage, filings=lambda at: [],
                      clock=lambda: at or datetime.now(UTC))
    desk.collect()
    return storage.board


def test_news_reaches_the_board_with_its_fields_and_never_enters_the_slack_queue(insight):
    article = ready_article(insight)
    saved = publish(insight, category="금융", priority="urgent")
    assert saved["venue"] == "insight"
    with insight.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0

    published = board(insight)
    (item,) = published["news"]
    assert item == {
        "id": f"source-{saved['event_id']}", "kind": "news", "origin": "reporter", "status": "reported",
        "headline": "중앙은행 정책금리 동결", "fact": FACTS, "interpretation": "", "watch": "", "observation": "",
        "observed_at": None, "published_at": saved["created_at"].astimezone(insight_zone()).isoformat(),
        "category": "금융", "priority": "urgent", "update": False, "attribution": None,
        "significance": WHY, "change": "",
        "sources": [{"title": "Bank policy decision", "url": article["url"], "publisher": "official",
                     "published_at": article["published_at"].isoformat()}],
    }
    # The earlier list keeps its shape for a reader that has not learned the new one.
    (legacy,) = published["issues"]
    assert legacy["id"] == item["id"] and legacy["published_at"] == item["published_at"]
    assert legacy["fact"] == saved["text"] and "*왜 중요한가*" in legacy["fact"] and "category" not in legacy


def insight_zone():
    from quant_company.briefing.schedule import KST

    return KST


def test_material_update_to_an_event_first_published_on_the_board_replaces_it(insight):
    ready_article(insight)
    first = publish(insight)
    ready_article(insight, body="Material correction", suffix="updated")
    request = insight.prepare_review()["request"]
    bundle = json.loads(request["prompt"].split("NEWS DATA JSON:\n", 1)[1])
    assert [(e["id"], e["root_delivery"]) for e in bundle["events"]] == [(str(first["event_id"]), "published")]
    result = insight.commit_review(reply(request, event_id=str(first["event_id"]), priority="urgent",
                                         facts="중앙은행이 정책 발표 수치를 정정했습니다.",
                                         change="공식 발표에 정정 내용이 추가됐습니다."))
    assert result["items"][0]["state"] == "queued"
    (item,) = board(insight)["news"]
    assert item["id"] == f"source-{first['event_id']}"
    assert (item["fact"], item["update"], item["change"], item["priority"]) == (
        "중앙은행이 정책 발표 수치를 정정했습니다.", True, "공식 발표에 정정 내용이 추가됐습니다.", "urgent")
    with insight.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0


def test_single_outlet_report_names_the_outlet(insight, tmp_path):
    from tests.test_news import source

    wire = source("wire", kind="media", allow_attributed_reporting=True)
    insight.company.settings.news_sources_file.write_text(json.dumps([wire.model_dump()]))
    insight.sync_sources()
    ready_article(insight, wire)
    publish(insight, verification="attributed_report")
    (item,) = board(insight)["news"]
    assert item["attribution"] == "wire" and item["sources"][0]["publisher"] == "wire"


def test_overnight_news_is_on_the_board_at_opening_without_a_morning_digest(efficient):  # noqa: F811
    store, _ = efficient
    store.company.settings.insight_market_bucket = "insight-test"
    store.company.settings.news_slack_delivery_enabled = False
    overnight(store)
    select(store)
    publish(store)
    assert NewsDigestStore(store.company).flush()["state"] == "idle"
    with store.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM news_digests").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"] == 0
    assert [item["headline"] for item in board(store)["news"]] == ["중앙은행 정책금리 동결"]


def test_stopping_slack_without_an_insight_board_keeps_posting_to_slack(news):  # noqa: F811
    news.company.settings.news_slack_delivery_enabled = False
    ready_article(news)
    assert publish(news)["venue"] == "slack"
    with news.db.transaction() as conn:
        assert conn.execute("SELECT status FROM outbox").fetchone()["status"] == "pending"


async def test_slack_delivery_is_unchanged_and_the_board_lists_only_what_was_delivered(news, credentials):  # noqa: F811
    news.company.settings.insight_market_bucket = "insight-test"
    ready_article(news)
    saved = publish(news)
    assert saved["venue"] == "slack"
    assert board(news)["news"] == [] and board(news)["issues"] == []
    sent = []

    def transport(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "ts": "1795000000.000100"})

    assert await SlackOutbox(news.company, reporter_credentials(credentials), httpx.MockTransport(transport)).send_one()
    assert sent[0]["text"] == saved["text"]
    (item,) = board(news)["news"]
    assert item["published_at"] == datetime.fromtimestamp(1795000000.0001, insight_zone()).isoformat()
    assert (item["fact"], item["significance"], item["priority"]) == (FACTS, WHY, "major")


def test_board_keeps_a_week_of_news_and_older_editions_stay_as_written(insight):
    ready_article(insight)
    recent = publish(insight)
    with insight.db.transaction() as conn:
        conn.execute("UPDATE news_publications SET item=NULL WHERE id=%s", (recent["id"],))
        conn.execute("UPDATE messages SET created_at=now()-interval '5 days' WHERE id=%s", (recent["id"],))
    published = board(insight)
    (item,) = published["news"]
    # An edition published before fields were recorded is listed with its text, and has left the two-day list.
    assert item["fact"] == recent["text"] and "priority" not in item
    assert published["issues"] == []
    with insight.db.transaction() as conn:
        conn.execute("UPDATE messages SET created_at=now()-interval '8 days' WHERE id=%s", (recent["id"],))
    assert board(insight)["news"] == []


def test_status_lists_board_publications_that_have_no_slack_delivery(insight):
    ready_article(insight)
    saved = publish(insight)
    status = insight.status()
    assert status["deliveries"] == []
    assert [(p["id"], p["venue"], p["headline"]) for p in status["publications"]] == [
        (str(saved["id"]), "insight", "중앙은행 정책금리 동결")]


def test_story_cited_by_an_index_move_analysis_stays_in_the_news_list(insight):
    ready_article(insight)
    saved = publish(insight)
    key = f"source-{saved['event_id']}"
    at = datetime.now(UTC)
    analysis = {"id": key, "kind": "move", "status": "analysis", "headline": "코스피 낙폭 확대의 배경",
                "fact": "지수가 밀렸습니다.", "interpretation": "정책 발표와 맞물렸습니다.", "watch": "다음 수집",
                "published_at": at.isoformat(), "observed_at": at.isoformat(), "observation": "지수 변화",
                "sources": []}
    with insight.db.transaction() as conn:
        conn.execute("""INSERT INTO market_desk_revisions(revision,issue_id,aliases,day,created_at,packet,article,state,next_at)
            VALUES('move-revision',%s,%s,%s,%s,'{"kind":"move"}',%s,'written',%s)""",
                     (key, ["2026/index/KOSPI/down", key], at.date(), at, Jsonb(analysis), at))
    published = board(insight)
    # The home shows the desk's explanation of the move in place of the report. The news page keeps the news.
    assert [(item["id"], item["kind"]) for item in published["issues"]] == [(key, "move")]
    assert [(item["id"], item["kind"], item["fact"]) for item in published["news"]] == [(key, "news", FACTS)]


async def test_follow_ups_share_one_thread_when_slack_resumes_for_a_story_begun_on_the_board(insight, credentials):
    ready_article(insight)
    first = publish(insight)
    insight.company.settings.news_slack_delivery_enabled = True
    posts = []

    def transport(request):
        posts.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "ts": f"2000.{len(posts):06d}"})

    outbox = SlackOutbox(insight.company, reporter_credentials(credentials), httpx.MockTransport(transport))
    for number, suffix in enumerate(["second", "third"], start=1):
        ready_article(insight, body=f"Material correction {number}", suffix=suffix)
        publish(insight, event_id=str(first["event_id"]), facts=f"중앙은행이 수치를 {number}차 정정했습니다.",
                change=f"{number}차 정정이 추가됐습니다.")
        assert await outbox.send_one()
    assert "thread_ts" not in posts[0]
    assert posts[1]["thread_ts"] == "2000.000001"


async def test_post_already_waiting_for_slack_is_still_delivered_after_slack_is_stopped(news, credentials):  # noqa: F811
    news.company.settings.insight_market_bucket = "insight-test"
    ready_article(news)
    waiting = publish(news)
    news.company.settings.news_slack_delivery_enabled = False
    sent = []

    def transport(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "ts": "1795000000.000100"})

    assert await SlackOutbox(news.company, reporter_credentials(credentials), httpx.MockTransport(transport)).send_one()
    assert [post["text"] for post in sent] == [waiting["text"]]
    assert [item["id"] for item in board(news)["news"]] == [f"source-{waiting['event_id']}"]


class Answering:
    async def run(self, request):
        return reply(request.model_dump())


@pytest.fixture
def outside_session(monkeypatch):
    """The desk reads live disclosures during exchange hours. These tests are about news, at any hour."""
    monkeypatch.setattr(MarketDesk, "session", lambda self, at: False)


@pytest.mark.parametrize(("slack", "written"), [(False, 1), (True, 0)])
async def test_board_only_news_is_on_the_board_as_soon_as_its_review_is_committed(
        insight, monkeypatch, outside_session, slack, written):
    boards = []

    class Storage:
        def __init__(self, bucket, endpoint=""):
            assert bucket == "insight-test"

        def market(self):
            raise FileNotFoundError

        def write(self, board):
            boards.append(board)

    monkeypatch.setattr("quant_company.market_desk.runner.BoardStorage", Storage)
    insight.company.settings.news_slack_delivery_enabled = slack
    ready_article(insight)
    editorial = next(loop for loop in news_loops(insight.company, Answering()) if loop.name == "news-editorial")
    result = await editorial.tick()
    assert result["items"][0]["state"] == "queued"
    # Without a Slack post there is no other place to read it. A Slack post reaches the board after its delivery.
    assert [[item["headline"] for item in board["news"]] for board in boards] == [["중앙은행 정책금리 동결"]] * written
    assert (await editorial.tick())["state"] == "idle" and len(boards) == written


async def test_failed_board_write_does_not_fail_the_committed_review(insight, monkeypatch, outside_session):
    class Unreachable:
        def __init__(self, bucket, endpoint=""):
            pass

        def market(self):
            raise FileNotFoundError

        def write(self, board):
            raise ConnectionError("storage unreachable")

    monkeypatch.setattr("quant_company.market_desk.runner.BoardStorage", Unreachable)
    ready_article(insight)
    editorial = next(loop for loop in news_loops(insight.company, Answering()) if loop.name == "news-editorial")
    assert (await editorial.tick())["items"][0]["state"] == "queued"
