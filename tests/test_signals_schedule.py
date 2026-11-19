from datetime import UTC, date, datetime

from quant_company.briefing.contracts import CalendarOverride
from quant_company.briefing.schedule import KST
from quant_company.signals.schedule import kst_day, slots_for


def hm(value):
    return value.astimezone(KST).strftime("%H:%M")


def test_normal_friday():
    intraday, overnight = slots_for(date(2026, 11, 20))
    assert (intraday.kind, overnight.kind) == ("intraday", "overnight")
    assert [hm(getattr(intraday, f)) for f in (
        "snapshot_at", "snapshot_latest", "tools_until", "fallback_at", "send_at", "entry_at", "exit_at")] == [
        "07:30", "08:00", "08:15", "08:28", "08:30", "09:00", "15:30"]
    assert intraday.exit_day == date(2026, 11, 20)
    assert [hm(getattr(overnight, f)) for f in (
        "snapshot_at", "snapshot_latest", "tools_until", "fallback_at", "send_at", "entry_at")] == [
        "14:30", "14:30", "14:55", "15:08", "15:10", "15:30"]
    assert overnight.exit_day == date(2026, 11, 23)
    assert overnight.exit_at.astimezone(KST) == datetime(2026, 11, 23, 9, 0, tzinfo=KST)
    assert overnight.exit_at.tzinfo is not None and overnight.exit_at.utcoffset().total_seconds() == 0


def test_csat_override_moves_overnight():
    day = date(2026, 11, 19)
    change = CalendarOverride(market="KR", day=day, close_at="2026-11-19T16:30:00+09:00",
                              source_url="https://global.krx.co.kr/")
    intraday, overnight = slots_for(day, {("KR", day): change})
    assert hm(intraday.send_at) == "08:30"
    assert (hm(overnight.snapshot_at), hm(overnight.send_at), hm(overnight.entry_at)) == ("15:30", "16:10", "16:30")


def test_first_session_of_year():
    intraday, _ = slots_for(date(2027, 1, 4))
    assert (hm(intraday.snapshot_at), hm(intraday.send_at), hm(intraday.entry_at)) == ("08:30", "09:30", "10:00")


def test_year_end_overnight_exit():
    overnight = slots_for(date(2026, 12, 30))[1]
    assert overnight.exit_day == date(2027, 1, 4)
    assert hm(overnight.exit_at) == "10:00"


def test_weekend_and_override_closure():
    assert slots_for(date(2026, 11, 21)) == []
    url = "https://global.krx.co.kr/"
    closed = {("KR", date(2026, 11, 20)): CalendarOverride(market="KR", day=date(2026, 11, 20), closed=True,
                                                           source_url=url)}
    assert slots_for(date(2026, 11, 20), closed) == []
    next_closed = {("KR", date(2026, 11, 23)): CalendarOverride(market="KR", day=date(2026, 11, 23), closed=True,
                                                                source_url=url)}
    assert slots_for(date(2026, 11, 20), next_closed)[1].exit_day == date(2026, 11, 24)


def test_kst_day_boundary():
    assert kst_day(datetime(2026, 11, 19, 15, 30, tzinfo=UTC)) == date(2026, 11, 20)
