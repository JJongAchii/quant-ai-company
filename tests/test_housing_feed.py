import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from quant_company.company import fingerprint
from quant_company.contracts import Role
from quant_company.housing_feed import schedule
from quant_company.housing_feed.contracts import ApplicationWindow, HousingNotice, MapLocation
from quant_company.housing_feed.maps import enrich, geocode, map_url, work_object
from quant_company.housing_feed.panel import HousingMapPanel
from quant_company.housing_feed.render import render
from quant_company.housing_feed.runner import HousingFeedCollector
from quant_company.housing_feed.sources import (
    applyhome_detail,
    applyhome_list,
    collect_source,
    lh_detail,
    lh_list,
    sh_list,
)
from quant_company.housing_feed.store import HousingFeedStore
from quant_company.slack import SlackIngress, SlackOutbox

FIXTURES = Path(__file__).parent / "fixtures" / "housing"


def fixture(name):
    return (FIXTURES / (name + ".html")).read_text()


def example(today=None):
    today = today or date(2026, 9, 28)
    return HousingNotice(id="applyhome-apt:fixture:1", source="applyhome-apt", title="합성 분양 공고",
                         region="경기", category="민영", published=today - timedelta(days=3),
                         url="https://www.applyhome.co.kr/ai/aia/selectAPTLttotPblancDetail.do?houseManageNo=1",
                         windows=[ApplicationWindow(label="1순위 · 해당지역", start=today + timedelta(days=1),
                                                    end=today + timedelta(days=1))])


def test_public_applyhome_snapshots_keep_region_rank_and_price_semantics():
    rows, _ = applyhome_list(fixture("applyhome"), "applyhome-apt")
    assert rows and all(n.region in {"서울", "경기"} for n in rows)
    notice = next(n for n in rows if n.title == "광명 시티프라디움 에듀하임")
    detail = applyhome_detail(fixture("applyhome-detail"), notice)
    assert [(w.label, w.start) for w in detail.windows if w.label.startswith("1순위")] == [
        ("1순위 · 해당지역", date(2026, 9, 30)), ("1순위 · 기타지역", date(2026, 10, 1))]
    assert detail.price_summary.startswith("주택형별 최고가") and "87,900만원" in detail.price_summary
    assert detail.document_url.startswith("https://static.applyhome.co.kr/")
    assert detail.result_date == date(2026, 10, 12)
    text = render(detail, datetime(2026, 9, 28, tzinfo=UTC))
    assert "최고가" in text and "최저" not in text and "자격" in text


def test_remaining_units_do_not_invent_general_supply_dates():
    rows, _ = applyhome_list(fixture("applyhome-remndr"), "applyhome-remndr")
    notice = next(n for n in rows if n.title == "과천 푸르지오 라비엔오")
    detail = applyhome_detail(fixture("remndr-detail"), notice)
    assert len(detail.windows) == 1 and detail.windows[0].label == "특별공급"
    assert detail.windows[0].start == detail.windows[0].end == date(2026, 9, 28)
    assert "80,338만원" in detail.price_summary
    assert not detail.active(date(2026, 9, 29))


def test_lh_uses_application_table_not_board_deadline():
    rows, _ = lh_list(fixture("lh"))
    notice = next(n for n in rows if "시흥하중" in n.title)
    detail = lh_detail(fixture("lh-detail"), notice)
    assert len(detail.windows) == 3
    assert all(w.start == date(2026, 9, 28) and w.end == date(2026, 9, 29) for w in detail.windows)
    assert "10:00" in detail.schedule_note and "17:00" in detail.schedule_note
    assert detail.price_summary.startswith("주택형별 평균가") and "367,070,000원" in detail.price_summary
    assert all(n.region in {"서울", "경기"} for n in rows)


def test_sh_registration_notices_are_not_new_housing_recruitment():
    assert all("이전등기" not in n.title for n in sh_list(fixture("sh-list")))


@pytest.mark.parametrize("parser,args", [(applyhome_list, ("applyhome-apt",)), (lh_list, ()), (sh_list, ())])
def test_maintenance_or_login_page_is_source_error_not_no_notices(parser, args):
    with pytest.raises(ValueError):
        parser("<html><p>점검 중입니다. 로그인하세요.</p></html>", *args)


def test_http_failure_and_redirect_are_not_empty_success():
    for code in [302, 403, 429, 500]:
        calls = []

        def response(request, calls=calls, code=code):
            calls.append(str(request.url))
            return httpx.Response(code, headers={"Location": "https://internal.invalid/"})

        receipt = collect_source("applyhome-apt", date(2026, 9, 28), transport=httpx.MockTransport(response))
        assert not receipt["ok"] and "entries" not in receipt and len(calls) == 1


