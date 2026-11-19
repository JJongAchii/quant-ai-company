"""Grading of signal slots: the official KOSPI rows through a fake lake reader, KODEX 200 bars from fake Yahoo charts,
and the stored returns on real PostgreSQL. No network and no qdata: every provider here is simulated."""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from quant_company.briefing import data
from quant_company.signals import lake
from quant_company.signals.grading import kodex_bars, long_return_bp, outcome
from quant_company.signals.runner import SignalRunner
from quant_company.signals.store import SignalStore, slot_id

from .signals_fixtures import KST, am_project, clock, recorded, signals  # noqa: F401
from .test_signals_turns import FRIDAY, MONDAY, ask, rows


def bar_chart(bars, events=None):
    """bars: [(date, open, close, volume)] stamped 09:00 KST; events: ex-dividend dates, or None for no events key."""
    stamps = [int(datetime(d.year, d.month, d.day, 9, tzinfo=KST).timestamp()) for d, *_ in bars]
    result = {"meta": {}, "timestamp": stamps, "indicators": {"quote": [{
        "open": [b[1] for b in bars], "close": [b[2] for b in bars], "volume": [b[3] for b in bars]}]}}
    if events is not None:
        result["events"] = {"dividends": {str(s): {"amount": 100, "date": s} for s in (
            int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp()) for d in events)}} if events else {}
    raw = json.dumps({"chart": {"result": [result]}}).encode()
    return {"ok": True, "payload": json.loads(raw), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


SOURCE = {"size_bytes": 1000, "etag": "synthetic"}


def official(*items):
    """items: (date, open, close)."""
    return {"ok": True, "source": SOURCE, "rows": [{"date": str(d), "open": str(o), "close": str(c)}
                                                    for d, o, c in items]}


class Feeds:
    """The fake lake reader and chart fetch, changeable between ticks."""

    def __init__(self, lake_result, chart):
        self.lake, self.chart, self.calls = lake_result, chart, []

    def reader(self, root, start, end):
        self.calls.append(("lake", start, end))
        return self.lake

    def fetch(self, symbol, **kwargs):
        if symbol == "069500.KS":
            self.calls.append(("chart", kwargs))
            return self.chart
        return recorded("intraday", MONDAY)(symbol)


def returns(company):
    return {(str(r["signal_id"]), r["instrument"]): r for r in rows(company, "SELECT * FROM trading_signal_returns")}


def test_returns_and_outcomes():
    assert long_return_bp(Decimal("100"), Decimal("101")) == Decimal("100.00")
    assert long_return_bp(Decimal("3"), Decimal("3.001")) == Decimal("3.33")
    assert outcome("short", Decimal("-31.00")) == "hit"
    assert outcome("long", Decimal("-1")) == "miss"
    assert outcome("long", Decimal("0")) == "tie"


async def test_kospi_final_needs_official_rows(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    ask(signals, clock, MONDAY, "intraday")
    feeds = Feeds(official((date(2026, 11, 13), 3900, 3910)),
                  bar_chart([(MONDAY, 52000, 52500, 100)], events=[]))
    runner = SignalRunner(signals, fetch=feeds.fetch, reader=feeds.reader)
    clock["at"] = datetime(2026, 11, 16, 9, 29, tzinfo=UTC)  # 18:29 KST: not yet
    assert (await runner.tick())["graded"] == 0 and feeds.calls == []
    clock["at"] = datetime(2026, 11, 16, 9, 30, tzinfo=UTC)
    assert (await runner.tick())["graded"] == 2
    kospi = returns(signals)[(str(slot_id(MONDAY, "intraday")), "KOSPI")]
    assert (kospi["state"], kospi["detail"], kospi["long_bp"]) == ("pending", "공식 자료 없음", None)
    assert returns(signals)[(str(slot_id(MONDAY, "intraday")), "KODEX200")]["state"] == "final"
    clock["at"] = datetime(2026, 11, 16, 9, 45, tzinfo=UTC)  # inside the 30 minute pending wait
    assert (await runner.tick())["graded"] == 0
    feeds.lake = official((MONDAY, 3920, 3950))
    clock["at"] = datetime(2026, 11, 16, 10, 1, tzinfo=UTC)
    assert (await runner.tick())["graded"] == 1  # the final KODEX 200 row is left as it was
    kospi = returns(signals)[(str(slot_id(MONDAY, "intraday")), "KOSPI")]
    assert (kospi["state"], kospi["entry_price"], kospi["exit_price"], kospi["long_bp"]) == (
        "final", Decimal("3920"), Decimal("3950"), Decimal("76.53"))
    assert kospi["receipt"] == SOURCE
    kodex = returns(signals)[(str(slot_id(MONDAY, "intraday")), "KODEX200")]
    assert kodex["receipt"] == feeds.chart["sha256"]
    assert feeds.calls[1][1] == {"params": "interval=1d&range=1mo&events=div"}
    clock["at"] += timedelta(hours=1)
    assert (await runner.tick())["graded"] == 0  # both final: nothing is due and nothing is overwritten


async def test_suspicious_yahoo_bar_is_pending(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    ask(signals, clock, MONDAY, "intraday")
    feeds = Feeds(official((MONDAY, 3920, 3950)), bar_chart([(MONDAY, 52000, 52000, 10)], events=[]))
    clock["at"] = datetime(2026, 11, 16, 9, 30, tzinfo=UTC)
    await SignalRunner(signals, fetch=feeds.fetch, reader=feeds.reader).tick()
    kodex = returns(signals)[(str(slot_id(MONDAY, "intraday")), "KODEX200")]
    assert (kodex["state"], kodex["detail"]) == ("pending", "의심 일봉")
    assert returns(signals)[(str(slot_id(MONDAY, "intraday")), "KOSPI")]["state"] == "final"


async def test_missing_bar_never_uses_a_nearby_day(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    ask(signals, clock, MONDAY, "intraday")
    feeds = Feeds(official((date(2026, 11, 13), 3900, 3910), (date(2026, 11, 17), 3920, 3950)),
                  bar_chart([(date(2026, 11, 13), 51000, 51500, 5), (date(2026, 11, 17), 52000, 52500, 5)]))
    clock["at"] = datetime(2026, 11, 16, 9, 30, tzinfo=UTC)
    await SignalRunner(signals, fetch=feeds.fetch, reader=feeds.reader).tick()
    assert {r["state"] for r in returns(signals).values()} == {"pending"}


async def test_overnight_dividend_excluded(signals, clock):  # noqa: F811
    am_project(signals, FRIDAY)
    ask(signals, clock, FRIDAY, "overnight")
    friday = slot_id(FRIDAY, "overnight")
    lake_rows = official((FRIDAY, 3900, 3910), (MONDAY, 3920, 3950))
    bars = [(FRIDAY, 52000, 52400, 5), (MONDAY, 52300, 52800, 5)]
    clock["at"] = datetime(2026, 11, 16, 9, 30, tzinfo=UTC)  # grading day is exit_day (Monday), not Friday
    feeds = Feeds(lake_rows, bar_chart(bars, events=[MONDAY]))
    await SignalRunner(signals, fetch=feeds.fetch, reader=feeds.reader).tick()
    kospi, kodex = returns(signals)[(str(friday), "KOSPI")], returns(signals)[(str(friday), "KODEX200")]
    assert (kospi["state"], kospi["entry_price"], kospi["exit_price"]) == ("final", Decimal("3910"), Decimal("3920"))
    assert (kodex["state"], kodex["detail"], kodex["long_bp"]) == ("excluded", "분배락", None)
    # Yahoo omits events when the window has no distribution: the return is final, with no detail (R18).
    with signals.db.transaction() as conn:
        conn.execute("DELETE FROM trading_signal_returns")
    feeds.chart = bar_chart(bars)
    await SignalRunner(signals, fetch=feeds.fetch, reader=feeds.reader).tick()
    kodex = returns(signals)[(str(friday), "KODEX200")]
    assert (kodex["state"], kodex["detail"], kodex["long_bp"]) == ("final", None, Decimal("-19.08"))


async def test_overnight_waits_for_the_exit_day(signals, clock):  # noqa: F811
    am_project(signals, FRIDAY)
    ask(signals, clock, FRIDAY, "overnight")
    feeds = Feeds(official((FRIDAY, 3900, 3910)), bar_chart([(FRIDAY, 52000, 52400, 5)]))
    clock["at"] = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)  # Friday 19:00 KST, the exit is Monday
    assert (await SignalRunner(signals, fetch=feeds.fetch, reader=feeds.reader).tick())["graded"] == 0
    assert feeds.calls == []


def test_save_returns_never_overwrites_a_final_row(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    ask(signals, clock, MONDAY, "intraday")
    store, sid = SignalStore(signals), slot_id(MONDAY, "intraday")
    base = {"signal_id": sid, "instrument": "KOSPI", "receipt": SOURCE}
    final = {**base, "state": "final", "entry_price": Decimal("10"), "exit_price": Decimal("11"),
             "long_bp": Decimal("1000.00")}
    pending = {**base, "state": "pending", "detail": "공식 자료 없음"}
    assert store.save_returns([pending]) == 1 and store.save_returns([pending]) == 1
    assert store.save_returns([final]) == 1
    assert store.save_returns([pending]) == 0 and store.save_returns([final]) == 0
    [row] = rows(signals, "SELECT * FROM trading_signal_returns")
    assert (row["state"], row["long_bp"], row["detail"]) == ("final", Decimal("1000.00"), None)


def test_slot_without_snapshot_is_not_graded(signals, clock):  # noqa: F811
    store = SignalStore(signals)
    store.schedule(datetime(2026, 11, 16, 0, 30, tzinfo=KST))
    assert store.grading_due(datetime(2026, 11, 17, 3, 0, tzinfo=UTC)) == []


def test_lake_budget_refusal():
    class Api:
        def inspect_dataset(self, name):
            return {"source": {"size_bytes": 5 * 1024 * 1024}, "row_count": 1, "columns": [], "date_bounds": {}}

    assert lake.read(Api(), date(2026, 11, 13), date(2026, 11, 16)) == {
        "ok": False, "error": "dataset_exceeds_signal_budget"}


def test_lake_reads_kospi_rows_only():
    import pandas as pd

    class Api:
        def inspect_dataset(self, name):
            return {"source": SOURCE, "row_count": 3, "columns": [], "date_bounds": {}}

        def load_krx_index(self, **kwargs):
            return pd.DataFrame({"date": pd.to_datetime(["2026-11-16", "2026-11-16"]), "index": ["KOSPI", "KOSDAQ"],
                                 "open": [3920.5, 900.0], "close": [3950.25, 910.0]})

    assert lake.read(Api(), date(2026, 11, 16), date(2026, 11, 16)) == {
        "ok": True, "source": SOURCE, "rows": [{"date": "2026-11-16", "open": "3920.5", "close": "3950.25"}]}


def test_kodex_bars_use_seoul_dates():
    bars, dividends = kodex_bars(bar_chart([(MONDAY, 52000, 52500, 7)], events=[MONDAY])["payload"])
    assert bars == {MONDAY: {"open": Decimal("52000"), "close": Decimal("52500"), "volume": Decimal("7")}}
    assert dividends == {MONDAY}
    assert kodex_bars(bar_chart([(MONDAY, 1, 2, 3)])["payload"])[1] is None


def test_query_refactor_keeps_behavior():
    assert data.query("", object()) == {"ok": False, "error": "lake_not_connected"}
    assert data.run_lake_module("", "quant_company.signals.lake", {}) == {"ok": False, "error": "lake_not_connected"}


async def test_failed_fetch_missing_bar_and_suspicious_bar_are_told_apart(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    ask(signals, clock, MONDAY, "intraday")
    sid = str(slot_id(MONDAY, "intraday"))
    good = official((MONDAY, 3920, 3950))
    cases = [({"ok": False, "error": "http_status", "http_status": 429}, "시세 조회 실패", {"error": "http_status"}),
             ({"ok": True, "payload": {"chart": {"result": None}}, "sha256": "x"}, "시세 조회 실패",
              {"error": "chart_parse_or_policy_error"}),
             (bar_chart([(FRIDAY, 51000, 51500, 5)], events=[]), "일봉 없음", None),
             (bar_chart([(MONDAY, 52000, 52000, 10)], events=[]), "의심 일봉", None)]
    for number, (chart, detail, receipt) in enumerate(cases):
        with signals.db.transaction() as conn:
            conn.execute("DELETE FROM trading_signal_returns")
        clock["at"] = datetime(2026, 11, 16, 9, 30, tzinfo=UTC) + timedelta(days=number)
        feeds = Feeds(good, chart)
        await SignalRunner(signals, fetch=feeds.fetch, reader=feeds.reader).tick()
        kodex = returns(signals)[(sid, "KODEX200")]
        assert (kodex["state"], kodex["detail"]) == ("pending", detail)
        assert kodex["receipt"] == (receipt or chart["sha256"])


def test_grading_gate_is_1830_kst_whatever_the_session_timezone(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    ask(signals, clock, MONDAY, "intraday")
    store = SignalStore(signals)
    for zone in ("UTC", "Asia/Seoul", "America/New_York"):
        with signals.db.transaction() as conn:
            conn.execute(f"SET LOCAL timezone='{zone}'")
            for at, due in ((datetime(2026, 11, 16, 9, 29, tzinfo=UTC), 0), (datetime(2026, 11, 16, 9, 30, tzinfo=UTC), 1)):
                found = conn.execute("""SELECT count(*) n FROM trading_signals s WHERE s.exit_at<%s AND s.baselines IS NOT NULL
                    AND (((CASE WHEN s.kind='intraday' THEN s.session_day ELSE s.exit_day END)+time '18:30')
                         AT TIME ZONE 'Asia/Seoul')<=%s""", (at, at)).fetchone()["n"]
                assert found == due, zone
    assert store.grading_due(datetime(2026, 11, 16, 9, 29, tzinfo=UTC)) == []
    assert [str(r["id"]) for r in store.grading_due(datetime(2026, 11, 16, 9, 30, tzinfo=UTC))] == [
        str(slot_id(MONDAY, "intraday"))]


async def test_non_positive_bar_is_suspicious(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    ask(signals, clock, MONDAY, "intraday")
    feeds = Feeds(official((MONDAY, 3920, 3950)), bar_chart([(MONDAY, 0, 52000, 0)], events=[]))
    clock["at"] = datetime(2026, 11, 16, 9, 30, tzinfo=UTC)
    assert (await SignalRunner(signals, fetch=feeds.fetch, reader=feeds.reader).tick())["graded"] == 2
    kodex = returns(signals)[(str(slot_id(MONDAY, "intraday")), "KODEX200")]
    assert (kodex["state"], kodex["detail"]) == ("pending", "의심 일봉")


async def test_one_failing_slot_does_not_stop_another(signals, clock, monkeypatch):  # noqa: F811
    from quant_company.signals import grading

    am_project(signals, MONDAY)
    ask(signals, clock, MONDAY, "intraday")
    ask(signals, clock, MONDAY, "overnight")
    real = grading.grade_slot

    def flaky(row, lake, chart):
        if row["kind"] == "intraday":
            raise ArithmeticError("boom")
        return real(row, lake, chart)

    monkeypatch.setattr(grading, "grade_slot", flaky)
    feeds = Feeds(official((MONDAY, 3920, 3950)), bar_chart([(MONDAY, 52000, 52500, 100)], events=[]))
    clock["at"] = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)
    await SignalRunner(signals, fetch=feeds.fetch, reader=feeds.reader).tick()
    assert {sid for sid, _ in returns(signals)} == {str(slot_id(MONDAY, "overnight"))}
