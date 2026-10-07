"""Calendar calculation stays in activities; Temporal workflows only use recorded results."""

import json
from datetime import UTC, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from ..company import stable
from .contracts import BriefEdition, CalendarOverride

KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")
SCHEDULE_VERSION = 5
PREPARATION_MINUTES = 65
COLLECTION_MINUTES = 20


def utcnow():
    return datetime.now(UTC)


@lru_cache(maxsize=6)
def exchange(market, year):
    import exchange_calendars

    return exchange_calendars.get_calendar("XNYS" if market == "US" else "XKRX",
                                           start=f"{year-1}-01-01", end=f"{year+1}-12-31")


def overrides(settings):
    path = settings.briefing_calendar_overrides_file
    values = [CalendarOverride.model_validate(x) for x in json.loads(path.read_text())] if path else []
    if len({(x.market, x.day) for x in values}) != len(values):
        raise ValueError("duplicate_calendar_override")
    return {(x.market, x.day): x for x in values}


def close(market, day, changes=None):
    change = (changes or {}).get((market, day))
    if change:
        return None if change.closed else change.close_at.astimezone(UTC)
    cal = exchange(market, day.year)
    return cal.session_close(str(day)).to_pydatetime() if cal.is_session(str(day)) else None


def previous(market, day, changes=None):
    for offset in range(1, 20):
        candidate = day - timedelta(days=offset)
        if close(market, candidate, changes) is not None:
            return candidate
    raise ValueError("previous_session_unavailable")


def editions(day, channel, owner, changes=None):
    if day.weekday() == 6:
        return []
    morning = datetime.combine(day, time(7, 45), KST)
    us_day = morning.astimezone(NY).date()
    us_close = close("US", us_day, changes)
    # Friday's result belongs to Saturday KST, not Monday's daily return.
    us_session = us_day if us_close and us_close.astimezone(KST).date() == day and us_close < morning else None
    kr_close = close("KR", day, changes)
    result = []
    # Corroborated session-close reports support a timely PM edition. Lake values
    # that arrive later remain dated context, never substitutes for today's close.
    lead = PREPARATION_MINUTES+COLLECTION_MINUTES
    evening = max(datetime.combine(day, time(17, 45), KST), kr_close+timedelta(minutes=lead)) if kr_close else None
    for kind, due in (("am", morning), ("pm", evening)):
        if due is None or (kind == "am" and not (us_session or kr_close or day.weekday() == 0)):
            continue
        result.append(BriefEdition(
            id=stable(f"brief:{channel}:{owner}:{day}:{kind}"), day=day, kind=kind,
            due_at=due, starts_at=due-timedelta(minutes=lead), cutoff=due-timedelta(minutes=PREPARATION_MINUTES),
            expires_at=due+timedelta(hours=1), us_session=us_session if kind == "am" else None,
            kr_session=day if kr_close else None,
            previous_us_session=previous("US", us_session, changes) if us_session and kind == "am" else None,
            previous_kr_session=previous("KR", day, changes) if kr_close or kind == "am" else None,
            weekly=("outlook" if day.weekday() == 0 else "review" if day.weekday() == 5 else None)
            if kind == "am" else None))
    return result


def scheduled(settings, at=None):
    local = (at or utcnow()).astimezone(KST)
    changes = overrides(settings)
    return [edition for offset in (-1, 0, 1)
            for edition in editions(local.date()+timedelta(days=offset), settings.briefing_channel_id,
                                    settings.briefing_owner_user, changes)]