def test_slack_markup_from_public_titles_cannot_mention_or_replace_links():
    notice = example().model_copy(update={"title": "<!channel> *sale* @here"})
    text = render(notice, datetime.now(UTC))
    assert "<!channel>" not in text and "@here" not in text and "&lt;!channel&gt;" in text
    with pytest.raises(ValueError):
        HousingNotice.model_validate({**notice.model_dump(), "url": "https://127.0.0.1/private"})


def test_verified_area_produces_interactive_openstreetmap_url():
    queries = []

    def response(request):
        queries.append(request.url.params["q"])
        return httpx.Response(200, json={"features": [{"properties": {
            "countrycode": "KR", "name": "소하동", "city": "광명시", "state": "경기도"},
            "geometry": {"coordinates": [126.8823406, 37.4432784]}}]})

    notice = example().model_copy(update={"address": "경기도 광명시 소하동 구름산지구 A6BL"})
    first = enrich({"ok": True, "entries": [notice.model_dump(mode="json")]}, {}, notice.published,
                   resolver=lambda address, region: geocode(address, region, transport=httpx.MockTransport(response)))
    mapped = HousingNotice.model_validate(first["entries"][0])
    assert queries == ["경기도 광명시 소하동"]
    assert mapped.map_location.label == "경기도 광명시 소하동"
    assert "export/embed.html?bbox=" in map_url(mapped.map_location, embed=True)
    assert "marker=37.443278%2C126.882341" in map_url(mapped.map_location, embed=True)
    assert work_object(mapped)["entity_payload"]["attributes"]["full_size_preview"]["mime_type"] == \
        "application/vnd.slack-embed"
    assert "카카오맵에서 주소 검색" in render(mapped, datetime.now(UTC))
    assert "정확한 위치" in render(mapped, datetime.now(UTC))


def test_kst_window_and_reminder_boundaries():
    notice = example()
    at = datetime(2026, 9, 28, 8, 59, tzinfo=schedule.KST)
    assert schedule.reminder(notice, at) == []
    assert len(schedule.reminder(notice, at.replace(hour=9))) == 2
    assert all("내일" in s for s in schedule.reminder(notice, at.replace(hour=9)))
    assert all("오늘" in s for s in schedule.reminder(notice, at.replace(day=29, hour=9)))
    assert schedule.reminder(notice, at.replace(hour=15)) == []
    assert schedule.delivery_time(at.replace(hour=7)).hour == 8
    assert schedule.delivery_time(at.replace(hour=21)).day == 29


@pytest.fixture
def housing(company, monkeypatch):
    company.settings.housing_feed_enabled = True
    company.settings.housing_feed_publish_enabled = True
    company.settings.housing_feed_channel_id = "CHOUSING"
    company.settings.housing_feed_owner_user = "UHUMAN"
    company.settings.housing_feed_allowed_channels = ["CHOUSING"]
    company.roles["reporter"] = Role(id="reporter", name="Reporter", mission="Fixture", model="unused",
                                     instructions="Fixture", tools=[], can_delegate_to=[], active=False)
    store = HousingFeedStore(company)
    store.clock = [datetime.now(UTC).astimezone(schedule.KST).replace(hour=10, minute=0, second=0, microsecond=0)]
    monkeypatch.setattr(schedule, "utcnow", lambda: store.clock[0])
    return store


def collect(housing, notice=None):
    with housing.db.transaction() as conn:
        conn.execute("UPDATE housing_feed_sources SET next_at=%s", (housing.clock[0],))
    notice = notice or example(housing.clock[0].date())
    for row in housing.claim_sources():
        housing.save(row, {"ok": True, "entries": [notice.model_dump(mode="json")]
                          if row["id"] == notice.source else [], "requests": []})
    return notice


def outgoing(housing):
    with housing.db.transaction() as conn:
        return conn.execute("""SELECT o.*,m.kind AS message_kind FROM outbox o JOIN messages m ON m.id=o.id
            WHERE m.kind='housing_feed' ORDER BY o.created_at""").fetchall()


def sender(housing, response):
    return SlackOutbox(housing.company, {"reporter": {"bot_token": "synthetic-slack-token"}},
                       transport=httpx.MockTransport(response))


def test_snapshot_commit_retry_and_preview_activation(housing):
    housing.company.settings.housing_feed_publish_enabled = False
    collect(housing)
    assert outgoing(housing) == []
    housing.company.settings.housing_feed_publish_enabled = True
    collect(housing)
    collect(housing)
    assert len(outgoing(housing)) == 1
    with housing.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM turns").fetchone()["n"] == 0


