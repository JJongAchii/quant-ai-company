from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def utcnow():
    return datetime.now(UTC)


def delivery_time(at):
    local = at.astimezone(ZoneInfo("Asia/Seoul"))
    return local.replace(hour=6, minute=0, second=0, microsecond=0) if local.hour < 6 else at
