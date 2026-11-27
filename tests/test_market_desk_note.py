"""Intraday market notes for Insight Invest: when they are written, what they may say, and how they are checked."""

import json
from datetime import date, datetime, timedelta

import pytest

from quant_company.briefing.contracts import CalendarOverride
from quant_company.briefing.schedule import KST
from quant_company.contracts import AgentDecision, ArtifactDraft, ProviderResponse
from quant_company.market_desk import note

DAY = date(2026, 11, 27)
OPEN, CLOSE = datetime(2026, 11, 27, 9, 0, tzinfo=KST), datetime(2026, 11, 27, 15, 30, tzinfo=KST)


def stock(name, ticker, chg, contrib=None, **extra):
    row = {"ticker": ticker, "name": name, "sector": "기계·장비", "chg_pct": chg, "value": 1.2e11, **extra}
    return {**row, "contrib_pp": contrib} if contrib is not None else row


def market_map(kosdaq=5.69, observed="2026-11-27T11:05:00+09:00"):
    breadth = {"up": 1461, "down": 219, "flat": 156, "median": 2.32, "up10": 93, "down10": 3,
               "limit_up": [stock("제닉스로보틱스", "001", 29.97)], "limit_down": []}
    kospi = {"approx_chg_pct": 1.77, "top10_share": 0.54, "sectors": [],
             "breadth": {**breadth, "up": 704, "down": 172, "up10": 12, "limit_up": []},
             "contributors": {"up": [stock("삼성전자", "005930", 1.40, 0.36)], "down": [stock("신한지주", "055550", -2.48, -0.025)]}}
    return {"version": 1, "trade_date": "2026-11-27", "observed_at": observed,
            "indices": {"KOSPI": {"level": 4794.49, "chg_pct": 1.77}, "KOSDAQ": {"level": 692.99, "chg_pct": kosdaq}},
            "markets": {"KOSPI": kospi, "KOSDAQ": {"approx_chg_pct": kosdaq, "top10_share": 0.29, "breadth": breadth, "sectors": [],
                                                    "contributors": {"up": [stock("레인보우로보틱스", "277810", 12.21, 0.30)], "down": []}}},
            "value_spikes": [stock("안랩", "053800", 7.42, multiple=5.75, market="KOSDAQ")], "value_spikes_state": "ready",
            "groups": [{"id": "robotics", "name": "로봇·자동화", "direction": "up",
                        "members": [{"ticker": "277810", "name": "레인보우로보틱스", "chg_pct": 12.21}]}]}


SIDECAR = "한국거래소는 이날 오전 10시 22분 코스닥시장에 매수 사이드카를 발동했다."


def doc(key, title, content, minutes=-40):
    at = datetime(2026, 11, 27, 11, 5, tzinfo=KST) + timedelta(minutes=minutes)
    return {"id": key, "title": title, "publisher": "시장통신", "url": "https://example.org/" + key,
            "published_at": at.isoformat(), "role": "시장 원문", "content": (content + " ") * 3}


def test_slots_follow_the_session_and_move_with_a_late_opening():
    bounds = note.session_bounds(DAY, {})
    assert bounds == (OPEN, CLOSE)
    assert [note.slot_time(bounds, name).strftime("%H:%M") for name in note.SLOT_NAMES] == [
        "09:40", "11:00", "13:30", "14:50", "16:05"]
    late = CalendarOverride(market="KR", day=DAY, close_at=CLOSE + timedelta(hours=1), source_url="https://www.krx.co.kr/")
    shifted = note.session_bounds(DAY, {("KR", DAY): late})
    assert note.slot_time(shifted, "open").strftime("%H:%M") == "10:40"
    assert note.session_bounds(date(2026, 11, 28), {}) is None


def written(slot, minutes_ago, kind="scheduled", trigger=None, at=None):
    at = at or datetime(2026, 11, 27, 11, 0, tzinfo=KST)
    return {"slot": slot, "kind": kind, "trigger": trigger, "created_at": at - timedelta(minutes=minutes_ago)}


def test_a_scheduled_note_is_due_once_and_yields_to_a_recent_note():
    bounds = note.session_bounds(DAY, {})
    at = datetime(2026, 11, 27, 11, 2, tzinfo=KST)
    assert note.due_slot(at, bounds, []) == ("morning", "write")
    assert note.due_slot(at, bounds, [written("morning", 2)]) is None
    assert note.due_slot(at, bounds, [written(None, 20, kind="urgent")]) == ("morning", "skip")
    assert note.due_slot(at + timedelta(minutes=50), bounds, []) is None  # missed slots are not written late
    assert note.due_slot(datetime(2026, 11, 27, 16, 7, tzinfo=KST), bounds, [written("preclose", 77)]) == ("close", "write")


