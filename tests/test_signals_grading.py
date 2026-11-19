"""Grading of signal slots: the official KOSPI rows through a fake lake reader, KODEX 200 bars from fake Yahoo charts,
and the stored returns on real PostgreSQL. No network and no qdata: every provider here is simulated."""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from quant_company.briefing import data
from quant_company.company import stable
from quant_company.signals import lake
from quant_company.signals.grading import kodex_bars, long_return_bp, outcome, sign_test_p, summarize
from quant_company.signals.render import SCORECARD_FOOTER, scorecard_post
from quant_company.signals.runner import SignalRunner
from quant_company.signals.store import SignalStore, slot_id

from .signals_fixtures import KST, am_project, clock, proposal, recorded, signals  # noqa: F401
from .test_signals_store import answer_at, ask_at, fallback_at, post_id
from .test_signals_turns import FRIDAY, MONDAY, ask, one, rows


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


def slot_item(direction, bp, *, received=None, timing="on_time", fallback=False, kind="intraday", follow_us="long",
              state="final", unparsed=False, reason=None):
    """One summarize() input: the Analyst's direction (None without a proposal) and the KOSPI long return in bp."""
    return {"kind": kind, "analyst": direction, "timing": timing if direction else None, "unparsed": unparsed,
            "received": received or direction, "fallback": fallback, "fallback_reason": reason,
            "baselines": {"always_long": "long", "follow_us": follow_us if kind == "intraday" else None},
            "returns": {"KOSPI": {"state": state, "long_bp": Decimal(bp) if state == "final" else None}}}


def versus_slots(wins, losses, **kwargs):
    """Analyst short against always long: a short hit when the long return is negative."""
    return ([slot_item("short", "-10", **kwargs) for _ in range(wins)]
            + [slot_item("short", "10", **kwargs) for _ in range(losses)])


def test_sign_test_values():
    assert sign_test_p(5, 4) == 1.0
    assert round(sign_test_p(9, 1), 4) == 0.0215
    assert sign_test_p(0, 0) == 1.0
    assert sign_test_p(10, 0) == min(1, 2 / 1024)


def test_summary_counts_and_received():
    summary = summarize([slot_item("short", "-30"), slot_item(None, "-10", received="long", fallback=True,
                                                               reason="앞선 질문에 답하는 중")])
    kospi = summary["intraday"]["instruments"]["KOSPI"]
    assert {k: kospi["analyst"][k] for k in ("n", "hits", "misses", "ties")} == {"n": 1, "hits": 1, "misses": 0, "ties": 0}
    assert (kospi["analyst"]["mean_bp"], kospi["analyst"]["sum_bp"]) == (Decimal("30.00"), Decimal("30"))
    assert (kospi["received"]["n"], kospi["received"]["hits"], kospi["received"]["misses"]) == (2, 1, 1)
    assert (kospi["always_long"]["hits"], kospi["always_long"]["misses"]) == (0, 2)
    assert summary["intraday"]["counts"] == {"on_time": 1, "late": 0, "fallback": 1, "after_entry": 0, "unparsed": 0,
                                             "pending": 0}
    assert summary["intraday"]["fallback_reasons"] == {"앞선 질문에 답하는 중": 1}
    assert summary["overnight"]["counts"]["on_time"] == 0 and "follow_us" not in summary["overnight"]["instruments"]["KOSPI"]


def test_summary_leaves_out_ties_pending_after_entry_and_unparsed():
    summary = summarize([slot_item("long", "0"), slot_item("long", "5", timing="after_entry"),
                         slot_item("long", "5", state="pending"), slot_item(None, "5", unparsed=True),
                         slot_item("long", "5", timing="late"), slot_item("short", "5", follow_us=None)])
    kospi = summary["intraday"]["instruments"]["KOSPI"]
    assert (kospi["analyst"]["n"], kospi["analyst"]["hits"], kospi["analyst"]["misses"], kospi["analyst"]["ties"]) == (
        2, 1, 1, 1)
    assert kospi["follow_us"]["n"] == 3 and kospi["always_long"]["n"] == 4  # pending left out; null follow_us left out
    assert summary["intraday"]["counts"] == {"on_time": 3, "late": 1, "fallback": 0, "after_entry": 1, "unparsed": 1,
                                             "pending": 1}
    # long vs follow_us long: never different; the short slot has no follow_us and is left out of that comparison
    assert kospi["versus"]["follow_us"] == {"wins": 0, "losses": 0, "p": 1.0}
    assert kospi["versus"]["always_long"] == {"wins": 0, "losses": 1, "p": 1.0}


