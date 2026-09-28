from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def utcnow():
    return datetime.now(UTC)


def delivery_time(at):
    local = at.astimezone(KST)
    if local.hour < 8:
        return local.replace(hour=8, minute=0, second=0, microsecond=0)
    if local.hour >= 21:
        return (local + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    return at


def reminder(notice, at):
    local = at.astimezone(KST)
    if not 9 <= local.hour < 15:
        return []
    today = local.date()
    result = []
    for period in notice.windows:
        for label, day in (("접수 시작", period.start), ("접수 마감", period.end)):
            diff = (day - today).days
            if diff in (0, 1):
                result.append(f"{'오늘' if diff == 0 else '내일'} {period.label} {label}")
    return result