def test_urgent_notes_follow_crossings_market_actions_and_big_moves_with_spacing_and_caps():
    bounds = note.session_bounds(DAY, {})
    at = datetime(2026, 11, 27, 10, 30, tzinfo=KST)
    market = {"map": market_map(kosdaq=5.04, observed="2026-11-27T10:25:00+09:00"), "events": []}
    headline = {"title": "코스닥 5%대 급등…매수 사이드카 발동", "published_at": (at - timedelta(minutes=4)).isoformat(),
                "url": "https://example.org/sidecar", "publisher": "시장통신"}
    action = note.urgent_trigger(at, bounds, market, [headline], [written("open", 50, at=at)], previous_indices=None)
    assert (action["kind"], action["id"]) == ("market_action", "2026-11-27/action/KOSDAQ/sidecar")
    again = written(None, 40, kind="urgent", trigger=action, at=at)
    assert note.urgent_trigger(at, bounds, market, [headline], [again], previous_indices=None) is None
    assert note.urgent_trigger(at, bounds, market, [headline], [written("open", 10, at=at)], previous_indices=None) is None
    moved = note.urgent_trigger(at, bounds, market, [], [written("open", 50, at=at)],
                                previous_indices={"KOSPI": 1.5, "KOSDAQ": 2.87})
    assert (moved["kind"], moved["index"]) == ("since_last", "KOSDAQ")
    crossing = {"id": "2026-11-27/cross/KOSDAQ/up", "index": "KOSDAQ", "direction": "up", "threshold": 3.0,
                "observed_at": "2026-11-27 10:15", "chg_pct": 3.4}
    first = note.urgent_trigger(at, bounds, {**market, "events": [crossing]}, [], [written("open", 50, at=at)],
                                previous_indices=None)
    assert first["kind"] == "index_cross"
    near_slot = datetime(2026, 11, 27, 10, 50, tzinfo=KST)
    assert note.urgent_trigger(near_slot, bounds, market, [headline | {"published_at": near_slot.isoformat()}],
                               [written("open", 70, at=near_slot)], previous_indices=None) is None
    full = [written(None, 200 - 40 * i, kind="urgent", trigger={"id": str(i)}, at=at) for i in range(3)]
    assert note.urgent_trigger(at, bounds, market, [headline], full, previous_indices=None) is None


def test_market_actions_are_one_alert_per_event_whoever_reports_it():
    rows = [{"title": "코스닥 매수 사이드카 발동", "url": "https://a.example/1", "publisher": "A",
             "published_at": "2026-11-27T10:24:00+09:00"},
            {"title": "[속보] 코스닥시장 매수 사이드카 발동…3거래일 만", "url": "https://b.example/2", "publisher": "B",
             "published_at": "2026-11-27T10:23:00+09:00"},
            {"title": "[속보] 코스닥 사이드카 발동", "url": "https://d.example/4", "publisher": "D",
             "published_at": "2026-11-27T10:22:50+09:00"},
            {"title": "사이드카 제도 개선 토론회", "url": "https://c.example/3", "publisher": "C",
             "published_at": "2026-11-27T10:30:00+09:00"}]
    alerts = note.market_actions(rows, note.session_bounds(DAY, {}))
    assert [(a["id"], a["source"]["publisher"]) for a in alerts] == [("2026-11-27/action/KOSDAQ/sidecar", "D")]


@pytest.mark.parametrize(("title", "expected"), [
    ("코스닥 5%대 급등…매수 사이드카 발동", [("KOSDAQ", "sidecar")]),
    ("美 증시 급락에 코스피 매도 사이드카 발동", [("KOSPI", "sidecar")]),
    ("코스피 서킷브레이커 1단계 발동", [("KOSPI", "circuit1")]),
    ("코스피·코스닥 서킷브레이커 2단계 동시 발동", [("KOSDAQ", "circuit2"), ("KOSPI", "circuit2")]),
    ("코스닥, 지난해 8월 이후 처음 매도 사이드카 발동", [("KOSDAQ", "sidecar")]),
    ("코스닥 전일 대비 6% 급락…매도 사이드카 발동", [("KOSDAQ", "sidecar")]),
    ("[개장시황] 전날 사이드카 발동 코스닥, 하락 출발", []),
    ("서킷브레이커 발동 요건은?", []),
    ("뉴욕증시 서킷브레이커 발동", []),
    ("코스피 약세 속 뉴욕증시 서킷브레이커 발동", []),
    ("사이드카 제도 개선 토론회", []),
])
def test_a_headline_counts_only_when_it_reports_a_measure_taken_in_a_korean_market_today(title, expected):
    assert note._actions(title) == expected