def card(slots, lines=()):
    return scorecard_post(date(2026, 11, 16), list(lines), summarize(slots))


def test_scorecard_wording():
    better = card(versus_slots(9, 1))
    assert "항상 롱과 다르게 건 날" in better and "장중 10일 중 9일 맞음 → 항상 롱보다 낫다" in better
    assert "미국 따라가기와 다르게 건 날" in better and "→ 미국 따라가기보다 낫다" in better
    assert "아직 우연과 구분할 수 없음" in card(versus_slots(5, 4))
    assert "항상 롱보다 못하다" in card(versus_slots(1, 9))
    assert card(versus_slots(5, 4)).endswith(SCORECARD_FOOTER)
    assert card(versus_slots(9, 1)).startswith("📊 신호 채점 · 11월 16일\n")


def deliver(company, message, stamp):
    """Mark an outbox post delivered. stamp: the Slack ts as a datetime, or a 1-tuple for no ts and a dispatch start."""
    with company.db.transaction() as conn:
        if isinstance(stamp, datetime):
            conn.execute("UPDATE outbox SET status='delivered',sent_ts=%s WHERE id=%s",
                         (f"{stamp.timestamp():.6f}", message))
        else:
            conn.execute("UPDATE outbox SET status='delivered',sent_ts=NULL,started_at=%s WHERE id=%s",
                         (stamp[0], message))


def put_returns(company, day, kind, kospi, kodex, detail=None):
    """kospi/kodex: (state, long_bp or None)."""
    sid = slot_id(day, kind)
    SignalStore(company).save_returns([
        {"signal_id": sid, "instrument": name, "state": state, "long_bp": None if bp is None else Decimal(bp),
         "detail": detail if name == "KODEX200" else None, "receipt": {"x": 1}}
        for name, (state, bp) in (("KOSPI", kospi), ("KODEX200", kodex))])


def monday_slot(signals, clock, direction="short"):  # noqa: F811
    """Monday's intraday slot with an on-time Analyst signal, delivered before the open."""
    am_project(signals, MONDAY)
    turn = ask_at(signals, clock, datetime(2026, 11, 16, 8, 0, tzinfo=KST))
    answer_at(signals, clock, turn, datetime(2026, 11, 16, 8, 10, tzinfo=KST),
              proposal(session_day=str(MONDAY), direction=direction))
    deliver(signals, post_id("analyst"), datetime(2026, 11, 16, 8, 11, tzinfo=KST))


def at(hour, minute=0, day=MONDAY):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST)


def report_posts(company):
    return rows(company, """SELECT m.id,m.text,o.status,o.project_id FROM messages m JOIN outbox o ON o.id=m.id
        WHERE m.id=%s""", stable(f"trading-signal-report:{MONDAY}"))


def test_report_once_and_cutoff(signals, clock):  # noqa: F811
    monday_slot(signals, clock)
    put_returns(signals, MONDAY, "intraday", ("pending", None), ("final", "-28.00"))
    store = SignalStore(signals)
    assert store.report(at(18, 29)) is False and store.report(at(19)) is False and report_posts(signals) == []
    assert rows(signals, "SELECT * FROM trading_signal_reports") == []
    assert store.report(at(21)) is True
    [post] = report_posts(signals)
    assert "채점 보류" in post["text"] and post["text"].endswith(SCORECARD_FOOTER)
    assert post["text"].startswith("📊 신호 채점 · 11월 16일\n")
    assert str(post["project_id"]) == str(one(signals, "SELECT project_id FROM trading_signals WHERE id=%s",
                                                  slot_id(MONDAY, "intraday"))["project_id"])
    [report] = rows(signals, "SELECT * FROM trading_signal_reports")
    assert (report["day"], str(report["message_id"])) == (MONDAY, str(post["id"]))
    assert report["summary"]["intraday"]["counts"]["pending"] == 1
    assert store.report(at(21, 5)) is False and len(report_posts(signals)) == 1


