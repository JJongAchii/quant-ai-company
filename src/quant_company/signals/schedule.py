"""Slot times for the daily Analyst signals, derived from the XKRX session and calendar overrides."""

from datetime import date, datetime, timedelta

from ..briefing.schedule import KST, close, exchange
from .contracts import Slot


def kst_day(at: datetime) -> date:
    return at.astimezone(KST).date()


def _next_open(day: date, changes):
    calendar = exchange("KR", day.year)
    following = day
    while True:
        following = calendar.next_session(str(following)).date()
        override = (changes or {}).get(("KR", following))
        if not (override and override.closed):
            return following, calendar.session_open(str(following)).to_pydatetime()


def slots_for(day: date, changes: dict | None = None) -> list[Slot]:
    """[intraday, overnight] for a trading day, or [] when it is not a session or an override closes it."""
    calendar = exchange("KR", day.year)
    end = close("KR", day, changes)
    if end is None or not calendar.is_session(str(day)):
        return []
    start = calendar.session_open(str(day)).to_pydatetime()
    minutes = lambda base, value: base + timedelta(minutes=value)  # noqa: E731
    exit_day, exit_at = _next_open(day, changes)
    return [
        Slot(day=day, kind="intraday", snapshot_at=minutes(start, -90), snapshot_latest=minutes(start, -60),
             tools_until=minutes(start, -45), fallback_at=minutes(start, -32), send_at=minutes(start, -30),
             entry_at=start, exit_at=end, exit_day=day),
        Slot(day=day, kind="overnight", snapshot_at=minutes(end, -60), snapshot_latest=minutes(end, -60),
             tools_until=minutes(end, -35), fallback_at=minutes(end, -22), send_at=minutes(end, -20),
             entry_at=end, exit_at=exit_at, exit_day=exit_day),
    ]