def test_reports_outside_the_session_are_not_events():
    morning = {"title": "코스닥 매수 사이드카 발동", "url": "https://a.example/1", "publisher": "A",
               "published_at": "2026-11-27T08:40:00+09:00"}
    evening = {**morning, "published_at": "2026-11-27T16:10:00+09:00"}
    assert note.market_actions([morning, evening], note.session_bounds(DAY, {})) == []


def packet(**changes):
    docs = [doc("wire", "코스닥 매수 사이드카 발동", SIDECAR)]
    return note.packet_for(kind="scheduled", slot="morning", trigger=None, market_map=market_map(), docs=docs,
                           previous={"headline": "코스닥 3% 넘게 상승", "indices": {"KOSPI": 0.59, "KOSDAQ": 3.0}},
                           at=datetime(2026, 11, 27, 11, 8, tzinfo=KST), **changes)


def response(request, **draft):
    body = {"headline": "코스닥 +5.69%, 10시 22분 매수 사이드카",
            "drivers": [{"text": "코스닥은 1,461개가 오르고 219개가 내려 시장 전체가 올랐다", "label": "데이터", "refs": ["breadth.KOSDAQ"], "citations": []},
                        {"text": "10시 22분 코스닥에 매수 사이드카가 발동됐다", "label": "보도", "refs": [],
                         "citations": [{"source_id": "wire", "quote": SIDECAR}]},
                        {"text": "로봇주에 매수가 몰린 것으로 보인다", "label": "해석", "refs": ["theme.robotics"], "citations": []}],
            "stocks": [{"name": "레인보우로보틱스", "reason": "로봇주 강세를 이끌었을 가능성", "label": "해석", "refs": ["lift.KOSDAQ"], "citations": []}],
            "since_last": "코스닥은 지난 노트의 +3.0%에서 +5.69%로 올랐다", "watch": ["사이드카 뒤 상승폭이 유지되는지"]}
    body.update(draft)
    return ProviderResponse(request_id=request.request_id, provider="codex", decision=AgentDecision(
        status="complete", say="", artifacts=[ArtifactDraft(title="장중 노트", content=json.dumps(body, ensure_ascii=False))]))


def check(draft_changes=None, **packet_changes):
    built = packet(**packet_changes)
    req = note.request_for("rev", built)
    row = {"packet": built, "request": req.model_dump(mode="json"), "issue_id": built["key"]}
    return note.edited(response(req, **(draft_changes or {})), row, datetime(2026, 11, 27, 11, 9, tzinfo=KST))


def test_facts_number_every_figure_the_note_may_use():
    facts = {f["id"]: f["text"] for f in packet()["facts"]}
    assert facts["index.KOSPI"] == "코스피 4,794.49 (전일 대비 +1.77%)"
    assert "상승 1,461 · 하락 219" in facts["breadth.KOSDAQ"] and "상한가 1: 제닉스로보틱스 +29.97%" in facts["breadth.KOSDAQ"]
    assert any("안랩 +7.42%" in text and "평소 하루치의 5.75배" in text for text in facts.values())
    assert any(text.startswith("로봇·자동화") for text in facts.values())


def test_a_supported_note_is_published_with_its_sources_and_moves():
    article = check()
    assert article["headline"] == "코스닥 +5.69%, 10시 22분 매수 사이드카"
    assert [c["label"] for c in article["drivers"]] == ["데이터", "보도", "해석"]
    assert article["drivers"][1]["sources"][0]["url"] == "https://example.org/wire"
    assert (article["stocks"][0]["ticker"], article["stocks"][0]["chg_pct"]) == ("277810", 12.21)
    assert article["observed_at"] == "2026-11-27T11:05:00+09:00" and article["slot"] == "morning"
    assert [(s["url"], s["role"]) for s in article["sources"]] == [("https://example.org/wire", "시장 원문")]


