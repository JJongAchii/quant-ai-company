from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ..tech_feed.schedule import delivery_time

KST = ZoneInfo("Asia/Seoul")


def utcnow():
    return datetime.now(UTC)


def discovery_slot(at=None):
    local = (at or utcnow()).astimezone(KST)
    if local.hour < 8:
        return None
    return local.strftime("%Y-%m-%d") + ("-20" if local.hour >= 20 else "-08")


def weekly_slot(at=None):
    local = (at or utcnow()).astimezone(KST)
    if local.weekday() == 0 and local.hour >= 8:
        return local.strftime("%G-W%V")
    return None


__all__ = ["delivery_time", "discovery_slot", "weekly_slot", "utcnow"]