def test_report_posts_as_soon_as_everything_is_final(signals, clock):  # noqa: F811
    monday_slot(signals, clock)
    store = SignalStore(signals)
    assert store.report(at(18, 40)) is False  # nothing graded yet counts as pending
    put_returns(signals, MONDAY, "intraday", ("final", "-31.00"), ("final", "-28.00"))
    assert store.report(at(18, 45)) is True
    [post] = report_posts(signals)
    assert ("장중(숏) KOSPI 시가→종가 -0.31% → 신호 +0.31% ✅ · KODEX 200 신호 +0.28% ✅") in post["text"].split("\n")
    assert ("Analyst 누적 장중 KOSPI 1전 1승(100%) 평균 +31.00bp 누적 +31.00bp"
            " · 항상 롱 1전 0승(0%) 평균 -31.00bp 누적 -31.00bp"
            " · 미국 따라가기 1전 0승(0%) 평균 -31.00bp 누적 -31.00bp") in post["text"]
    assert "받으신 신호 기준 KOSPI 장중 1전 1승(100%) 평균 +31.00bp 누적 +31.00bp" in post["text"].split("\n")
    assert "채점 보류" not in post["text"]
    assert "정시 1 · 늦음 0 · 임시 0" in post["text"]


def test_miss_tie_and_excluded_are_marked(signals, clock):  # noqa: F811
    monday_slot(signals, clock, "long")
    put_returns(signals, MONDAY, "intraday", ("final", "-31.00"), ("excluded", None), detail="분배락")
    assert SignalStore(signals).report(at(19)) is True
    text = report_posts(signals)[0]["text"]
    assert "장중(롱) KOSPI 시가→종가 -0.31% → 신호 -0.31% ❌ · KODEX 200 분배락 제외" in text.split("\n")


def test_tie_line(signals, clock):  # noqa: F811
    monday_slot(signals, clock, "long")
    sid = slot_id(MONDAY, "intraday")
    with signals.db.transaction() as conn:
        for instrument in ("KOSPI", "KODEX200"):
            conn.execute("INSERT INTO trading_signal_returns(signal_id,instrument,state,long_bp) VALUES (%s,%s,'final',0)",
                         (sid, instrument))
    assert SignalStore(signals).report(at(19)) is True
    assert "장중(롱) KOSPI 시가→종가 +0.00% → 신호 +0.00% ➖ · KODEX 200 신호 +0.00% ➖" in report_posts(signals)[0]["text"]