def test_an_event_a_note_already_had_does_not_come_back_as_an_urgent_note():
    """10:52 사이드카 보도는 11:00 정시 노트가 다뤘다 — 11:30에 같은 사건으로 급변 노트를 쓰지 않는다."""
    bounds = note.session_bounds(DAY, {})
    at = datetime(2026, 11, 27, 11, 30, tzinfo=KST)
    market = {"map": market_map(observed="2026-11-27T11:25:00+09:00"), "events": [
        {"id": "2026-11-27/cross/KOSDAQ/up", "index": "KOSDAQ", "direction": "up", "threshold": 3.0,
         "observed_at": "2026-11-27 10:55", "chg_pct": 3.4}]}
    headline = {"title": "코스닥 매수 사이드카 발동", "published_at": "2026-11-27T10:52:00+09:00",
                "url": "https://example.org/sidecar", "publisher": "시장통신"}
    scheduled = written("morning", 30, at=at)
    assert note.urgent_trigger(at, bounds, market, [headline], [scheduled], previous_indices=None) is None
    later = headline | {"title": "코스피 매도 사이드카 발동", "published_at": "2026-11-27T11:12:00+09:00"}
    assert note.urgent_trigger(at, bounds, market, [headline, later], [scheduled], previous_indices=None)["id"] == (
        "2026-11-27/action/KOSPI/sidecar")


def test_investors_buying_is_an_observation_not_advice():
    article = check({"watch": ["외국인이 계속 순매수해야 상승이 이어질 수 있다"]})
    assert article["watch"] == ["외국인이 계속 순매수해야 상승이 이어질 수 있다"]


@pytest.mark.parametrize("changes", [
    {"headline": "코스닥 -5.69% 급락"},
    {"drivers": [{"text": "코스닥은 219개가 오르고 1,461개가 내렸다", "label": "데이터", "refs": ["breadth.KOSDAQ"],
                  "citations": []}]},
    {"drivers": [{"text": "로봇주 강세는 정책 기대 때문이다", "label": "해석", "refs": ["theme.robotics"], "citations": []}]},
    {"drivers": [{"text": "레인보우로보틱스의 대형 공급계약이 매수를 부른 것으로 보인다", "label": "해석",
                  "refs": ["lift.KOSDAQ"], "citations": []}]},
])
def test_flipped_signs_swapped_counts_flat_readings_and_invented_events_are_rejected(changes):
    with pytest.raises(ValueError):
        check(changes)


@pytest.mark.parametrize("changes", [
    {"headline": "코스닥 +6.2% 급등"},
    {"drivers": [{"text": "코스닥이 오른 이유는 정책 기대다", "label": "해석", "refs": [], "citations": []}]},
    {"drivers": [{"text": "10시 22분 사이드카가 발동됐다", "label": "보도", "refs": [],
                  "citations": [{"source_id": "wire", "quote": "원문에 없는 인용문을 지어냈다"}]}]},
    {"drivers": [{"text": "코스닥은 1,500개가 올랐다", "label": "데이터", "refs": ["breadth.KOSDAQ"], "citations": []}]},
    {"stocks": [{"name": "없는회사", "reason": "지도에 없는 종목", "label": "데이터", "refs": ["lift.KOSDAQ"], "citations": []}]},
    {"watch": ["레인보우로보틱스를 매수하세요"]},
])
def test_unsupported_numbers_names_quotes_and_advice_are_rejected(changes):
    with pytest.raises(ValueError):
        check(changes)


# ---- registration, writing and the board (real PostgreSQL)

from unittest.mock import AsyncMock  # noqa: E402

from quant_company.market_desk.runner import MarketDesk  # noqa: E402

AT = datetime(2026, 11, 27, 11, 2, tzinfo=KST)


class Storage:
    def __init__(self, market):
        self.input, self.board = market, None

    def market(self):
        return self.input

    def write(self, board):
        self.board = board


def desk_input(observed=AT - timedelta(minutes=2), events=()):
    return {"version": 1, "trade_date": "2026-11-27", "observed_at": observed.isoformat(), "quote_delay_minutes": 20,
            "large_companies": [], "moves": [], "session": None, "events": list(events),
            "map": market_map(observed=observed.replace(second=0, microsecond=0).isoformat())}