def test_disabled_map_panel_keeps_existing_publication_policy(housing):
    settings = housing.company.settings
    assert housing.policy() == fingerprint({
        "version": 1, "enabled": settings.housing_feed_enabled,
        "publish": settings.housing_feed_publish_enabled, "channel": settings.housing_feed_channel_id,
        "owner": settings.housing_feed_owner_user, "users": settings.slack_allowed_users,
        "channels": settings.housing_feed_allowed_channels,
        "sources": ["applyhome-apt", "applyhome-remndr", "lh-sale", "sh-sale"]})


def test_reminders_are_durable_and_initial_connection_does_not_double_post(housing):
    collect(housing)
    assert housing.reminders() == 0
    housing.clock[0] += timedelta(days=1)
    collect(housing, example(housing.clock[0].date() - timedelta(days=1)))
    assert housing.reminders() == 1
    assert HousingFeedStore(housing.company).reminders() == 0
    assert len(outgoing(housing)) == 2


def test_old_lease_cannot_replace_newer_snapshot(housing):
    old = next(r for r in housing.claim_sources() if r["id"] == "applyhome-apt")
    housing.clock[0] += timedelta(minutes=16)
    current = next(r for r in housing.claim_sources() if r["id"] == "applyhome-apt")
    assert housing.save(current, {"ok": True, "entries": []})["state"] == "collected"
    assert housing.save(old, {"ok": True, "entries": [example().model_dump(mode="json")]})["state"] == "stale"


@pytest.mark.parametrize("change", ["publish", "owner", "channel", "allowlist", "notice", "gone", "expired"])
def test_gate_rechecks_policy_and_current_notice_before_http(housing, change):
    notice = collect(housing)
    outbox = sender(housing, lambda _: pytest.fail("HTTP must not run after invalidation"))
    row = outbox.claim()
    assert row
    if change == "publish":
        housing.company.settings.housing_feed_publish_enabled = False
    elif change == "allowlist":
        housing.company.settings.housing_feed_allowed_channels = []
    elif change == "owner":
        housing.company.settings.slack_allowed_users = []
    elif change == "channel":
        housing.company.settings.housing_feed_channel_id = "COTHER"
    elif change == "notice":
        collect(housing, notice.model_copy(update={"price_summary": "changed"}))
    elif change == "gone":
        with housing.db.transaction() as conn:
            conn.execute("UPDATE housing_feed_notices SET active=false")
    else:
        housing.clock[0] += timedelta(days=3)
    assert not outbox.before_send(row)
    assert outgoing(housing)[0]["status"] == "stale"


def test_source_failure_preserves_notice_and_holds_pending_delivery(housing):
    collect(housing)
    with housing.db.transaction() as conn:
        conn.execute("UPDATE housing_feed_sources SET next_at=%s", (housing.clock[0],))
    claimed = next(r for r in housing.claim_sources() if r["id"] == "applyhome-apt")
    housing.save(claimed, {"ok": False, "error": "HTTPStatusError"})
    assert sender(housing, lambda _: None).claim() is None
    assert outgoing(housing)[0]["status"] == "pending"
    with housing.db.transaction() as conn:
        assert conn.execute("SELECT active FROM housing_feed_notices").fetchone()["active"]


@pytest.mark.parametrize("result", ["timeout", "500", "missing_ts", "missing_channel", "wrong_channel"])
async def test_uncertain_slack_outcome_is_not_replayed(housing, result):
    collect(housing)
    calls = []

    def response(request):
        calls.append(json.loads(request.content))
        if result == "timeout":
            raise httpx.ReadTimeout("synthetic timeout")
        if result == "500":
            return httpx.Response(500)
        if result == "missing_channel":
            return httpx.Response(200, json={"ok": True, "ts": "123.456"})
        return httpx.Response(200, json={"ok": True, "ts": "123.456" if result == "wrong_channel" else None,
                                        "channel": "CWRONG" if result == "wrong_channel" else "CHOUSING"})

    outbox = sender(housing, response)
    assert await outbox.send_one()
    assert outgoing(housing)[0]["status"] == "uncertain"
    assert not await outbox.send_one() and len(calls) == 1


async def test_real_postgres_to_simulated_slack_has_stable_receipt(housing):
    collect(housing)
    posted = []

    def response(request):
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "ts": "123.456", "channel": "CHOUSING"})

    assert await sender(housing, response).send_one()
    assert posted[0]["channel"] == "CHOUSING"
    assert "합성 분양 공고" in posted[0]["text"] and "공식 공고·신청 안내 확인" in posted[0]["text"]
    assert posted[0]["client_msg_id"] == str(outgoing(housing)[0]["id"])
    assert outgoing(housing)[0]["status"] == "delivered"