def test_interim_only_line_is_marked_and_no_signal_shows_the_move(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    ask_at(signals, clock, at(8, 0))
    fallback_at(signals, clock)  # the interim post, no Analyst answer
    deliver(signals, post_id("fallback"), at(8, 29))
    put_returns(signals, MONDAY, "intraday", ("final", "42.00"), ("final", "45.00"))
    assert SignalStore(signals).report(at(19)) is True
    text = report_posts(signals)[0]["text"]
    assert "장중(임시 롱) KOSPI 시가→종가 +0.42% → 신호 +0.42% ✅ · KODEX 200 신호 +0.45% ✅" in text.split("\n")
    assert "정시 0 · 늦음 0 · 임시 1" in text
    # No Analyst proposal and no interim post at all: only the market move.
    with signals.db.transaction() as conn:
        conn.execute("DELETE FROM trading_signal_reports")
        conn.execute("DELETE FROM outbox WHERE id=%s", (stable(f"trading-signal-report:{MONDAY}"),))
        conn.execute("DELETE FROM messages WHERE id=%s", (stable(f"trading-signal-report:{MONDAY}"),))
        conn.execute("UPDATE trading_signals SET fallback_message_id=NULL WHERE id=%s", (slot_id(MONDAY, "intraday"),))
    assert SignalStore(signals).report(at(19)) is True
    assert "장중 KOSPI 시가→종가 +0.42% · KODEX 200 시가→종가 +0.45%" in report_posts(signals)[0]["text"].split("\n")


def test_late_final_is_reported_as_previous_pending(signals, clock):  # noqa: F811
    monday_slot(signals, clock)
    put_returns(signals, MONDAY, "intraday", ("pending", None), ("final", "-28.00"))
    store = SignalStore(signals)
    assert store.report(at(21)) is True
    put_returns(signals, MONDAY, "intraday", ("final", "-31.00"), ("final", "-28.00"))
    tuesday = date(2026, 11, 17)
    assert store.report(at(18, 40, tuesday)) is True
    [post] = rows(signals, "SELECT text FROM messages WHERE id=%s", stable(f"trading-signal-report:{tuesday}"))
    lines = post["text"].split("\n")
    assert lines[1] == "지난 보류분" and lines[2] == "장중(11/16 숏) KOSPI 시가→종가 -0.31% → 신호 +0.31% ✅"
    assert post["text"].count("지난 보류분") == 1
    # the KODEX 200 row was final at the first report, so it is not listed again
    assert "KODEX 200" not in lines[2]
    wednesday = date(2026, 11, 18)
    assert store.report(at(19, 0, wednesday)) is False  # nothing new: no slot and no return


def test_received_is_the_last_delivered_post_before_entry(signals, clock):  # noqa: F811
    am_project(signals, MONDAY)
    turn = ask_at(signals, clock, at(8, 0))
    fallback_at(signals, clock)  # 08:28 interim post (long)
    answer_at(signals, clock, turn, at(8, 40), proposal(session_day=str(MONDAY), direction="short"))
    sid = slot_id(MONDAY, "intraday")

    def received():
        [item] = SignalStore(signals).history(at(19))
        return item["received"], item["analyst"], item["timing"], item["fallback"]

    deliver(signals, post_id("fallback"), at(8, 28))
    assert received() == ("long", "short", "late", True)  # the Analyst's post is not delivered yet
    deliver(signals, post_id("analyst"), at(9, 5))
    assert received() == ("long", "short", "late", True)  # delivered after the entry
    deliver(signals, post_id("analyst"), at(8, 41))
    assert received()[0] == "short"
    deliver(signals, post_id("analyst"), (at(8, 41),))  # no Slack ts: the dispatch start time counts
    assert received()[0] == "short"
    deliver(signals, post_id("analyst"), (at(9, 1),))
    assert received()[0] == "long"
    with signals.db.transaction() as conn:
        conn.execute("UPDATE outbox SET status='uncertain' WHERE id=%s", (post_id("analyst"),))
        conn.execute("UPDATE outbox SET status='uncertain' WHERE id=%s", (post_id("fallback"),))
    assert received()[0] is None and one(signals, "SELECT 1 x FROM trading_signals WHERE id=%s", sid)


def test_scorecard_shows_pending_per_kind_and_instrument():
    pending = slot_item("long", "5", state="pending")
    pending["returns"]["KODEX200"] = {"state": "final", "long_bp": Decimal("7")}
    text = card([pending, slot_item("long", "5")])
    lines = {line.split(" · ")[0]: line for line in text.split("\n") if line.startswith("Analyst 누적")}
    assert lines["Analyst 누적 장중 KOSPI 1전 1승(100%) 평균 +5.00bp 누적 +5.00bp"].endswith(" · 채점 보류 1")
    kodex = next(line for line in text.split("\n") if line.startswith("Analyst 누적 장중 KODEX 200"))
    assert "채점 보류" not in kodex
    assert "채점 보류" not in card([slot_item("long", "5")])
    assert summarize([pending])["intraday"]["instruments"]["KOSPI"]["pending"] == 1
    assert summarize([slot_item("long", "5")])["intraday"]["instruments"]["KOSPI"]["pending"] == 0
