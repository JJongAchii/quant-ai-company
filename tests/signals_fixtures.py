"""Shared fixtures for the Analyst trading-signal tests: a briefing company with signals on, a delivered AM edition, a
recorded quote snapshot and a valid proposal. Synthetic fixture data only, never a real market record."""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta

import pytest
from psycopg.types.json import Jsonb

from quant_company.briefing import schedule
from quant_company.briefing.contracts import BRIEFER
from quant_company.briefing.store import BriefStore
from quant_company.company import load_roles, stable
from quant_company.config import Settings
from quant_company.signals.snapshot import CATALOGUE, read_snapshot

KST = schedule.KST
DAY = date(2026, 11, 20)
# DAY's snapshot times: 07:30 KST (intraday) and 15:00 KST (overnight).
OBSERVED = {"intraday": datetime(2026, 11, 19, 22, 30, tzinfo=UTC),
            "overnight": datetime(2026, 11, 20, 6, 0, tzinfo=UTC)}
# key: (exchange timezone, previous close, last price)
QUOTES = {
    "es": ("America/Chicago", 6812.25, 6848.5), "nq": ("America/Chicago", 25010.75, 25190.25),
    "spy": ("America/New_York", 676.3, 680.12), "ewy": ("America/New_York", 79.85, 80.46),
    "usdkrw": ("Europe/London", 1461.2, 1458.7), "kospi": ("Asia/Seoul", 3925.4, 3951.08),
    "kodex200": ("Asia/Seoul", 52310, 52645), "kosdaq": ("Asia/Seoul", 871.2, 876.43),
    "n225": ("Asia/Tokyo", 49210.5, 49544.9), "hsi": ("Asia/Hong_Kong", 25630.1, 25811.6),
    "shanghai": ("Asia/Shanghai", 3940.2, 3951.7),
}
# Minutes between a quote's source time and the snapshot: at 07:30 KST the CME futures are in their daily break
# (07:00 value), SPY/EWY show the 06:00 KST US close and KRX the previous 15:30 close; at 15:00 the cash markets trade.
AGE_MINUTES = {
    "intraday": {"es": 30, "nq": 30, "usdkrw": 2, "spy": 90, "ewy": 90, "kospi": 960, "kodex200": 960, "kosdaq": 960},
    "overnight": {"es": 1, "nq": 1, "usdkrw": 1, "spy": 510, "ewy": 510, "kospi": 1, "kodex200": 1, "kosdaq": 1,
                  "n225": 1, "hsi": 1, "shanghai": 1},
}


@pytest.fixture
def clock(monkeypatch):
    """The one signals clock (briefing.schedule.utcnow), at 07:30 KST on DAY until a test moves it."""
    value = {"at": OBSERVED["intraday"]}
    monkeypatch.setattr(schedule, "utcnow", lambda: value["at"])
    return value


@pytest.fixture
def signals(company, clock):
    s = company.settings
    s.briefing_enabled = True
    s.briefing_publish_enabled = True
    s.briefing_search_enabled = False
    s.briefing_channel_id = "CQUANT"
    s.briefing_owner_user = "UHUMAN"
    s.analyst_signals_enabled = True
    company.roles[BRIEFER] = load_roles(Settings(briefing_enabled=True))[BRIEFER]
    return company


def am_project(company, day, thread_ts="100.001"):
    """The committed AM edition of day and its project. thread_ts is the delivered parent post; None means not yet."""
    s = company.settings
    edition = next(e for e in schedule.editions(day, s.briefing_channel_id, s.briefing_owner_user) if e.kind == "am")
    with company.db.transaction() as conn:
        project = conn.execute("""INSERT INTO projects(id,title,instruction,owner_user,channel,thread_ts)
            VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""", (stable("brief-project:" + edition.id), f"{day} am 시장 브리핑",
                                                       "이 발간본과 원문을 기준으로 후속 질문에 답한다.",
                                                       s.briefing_owner_user, s.briefing_channel_id, thread_ts)).fetchone()
        conn.execute("""INSERT INTO brief_editions(id,day,kind,channel,owner_user,definition,due_at,cutoff,expires_at,
            policy_digest,publish,state,project_id,committed_at)
            VALUES (%s,%s,'am',%s,%s,%s,%s,%s,%s,%s,true,'committed',%s,%s)""",
                     (edition.id, day, s.briefing_channel_id, s.briefing_owner_user,
                      Jsonb(edition.model_dump(mode="json")), edition.due_at, edition.cutoff, edition.expires_at,
                      BriefStore(company).policy(), project["id"], edition.due_at))
    return project


def observed(kind, day=DAY):
    """The snapshot time of day's slot on a normal session: OBSERVED moved to day."""
    return OBSERVED[kind] + timedelta(days=(day - DAY).days)


def _chart(key, kind, day=DAY):
    zone, previous, price = QUOTES[key]
    at = observed(kind, day) - timedelta(minutes=AGE_MINUTES[kind][key])
    bars = [at - timedelta(days=1), at]
    return {"chart": {"result": [{
        "meta": {"symbol": CATALOGUE[key].symbol, "exchangeTimezoneName": zone, "regularMarketPrice": price,
                 "regularMarketTime": int(at.timestamp())},
        "timestamp": [int(bar.timestamp()) for bar in bars],
        "indicators": {"quote": [{"open": [previous, price], "close": [previous, price]}]}}]}}


def recorded(kind, day=DAY):
    """A chart fetch that replays the recorded Yahoo responses of day's snapshot for this slot kind."""
    symbols = {instrument.symbol: key for key, instrument in CATALOGUE.items()}

    def fetch(symbol):
        raw = json.dumps(_chart(symbols[symbol], kind, day)).encode()
        return {"ok": True, "payload": json.loads(raw), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    return fetch


def snapshot(kind, day=DAY):
    """The snapshot read_snapshot froze from the recorded responses at the slot's snapshot time. A past day keeps the
    snapshot source visible to the DB now() filters of Company._context and _check_sources."""
    return read_snapshot(kind, recorded(kind, day), clock=lambda: observed(kind, day))


def proposal(**over):
    """A valid SignalProposal dict for DAY's intraday slot, citing its snapshot source. Its one reason gives +10 points,
    so the computed confidence is 60 for either kind and direction."""
    kind, day = over.get("kind", "intraday"), over.get("session_day", str(DAY))
    source = f"signal:{day}:{kind}:snapshot"
    value = {"kind": kind, "session_day": day, "direction": "long",
             "reasons": [{"text": "미국 지수 선물이 전일 종가보다 0.5% 높다", "points": 10, "source_ids": [source]}],
             "invalidation": "시가가 전일 종가 아래에서 출발하면", "source_ids": [source]}
    return {**value, **over}