async def test_two_collectors_do_not_duplicate_committed_messages(housing):
    def fetch(source, today):
        return {"ok": True, "entries": [example(today).model_dump(mode="json")] if source == "applyhome-apt" else []}

    await asyncio.gather(HousingFeedCollector(housing.company, fetch).tick(),
                         HousingFeedCollector(housing.company, fetch).tick())
    assert len(outgoing(housing)) == 1


async def test_map_work_object_click_opens_durable_interactive_panel(housing):
    housing.company.settings.housing_map_panel_enabled = True
    location = MapLocation(longitude=126.8823406, latitude=37.4432784,
                           label="경기도 광명시 소하동", level="neighborhood")
    notice = example(housing.clock[0].date()).model_copy(update={
        "address": "경기도 광명시 소하동 구름산지구 A6BL", "map_location": location})
    collect(housing, notice)
    collect(housing, notice.model_copy(update={"map_location": None}))
    assert len(outgoing(housing)) == 1  # Location enrichment never creates a changed-notice alert.
    collect(housing, notice)
    posted = []

    def post(request):
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "ts": "123.456", "channel": "CHOUSING"})

    assert await sender(housing, post).send_one()
    entity = posted[0]["metadata"]["entities"][0]
    assert entity["entity_type"] == "slack#/entities/item"
    assert entity["external_ref"] == {"id": notice.id, "type": "housing_notice"}
    assert "image" not in posted[0]
    credentials = {"reporter": {"app_id": "AREPORTER", "bot_user_id": "UBOTREPORTER",
                                "bot_token": "synthetic-slack-token"}}
    event = {"type": "event_callback", "team_id": "TTEST", "api_app_id": "AREPORTER",
             "event_id": "EvMap1", "event": {"type": "entity_details_requested", "user": "UHUMAN",
             "channel": "CHOUSING", "message_ts": "123.456", "trigger_id": "trigger-1",
             "external_ref": entity["external_ref"], "entity_url": notice.url}}
    ingress = SlackIngress(housing.company.settings, housing.company, credentials)
    assert ingress.accept("reporter", event, credentials["reporter"])["map_details_queued"]
    assert ingress.accept("reporter", event, credentials["reporter"])["map_details_queued"]
    delivered = []

    def respond(request):
        delivered.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    panel = HousingMapPanel(housing.company, credentials, transport=httpx.MockTransport(respond))
    assert await panel.send_one()
    assert delivered[0]["metadata"]["entity_payload"]["attributes"]["full_size_preview"]["preview_url"].startswith(
        "https://www.openstreetmap.org/export/embed.html?")
    assert not await panel.send_one()
    with housing.db.transaction() as conn:
        assert conn.execute("SELECT status FROM housing_map_details").fetchone()["status"] == "delivered"


async def test_map_panel_rejects_other_user_and_preserves_uncertain_delivery(housing):
    housing.company.settings.housing_map_panel_enabled = True
    location = MapLocation(longitude=126.8823406, latitude=37.4432784,
                           label="경기도 광명시 소하동", level="neighborhood")
    notice = collect(housing, example(housing.clock[0].date()).model_copy(update={
        "address": "경기도 광명시 소하동", "map_location": location}))
    assert await sender(housing, lambda _: httpx.Response(200, json={
        "ok": True, "ts": "123.456", "channel": "CHOUSING"})).send_one()
    credential = {"app_id": "AREPORTER", "bot_user_id": "UBOTREPORTER", "bot_token": "synthetic"}
    credentials = {"reporter": credential}
    event = {"type": "event_callback", "team_id": "TTEST", "api_app_id": "AREPORTER",
             "event_id": "EvMap2", "event": {"type": "entity_details_requested", "user": "UOTHER",
             "channel": "CHOUSING", "message_ts": "123.456", "trigger_id": "trigger-2",
             "external_ref": {"id": notice.id, "type": "housing_notice"}, "entity_url": notice.url}}
    ingress = SlackIngress(housing.company.settings, housing.company, credentials)
    assert ingress.accept("reporter", event, credential)["ignored"]
    event["event"]["user"] = "UHUMAN"
    assert ingress.accept("reporter", event, credential)["map_details_queued"]

    def timeout(_):
        raise httpx.ReadTimeout("synthetic")

    panel = HousingMapPanel(housing.company, credentials, transport=httpx.MockTransport(timeout))
    assert await panel.send_one()
    assert not await panel.send_one()
    with housing.db.transaction() as conn:
        assert conn.execute("SELECT status FROM housing_map_details").fetchone()["status"] == "uncertain"