def answer(request):
    body = {"headline": "코스닥 상승 1,461 · 하락 219", "since_last": "", "watch": [], "stocks": [],
            "drivers": [{"text": "코스닥은 1,461개가 올랐다", "label": "데이터", "refs": ["breadth.KOSDAQ"], "citations": []}]}
    return ProviderResponse(request_id=request.request_id, provider="codex", decision=AgentDecision(
        status="complete", say="", artifacts=[ArtifactDraft(title="장중 노트", content=json.dumps(body, ensure_ascii=False))]))


def notes_in(company):
    with company.db.transaction() as conn:
        return conn.execute("""SELECT packet->>'slot' AS slot,packet->>'note_kind' AS kind,state FROM market_desk_revisions
            WHERE packet->>'kind'='note' ORDER BY created_at""").fetchall()


@pytest.mark.integration
async def test_scheduled_note_is_frozen_once_written_and_published_without_spending_the_lead(company):
    storage, provider = Storage(desk_input()), AsyncMock()
    provider.run.side_effect = answer
    desk = MarketDesk(company, provider, storage, filings=lambda at: [], clock=lambda: AT)
    await desk.tick()
    assert notes_in(company) == [] and "notes" not in storage.board
    company.settings.insight_notes_enabled = True
    await desk.tick()
    await desk.tick()
    assert [(n["slot"], n["kind"], n["state"]) for n in notes_in(company)] == [("morning", "scheduled", "queued")]
    await desk.write_article()
    assert [n["headline"] for n in storage.board["notes"]] == ["코스닥 상승 1,461 · 하락 219"]
    assert storage.board["notes"][0]["observed_at"] == "2026-11-27T11:00:00+09:00"
    assert storage.board["issues"] == []
    with company.db.transaction() as conn:
        used = conn.execute("""SELECT count(*) AS n FROM market_desk_revisions WHERE request IS NOT NULL
            AND packet->>'kind'<>'note'""").fetchone()["n"]
    assert used == 0


@pytest.mark.integration
async def test_a_stale_map_skips_the_slot_for_good(company):
    company.settings.insight_notes_enabled = True
    storage = Storage(desk_input(observed=AT - timedelta(minutes=40)))
    desk = MarketDesk(company, AsyncMock(), storage, filings=lambda at: [], clock=lambda: AT)
    await desk.tick()
    storage.input = desk_input()
    await desk.tick()
    assert [(n["slot"], n["state"]) for n in notes_in(company)] == [("morning", "skipped")]


@pytest.mark.integration
async def test_two_outlets_reporting_a_sidecar_make_one_alert_and_one_urgent_note(company):
    company.settings.insight_notes_enabled = True
    at = datetime(2026, 11, 27, 10, 30, tzinfo=KST)
    with company.db.transaction() as conn:
        for key, publisher in (("a", "통신 A"), ("b", "통신 B")):
            conn.execute("INSERT INTO news_sources(id,config,config_digest,enabled) VALUES(%s,%s,'d',true)",
                         (key, json.dumps({"publisher": publisher, "use_for_summary": False})))
            conn.execute("""INSERT INTO news_articles(id,source_id,url,title,summary,feed_digest,published_at,collected_at,
                source_digest,state) VALUES(%s,%s,%s,'코스닥 매수 사이드카 발동','',%s,%s,%s,'d','discovery_only')""",
                         (key, key, f"https://{key}.example/1", key, at - timedelta(minutes=6), at - timedelta(minutes=5)))
    storage = Storage(desk_input(observed=at - timedelta(minutes=5)))
    desk = MarketDesk(company, AsyncMock(), storage, filings=lambda at: [], clock=lambda: at)
    await desk.tick()
    assert [a["id"] for a in storage.board["alerts"]] == ["2026-11-27/action/KOSDAQ/sidecar"]
    assert [(n["slot"], n["kind"]) for n in notes_in(company)] == [(None, "urgent")]
    await desk.tick()
    assert len(notes_in(company)) == 1


def written_note(conn, key, at, sources):
    article = {"id": key, "headline": "코스닥 급등", "published_at": at.isoformat(), "sources": sources}
    conn.execute("""INSERT INTO market_desk_revisions(revision,issue_id,aliases,day,created_at,packet,article,state,next_at)
        VALUES(%s,%s,%s,%s,%s,%s,%s,'written',%s)""", (key, key, [key], at.date(), at,
                                                        json.dumps({"kind": "note", "slot": None}), json.dumps(article), at))


@pytest.mark.integration
async def test_reports_the_notes_quoted_are_listed_once_as_market_news(company):
    company.settings.insight_notes_enabled = True
    wire = {"title": "코스닥 매수 사이드카 발동", "publisher": "시장통신", "url": "https://example.org/wire",
            "published_at": "2026-11-27T10:25:00+09:00"}
    evening = {**wire, "title": "뉴욕증시 로봇주 강세", "url": "https://example.org/evening", "published_at": "2026-11-26T19:40:00+09:00"}
    filing = {**wire, "title": "레인보우로보틱스/단일판매ㆍ공급계약체결", "url": "https://dart.example/1", "role": "공시"}
    with company.db.transaction() as conn:
        written_note(conn, "note/2026-11-27/open", AT - timedelta(minutes=80),
                     [{**wire, "role": "시장 원문"}, {**evening, "role": "종목 원문"}, filing])
        written_note(conn, "note/2026-11-27/morning", AT, [{**wire, "role": "시장 원문"}])
    storage = Storage(desk_input())
    desk = MarketDesk(company, AsyncMock(), storage, filings=lambda at: [], clock=lambda: AT)
    await desk.tick()
    market = [(i["headline"], i["fact"], i["category"], i["origin"], i["priority"])
              for i in storage.board["news"] if i["id"].startswith("market-")]
    assert market == [("코스닥 매수 사이드카 발동", "09:42·11:02 장중 노트가 근거로 인용한 원문입니다.", "시장", "desk", "major"),
                      ("뉴욕증시 로봇주 강세", "11월 27일 09:42 장중 노트가 근거로 인용한 원문입니다.", "시장", "desk", "major")]
    assert all("role" not in s for i in storage.board["news"] for s in i["sources"])
    with company.db.transaction() as conn:
        assert desk.store.quoted(conn, AT, {"https://example.org/wire", "https://example.org/evening"}) == []
    company.settings.insight_notes_enabled = False
    await desk.tick()
    assert not [i for i in storage.board["news"] if i["id"].startswith("market-")]


class _Refusing:
    def __init__(self, code):
        self.code, self.calls = code, 0

    async def run(self, request):
        from quant_company.contracts import ProviderFault

        self.calls += 1
        raise ProviderFault(self.code, "refused", retry_after_seconds=60)


def note_states(company):
    with company.db.transaction() as conn:
        return [r["state"] for r in conn.execute("""SELECT state FROM market_desk_revisions
            WHERE packet->>'kind'='note' ORDER BY created_at""").fetchall()]


@pytest.mark.integration
@pytest.mark.parametrize(("code", "state"), [("quota", "failed"), ("unavailable", "failed"), ("busy", "queued")])
async def test_a_note_refused_for_quota_skips_its_round_and_a_busy_lane_waits(company, code, state):
    company.settings.insight_notes_enabled = True
    provider = _Refusing(code)
    desk = MarketDesk(company, provider, Storage(desk_input()), filings=lambda at: [], clock=lambda: AT)
    await desk.tick()
    await desk.write_article()
    assert provider.calls == 1 and note_states(company) == [state]


@pytest.mark.integration
async def test_a_note_that_waited_too_long_is_dropped_without_a_call(company):
    company.settings.insight_notes_enabled = True
    provider = AsyncMock()
    clock = {"now": AT}
    desk = MarketDesk(company, provider, Storage(desk_input()), filings=lambda at: [], clock=lambda: clock["now"])
    await desk.tick()
    clock["now"] = AT + note.LATE + timedelta(minutes=1)
    await desk.write_article()
    assert provider.run.await_count == 0 and note_states(company) == ["failed"]


@pytest.mark.integration
@pytest.mark.parametrize(("observed", "state"), [("15:45", "skipped"), ("15:55", "queued")])
async def test_the_closing_note_waits_for_a_map_that_has_the_close(company, observed, state):
    """KRX 시세가 약 20분 늦다: 15:45 수집은 15:30 종가 전이라 그 지도로는 마감 노트를 쓰지 않는다."""
    company.settings.insight_notes_enabled = True
    at = datetime(2026, 11, 27, 16, 6, tzinfo=KST)
    seen = datetime(2026, 11, 27, int(observed[:2]), int(observed[3:]), tzinfo=KST)
    desk = MarketDesk(company, AsyncMock(), Storage(desk_input(observed=seen)), filings=lambda at: [], clock=lambda: at)
    await desk.tick()
    assert [(n["slot"], n["state"]) for n in notes_in(company)] == [("close", state)]
